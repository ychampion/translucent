"""Seed data/recordings/sam/ from real SAM 3.1 outputs we already paid for, so campaigns and evals replay them.

Sources:
- an early probe: data/frames/sam/r1_point_fwd60.txt (Movie 1, point at frame 60, forward), keyed by the ledger's remux.
- ledgers: every ok segment_images run (per captured image), re-keyed by the exact JPEG the tool sends.
Usage: uv run python scripts/seed_recordings.py [ledger_dir ...]
"""
import base64
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import cv2

from translucent import recordings
from translucent.budget import ROOT
from translucent.ledger import Ledger
from translucent.tools import _frame, _video_meta, import_video

SAM = ROOT / "data" / "frames" / "sam"


def seed_movie1() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        L = Ledger(Path(tmp))
        art = import_video(L, ROOT / "data" / "raw" / "reisecker2023" / "movie1.mp4", label="Movie 1",
                           citation="Reisecker et al., Nat. Commun. 2023", licence="CC BY 4.0",
                           experiment="reisecker2023/movie1")
        sha, source = recordings._sha_file(L.blob_path(art.id)), recordings.stream_id(L.blob_path(art.id))
    resp = json.loads((SAM / "r1_point_fwd60.response.json").read_text(encoding="utf-8"))
    recordings.save("video", sha, "<60f>1<|point;x=1030;y=550;w=1920;h=1080|>",
                    {"propagation_direction": "forward", "include_confidence": "true"},
                    (SAM / "r1_point_fwd60.txt").read_text(encoding="utf-8"), resp.get("usage") or {}, source)
    return 1


def seed_ledger(root: Path) -> int:
    L, n = Ledger(root), 0
    for run in L.list("run"):
        if run.tool != "segment_images" or run.status != "ok":
            continue
        path = L.blob_path(run.params["video_id"])
        W, H = (m := _video_meta(path))["width"], m["height"]
        if run.params.get("boxes"):
            prompt = "<0f>" + ",".join(f"{i + 1}<|box;x1={int(a)};y1={int(b)};x2={int(c)};y2={int(d)};w={W};h={H}|>"
                                       for i, (a, b, c, d) in enumerate(run.params["boxes"]))
        else:
            prompt = "<0f>" + ",".join(f"{i + 1}<|point;x={int(x)};y={int(y)};w={W};h={H}|>"
                                       for i, (x, y) in enumerate(run.params["points"]))
        masks = next(a for i in run.output_ids if (a := L.get("artifact", i)) and a.kind == "masks")
        for f, text in json.loads(L.blob_path(masks.id).read_text(encoding="utf-8")).items():
            ok, jpg = cv2.imencode(".jpg", _frame(path, int(f)), [cv2.IMWRITE_JPEG_QUALITY, 95])
            recordings.save("image", hashlib.sha256(jpg.tobytes()).hexdigest(), prompt,
                            {"include_confidence": "true"}, text, {"seeded_from": f"{root.name}/{run.id}"},
                            source=f"{recordings.stream_id(path)}#{int(f)}")
            n += 1
    return n


if __name__ == "__main__":
    print("movie1 video:", seed_movie1())
    for d in sys.argv[1:] or ["runs/dev-m4"]:
        print(d, "images:", seed_ledger(ROOT / d))
    print(len(list(recordings.STORE.glob("*.json"))), "recordings in", recordings.STORE)
