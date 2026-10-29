"""Build web/public/fixtures/m1/ from real Day-1 outputs (no API calls): movie 1 + SAM 3.1 track (r1_point_fwd60).

Events are NOT included: the design fixture's event narrative is written by the UI work and replaced by real
ledger replays once the runtime produces them.
"""
import json
import shutil
from pathlib import Path

from meta_sam_parser import CompletedOutcome, decode_mask_to_svg_path, video_segmentation_format

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "public" / "fixtures" / "m1"
OUT.mkdir(parents=True, exist_ok=True)

text = (ROOT / "data/frames/sam/r1_point_fwd60.txt").read_text(encoding="utf-8")
parser = video_segmentation_format().create_parser()
list(parser.push(text))
result = parser.finish(CompletedOutcome()).result
compare = {r["frame"]: r for r in json.loads((ROOT / "data/frames/sam/r1_point_fwd60.compare.json").read_text())["rows"]}

frames, paths, origins, points = [], [], [], []
for rec in result.records:
    if rec.kind != "mask":
        continue
    f = rec.frame.frame_index
    frames.append(f)
    paths.append(decode_mask_to_svg_path(rec.mask))
    origins.append([rec.bounds.left, rec.bounds.top])
    points.append(compare[f]["sam_tip"] if f in compare else None)

shutil.copy(ROOT / "data/frames/sam/reisecker2023_movie1_faststart.mp4", OUT / "movie.mp4")
probe = json.loads((ROOT / "data/frames/probe/probe.json").read_text())["movie1"]["rows"]
bundle = {
    "contract_version": "1",
    "note": "Design fixture: real video and real SAM 3.1 masks; campaign/events are to be authored for design only.",
    "video": "movie.mp4", "video_size": [1920, 1080], "fps": 25.0,
    "source": {"citation": "Reisecker et al., Nat. Commun. 14, 2432 (2023), Supplementary Movie 1", "licence": "CC BY 4.0"},
    "calibration_px_per_mm": 184.9,
    "tracks": [{"subject": "dendrite 1", "frames": frames, "mask_paths": paths, "mask_origins": origins, "points": points}],
    "classical_tip": [{"frame": r["frame"], "point": [r["tip_x"], r["tip_y"]]} for r in probe if r["tip_x"] is not None],
}
(OUT / "bundle.json").write_text(json.dumps(bundle))
print(len(frames), "frames;", sum(len(p) for p in paths) // 1000, "k chars of SVG;", (OUT / "bundle.json").stat().st_size // 1000, "kB")
print("sample path:", paths[0][:160])
