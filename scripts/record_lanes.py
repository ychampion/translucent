"""Record SAM 3.1 lane-box outlines for every captured Movie 4 image (paid: ~49 images x $0.0025 = ~$0.12).

Paid. Every response is stored in data/recordings/sam/
so evals and replays use real clean filament masks afterwards for free.
  uv run python scripts/record_lanes.py --ledger runs/lanes-m4
"""
import argparse
import json
import os
from pathlib import Path

os.environ["TRANSLUCENT_SAM"] = "record"

from translucent import tools  # noqa: E402
from translucent.ledger import Ledger  # noqa: E402
from translucent.runtime import new_campaign  # noqa: E402

# one loose box per filament lane, right electrode (x~615) to left (x~210); checked on frame 150 (conf 0.42-0.84)
LANES = [[210, 236, 615, 262], [210, 304, 615, 330], [210, 360, 615, 386], [210, 454, 615, 480], [210, 522, 615, 550]]

ap = argparse.ArgumentParser()
ap.add_argument("--ledger", default="runs/lanes-m4")
a = ap.parse_args()

L = Ledger(Path(a.ledger))
v = tools.import_video(L, Path("data/raw/reisecker2023/movie4.mp4"), label="Movie 4", licence="CC BY 4.0",
                       experiment="reisecker2023/movie4",
                       citation="Reisecker et al., Nat. Commun. 14, 2432 (2023), Supplementary Movie 4")
c = new_campaign(L, "Record lane outlines for Movie 4", "Movie 4 lanes", [v.id], budget_usd=0.25)
ctx = tools.Context(L, c.id)
seg = tools.call(ctx, "segment_images", {"video_id": v.id, "boxes": LANES})
print(seg.status, seg.summary[:600])
if seg.status == "ok":
    scale = tools.call(ctx, "lookup_calibration", {"video_id": v.id, "quantity": "spatial_scale"})
    clock = tools.call(ctx, "lookup_calibration", {"video_id": v.id, "quantity": "time_base"})
    grow = tools.call(ctx, "measure_growth", {"boxes_id": seg.data["boxes_id"], "video_id": v.id, "grows_from": "right",
                                              "scale_id": scale.data["measurement_id"],
                                              "time_base_id": clock.data["measurement_id"]})
    print(grow.status, grow.summary)
print(json.dumps(ctx.meta.ledger.balance(), indent=1))
