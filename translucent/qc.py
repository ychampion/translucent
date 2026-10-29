"""Outline quality checks: is one SAM outline really one filament?

Two filaments merged into one outline give a box that spans both, so the "front" is whichever tip is ahead and the
speed is not either filament's. With a mask we can look inside the outline: two filaments leave the electrode as two
separate bodies side by side, so the mask's cross-sections near the root have two runs with a gap between them
(a side branch joins its trunk away from the root, so it does not look like this). Without a mask we only have the
box. Box-only screening cannot establish the identity of a single filament for measurement.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from meta_sam_parser import CompletedOutcome, decode_mask_to_raster, video_segmentation_format

WIDE_FRACTION = 0.04   # box-only review limit as a share of the field across growth
REFERENCE_UM_PER_PX = 1000 / 161.2
MIN_BODY_UM = MIN_GAP_UM = 2 * REFERENCE_UM_PER_PX   # smallest split checked on the Reisecker outlines


def _runs(col: np.ndarray) -> list[tuple[int, int]]:
    d = np.diff(np.concatenate(([0], col.astype(np.int8), [0])))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def split_columns(mask: np.ndarray, axis: int, grows_from: str, um_per_px=REFERENCE_UM_PER_PX) -> np.ndarray:
    """Per position along the growth axis (root first): does the cross-section hold two separate bodies?"""
    m = np.asarray(mask, bool)
    if axis == 1:
        m = m.T                      # columns = positions along growth, rows = across
    if grows_from in ("right", "bottom"):
        m = m[:, ::-1]               # root first
    cols = np.flatnonzero(m.any(0))
    if not len(cols):
        return np.zeros(0, bool)
    m = m[:, cols[0]:cols[-1] + 1]
    out = np.zeros(m.shape[1], bool)
    min_run, min_gap = max(2, int(np.ceil(MIN_BODY_UM / um_per_px))), max(2, int(np.ceil(MIN_GAP_UM / um_per_px)))
    for j in range(m.shape[1]):
        runs = [r for r in _runs(m[:, j]) if r[1] - r[0] >= min_run]
        out[j] = any(b[0] - a[1] >= min_gap for a, b in zip(runs, runs[1:]))
    return out


def two_bodies(mask: np.ndarray, axis: int, grows_from: str, um_per_px=REFERENCE_UM_PER_PX) -> bool:
    """Two separate bodies side by side from the root: near the root most cross-sections are split, and the split
    runs at least a fifth of the outline's length."""
    s = split_columns(mask, axis, grows_from, um_per_px)
    if len(s) < 4:
        return False
    root = s[:max(3, len(s) // 10)]
    m = np.asarray(mask, bool)
    if axis == 1:
        m = m.T
    if grows_from in ("right", "bottom"):
        m = m[:, ::-1]
    cols = np.flatnonzero(m.any(0))
    rows = np.flatnonzero(m.any(1))
    if rows[-1] - rows[0] >= cols[-1] - cols[0]:
        return False
    across = m[:, cols[0]:cols[-1] + 1].sum(0)
    ridge_width = float(np.percentile(across[across > 0], 75)) / 2 if s.any() else 0.0
    return bool(root.mean() >= 0.6 and s.sum() >= max(4, 0.2 * len(s), 4 * ridge_width))


def _raster(mask) -> bytes:
    if mask.encoding == "one_bit":
        from meta_sam_parser._mask_codec import _decode_raster  # skips the re-encode check: 2x faster
        try:
            return _decode_raster(mask.payload, int(mask.width), int(mask.height))
        except Exception:
            pass
    return decode_mask_to_raster(mask)


@lru_cache(maxsize=128)
def _records(line: str):
    parser = video_segmentation_format().create_parser()
    list(parser.push(line if line.endswith("\n") else line + "\n"))
    result = parser.finish(CompletedOutcome()).result
    if result.diagnostics:
        raise ValueError(f"Could not read the outline: {result.diagnostics}")
    return tuple(r for r in result.records if r.kind == "mask")


@lru_cache(maxsize=512)
def _mask_split(mask, axis, grows_from, um_per_px=REFERENCE_UM_PER_PX):
    m = np.frombuffer(_raster(mask), dtype=np.uint8).reshape(mask.height, mask.width)
    if not m.any():
        raise ValueError("outline has no visible body")
    return two_bodies(m, axis, grows_from, um_per_px)


def masks_in(line: str) -> dict[str, np.ndarray]:
    """Object id -> mask raster (box-local) for one line of SAM output text."""
    out = {}
    for r in _records(line):
        w, h = r.mask.width, r.mask.height
        if w * h:
            out[str(r.object_id)] = np.frombuffer(_raster(r.mask), dtype=np.uint8).reshape(h, w)
    return out


def has_mask(line: str, obj: str) -> bool:
    return bool(line) and any(str(r.object_id) == obj for r in _records(line))


def frame_lines(raw: dict) -> dict[int, str]:
    """Frame index -> SAM text line, from the stored masks artifact ({"video": text} or {frame: text})."""
    out = {}
    for k, text in raw.items():
        for ln in text.splitlines():
            if "<|mask" not in ln:
                continue
            if k == "video":
                if ln.startswith("<") and "f>" in ln[:12]:
                    out[int(ln[1:ln.index("f>")])] = ln
            else:
                out[int(k)] = ln
    return out


def merged_track(lines: dict[int, str], obj: str, frames: list[int], axis: int, grows_from: str,
                 um_per_px=REFERENCE_UM_PER_PX):
    """Check both ends of every target outline; one merged image makes identity across the track ambiguous.

    Only compressed records and decisions are cached, never image arrays. Repeated frames cannot conceal a brief
    merge between sparsely sampled images or turn one suspect outline into a majority vote.
    """
    seen = hits = 0
    ends = ("left", "right") if axis == 0 else ("top", "bottom")
    for f in frames:
        if f not in lines:
            continue
        for r in _records(lines[f]):
            if str(r.object_id) == obj:
                seen += 1
                hits += any(_mask_split(r.mask, axis, end, um_per_px) for end in ends)
    if hits:
        return (f"its outline holds two separate bodies side by side from the electrode in {hits} of {seen} images "
                "checked (two filaments merged into one outline)"), seen
    return None, seen


def too_wide(box, axis: int, um_per_px: float, frame_size=None) -> str | None:
    """Request mask pixels for broad box-only tracks; width alone does not establish a merge."""
    x1, y1, x2, y2 = box[:4]
    along, across = (x2 - x1, y2 - y1) if axis == 0 else (y2 - y1, x2 - x1)
    limit = WIDE_FRACTION * frame_size[1 - axis] if frame_size else 0.2 * along
    across_um = across * um_per_px
    if across > limit:
        return (f"{across_um:.0f} um across and there is no mask to show it is one body "
                "(may be two filaments merged)")
    return None


def audit_recordings(store, output=None) -> dict:
    """Check every stored outline, including faint and already misshapen objects; no network or ledger writes."""
    import json
    from pathlib import Path
    from translucent import instruments
    paths = sorted(Path(store).glob("*.json"))
    files, rows = 0, []
    result = {"recordings": 0, "outlines": 0, "flagged": 0, "complete": False, "rows": rows}
    for path in paths:
        rec = json.loads(path.read_text(encoding="utf-8"))
        exp = rec.get("experiment")
        if exp is None:
            seed = rec.get("usage", {}).get("seeded_from", "")
            exp = "reisecker2023/movie4" if seed.startswith("dev-m4/") else None
            if rec.get("media_sha") == instruments.RECORDED_FILES["reisecker2023/movie1"]:
                exp = "reisecker2023/movie1"
        cal = instruments.spatial_scale(exp) if exp else None
        um_px = 1000 / cal["px_per_mm"] if cal else REFERENCE_UM_PER_PX
        files += 1
        for r in _records(rec["text"]):
            flags = [g for g in ("right", "left") if _mask_split(r.mask, 0, g, um_px)]
            rows.append({"recording": path.name, "frame": r.frame.frame_index, "object": r.object_id,
                         "shape": [r.mask.height, r.mask.width], "flagged_from": flags})
        result.update(recordings=files, outlines=len(rows), flagged=sum(bool(r["flagged_from"]) for r in rows),
                      complete=files == len(paths))
        if output:
            Path(output).write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(f"[qc] {files} recordings, {len(rows)} outlines checked, {result['flagged']} flagged", flush=True)
    return result


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path
    result = audit_recordings("data/recordings/sam", sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}))
