"""Recorded SAM 3.1 outputs. SAM is non-deterministic and paid, so every real response is recorded once and replayed.

- RecordingSAM(inner): wraps a live MeteredMeta; passes calls through and stores each response.
- RecordedSAM(mode): serves stored responses with no network.
    mode="strict": only an exact match (video/image content + prompt + settings); otherwise the call fails.
    mode="nearest": development only. With no exact match, serves the recording for the same video whose prompt
    frame is closest, and says so in the response (`substituted`), which the tool records in its run.
Store: data/recordings/sam/<key>.json  {kind, media_sha, source?, prompt, metadata, text, usage, recorded_at}
    source: the video's stream_id (images: "<stream_id>#<frame>"). Remuxed files and decoded frames differ between
    FFmpeg builds (Windows vs Linux), so when the bytes match nothing, replay falls back to the same source with the
    same prompt and settings.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from translucent.budget import ROOT

STORE = ROOT / "data" / "recordings" / "sam"


def _sha_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha_image(image_url: str) -> str:
    data = image_url.split("base64,", 1)[1] if "base64," in image_url else image_url
    return hashlib.sha256(base64.b64decode(data) if "base64," in image_url else data.encode()).hexdigest()


def ffmpeg() -> str:
    """The ffmpeg executable, or a plain setup error (a bare WinError 3 sent a campaign hunting for causes in the data)."""
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg is not installed on the machine running the tools. This is a setup problem, not "
                           "the experiment's: outlining and importing videos stay unavailable until it is installed.")
    return exe


@lru_cache(maxsize=64)
def stream_id(path) -> str:
    """Hash of the video's encoded packets: the same for the raw file and every stream-copy remux, on any FFmpeg."""
    out = subprocess.run([ffmpeg(), "-v", "error", "-i", str(path), "-map", "0:v:0", "-c", "copy", "-f", "hash",
                          "-hash", "sha256", "-"], check=True, capture_output=True, text=True).stdout.strip()
    return "stream:" + out.removeprefix("SHA256=")


def key(kind: str, media_sha: str, prompt: str, metadata: dict | None) -> str:
    return hashlib.sha256(json.dumps([kind, media_sha, prompt, metadata or {}], sort_keys=True).encode()).hexdigest()


def save(kind, media_sha, prompt, metadata, text, usage, source=None) -> Path:
    STORE.mkdir(parents=True, exist_ok=True)
    k = key(kind, media_sha, prompt, metadata)
    p = STORE / f"{k}.json"
    rec = {"kind": kind, "media_sha": media_sha, "prompt": prompt, "metadata": metadata or {},
           "text": text, "usage": usage, "recorded_at": time.time()}
    if source:
        rec["source"] = source
    p.write_text(json.dumps(rec), encoding="utf-8")
    return p


def _final(rec: dict, substituted: str | None = None):
    usage = SimpleNamespace(model_dump=lambda: dict(rec.get("usage") or {}, replayed=True))
    return SimpleNamespace(id=f"recorded:{rec.get('key', '')}", usage=usage, output_text=rec["text"],
                           substituted=substituted)


class RecordingSAM:
    """Live SAM through the metered client, with every response stored for replay."""

    def __init__(self, inner):
        self.inner, self.ledger, self._sha_of = inner, inner.ledger, {}

    def upload(self, path):
        fid = self.inner.upload(path)
        self.remember(fid, path)
        return fid

    def remember(self, file_id, path):
        """Map an already-uploaded Meta file id to its content hash (uploads are cached per ledger)."""
        self._sha_of[file_id] = _sha_file(path)

    def sam_video(self, *, file_id, prompt, frames, metadata=None, on_delta=None, source=None):
        text, final = self.inner.sam_video(file_id=file_id, prompt=prompt, frames=frames, metadata=metadata,
                                           on_delta=on_delta)
        usage = final.usage.model_dump() if getattr(final, "usage", None) else {}
        save("video", self._sha_of[file_id], prompt, metadata, text, usage, source)
        return text, final

    def sam_image(self, *, image_url, prompt, metadata=None, source=None):
        text, final = self.inner.sam_image(image_url=image_url, prompt=prompt, metadata=metadata)
        usage = final.usage.model_dump() if getattr(final, "usage", None) else {}
        save("image", _sha_image(image_url), prompt, metadata, text, usage, source)
        return text, final


class NoRecording(RuntimeError):
    pass


class RecordedSAM:
    """Replay stored SAM responses; never touches the network. Has a free ledger-shaped balance for the tools."""

    def __init__(self, mode: str = "strict"):
        assert mode in ("strict", "nearest")
        self.mode = mode
        self.ledger = SimpleNamespace(balance=lambda: {"committed_usd": 0.0})
        self._by_source = None

    def upload(self, path):
        return "rec:" + _sha_file(path)

    def _load(self, kind, media_sha, prompt, metadata, source=None):
        p = STORE / f"{key(kind, media_sha, prompt, metadata)}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")), None
        if source:
            if self._by_source is None:
                recs = [json.loads(q.read_text(encoding="utf-8")) for q in STORE.glob("*.json")]
                self._by_source = {key(r["kind"], r["source"], r["prompt"], r["metadata"]): r
                                   for r in recs if r.get("source")}
            if rec := self._by_source.get(key(kind, source, prompt, metadata)):
                return rec, None
        if self.mode == "strict":
            raise NoRecording(f"no recorded SAM {kind} output for this prompt ({prompt[:80]}). This replay has no live "
                              "SAM, so any new prompt fails the same way; only prompts recorded earlier replay")
        same = [json.loads(q.read_text(encoding="utf-8")) for q in STORE.glob("*.json")]
        same = [r for r in same
                if r["kind"] == kind and (r["media_sha"] == media_sha or source and r.get("source") == source)]
        if not same:
            raise NoRecording(f"no recorded SAM {kind} output for this {'video' if kind == 'video' else 'image'}")
        want = int(m.group(1)) if (m := re.match(r"<(\d+)f>", prompt)) else 0
        best = min(same, key=lambda r: abs(int(re.match(r"<(\d+)f>", r["prompt"]).group(1)) - want)
                   if re.match(r"<(\d+)f>", r["prompt"]) else 1e9)
        return best, best["prompt"]

    def sam_video(self, *, file_id, prompt, frames, metadata=None, on_delta=None, source=None):
        rec, sub = self._load("video", file_id.removeprefix("rec:"), prompt, metadata, source)
        return rec["text"], _final(rec, sub)

    def sam_image(self, *, image_url, prompt, metadata=None, source=None):
        rec, sub = self._load("image", _sha_image(image_url), prompt, metadata, source)
        return rec["text"], _final(rec, sub)
