"""Print the exact Lu image requests to record later. Never constructs a SAM client."""
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translucent import instruments, tools


def main():
    path = ROOT / "data/raw/lu2023/movie1.mp4"
    with path.open("rb") as fh:
        instruments.check_recording(instruments.LU, hashlib.file_digest(fh, "sha256").hexdigest())
    times = instruments.frame_times(instruments.LU)
    frames = tools._capture_frames(path, times)
    indices = np.linspace(0, len(frames) - 1, 12).round().astype(int)
    frames = [frames[i] for i in sorted(set(indices))]
    boxes = [[0, 120, 120, 420], [0, 420, 120, 760]]
    prompt = "<0f>" + ",".join(f"{i + 1}<|box;x1={a};y1={b};x2={c};y2={d};w=1280;h=938|>"
                                for i, (a, b, c, d) in enumerate(boxes))
    images = []
    for frame in frames:
        ok, jpg = cv2.imencode(".jpg", tools._frame(path, frame), [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok:
            raise ValueError(f"could not encode frame {frame}")
        images.append({"frame": frame, "t_s": times[frame], "jpeg_sha256": hashlib.sha256(jpg.tobytes()).hexdigest()})
    plan = {"experiment": instruments.LU, "tool": "segment_images", "args": {"boxes": boxes, "max_images": 12},
            "prompt": prompt, "metadata": {"include_confidence": "true"}, "jpeg_quality": 95,
            "images": images, "sam_image_calls": len(images), "sam_video_calls": 0,
            "purpose": "Compare the left electrode edge before and after charging; candidate plated-lithium regions. "
                       "These boxes need visual review against graphite and current collector; no visual finding is earned yet."}
    out = ROOT / "data/groundtruth/lu2023/lu2023_sam_plan.json"
    out.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
