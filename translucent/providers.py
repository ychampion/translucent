"""Metered clients for paid model providers. Nothing else in the repo may construct a client for these hosts.

- Meta Model API (api.meta.ai): Muse Wonder, Muse Spark, SAM 3.1. Prices: dev.meta.ai/docs/pricing-rate-limits
  (read 2026-10-28).
- Fireworks: Muse Spark Open, Muse Glimmer. Prices: Fireworks model pages / aggregators (read 2026-10-28).
"""
import json
import os

from dotenv import load_dotenv
from openai import APIStatusError, OpenAI

from translucent.budget import ROOT, SAFETY, BudgetExceeded, Ledger

# USD per token (input, cached input, output)
TOKEN_PRICES = {
    "meta": {
        "muse-wonder-1.0": (4.00e-6, 0.20e-6, 20.00e-6),
        "muse-spark-1.3": (1.25e-6, 0.15e-6, 4.25e-6),
    },
    "fireworks": {
        "accounts/fireworks/models/muse-spark-open": (1.25e-6, 0.15e-6, 4.25e-6),
        "accounts/fireworks/models/muse-glimmer-30b": (0.35e-6, 0.04e-6, 1.50e-6),
    },
}
BASE_URLS = {"meta": "https://api.meta.ai/v1", "fireworks": "https://api.fireworks.ai/inference/v1"}
KEY_VARS = {"meta": ("MODEL_API_KEY", "META_API_KEY"), "fireworks": ("FIREWORKS_API_KEY",)}
SAM_PER_FRAME, SAM_MIN_FRAMES = 0.20 / 1000, 50
SAM_PER_IMAGE = 2.50 / 1000
IMAGE_TOKENS_WORST = 4000  # per input image; image token counts are not published


def _token_cost(prices, usage):
    p_in, p_cached, p_out = prices
    prompt = usage.prompt_tokens or 0
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None) or 0
    out = usage.completion_tokens or 0
    reasoning = getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None) or 0
    # if a provider reports reasoning outside completion_tokens, bill the larger reading
    out = max(out, reasoning)
    return (prompt - cached) * p_in + cached * p_cached + out * p_out


def _rejected(e):
    return 400 <= e.status_code < 500 and e.status_code != 429


class Metered:
    """Chat completions against one provider, with a mandatory output ceiling and a hard cap."""

    provider = None

    def __init__(self):
        load_dotenv(ROOT / ".env")
        key = next((os.environ[v] for v in KEY_VARS[self.provider] if os.environ.get(v)), None)
        if not key:
            raise RuntimeError(f"{KEY_VARS[self.provider][0]} missing from .env")
        # no hidden retries: each reserve() covers exactly one billed request
        self._client = OpenAI(base_url=BASE_URLS[self.provider], api_key=key, max_retries=0)
        self.ledger = Ledger(self.provider)

    def chat(self, *, model, messages, max_tokens, **kw):
        prices = TOKEN_PRICES[self.provider].get(model)
        if prices is None:
            raise BudgetExceeded(f"no {self.provider} price on record for {model}; add it to TOKEN_PRICES first")
        text = json.dumps(messages) + json.dumps(kw.get("tools", []))
        images = text.count("image_url")
        # ~2 chars/token over-counts English and base64 alike; images at a fixed worst case
        in_tokens = len(text) // 2 if not images else len(text.split("base64,")[0]) // 2 + images * IMAGE_TOKENS_WORST
        worst = (in_tokens * prices[0] + max_tokens * prices[2]) * SAFETY
        rid = self.ledger.reserve(worst, f"chat {model} max_tokens={max_tokens}")
        try:
            r = self._client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens, **kw)
        except APIStatusError as e:
            if _rejected(e):
                self.ledger.settle(rid, 0.0, {"rejected": e.status_code})
            raise
        if r.usage is not None:
            self.ledger.settle(rid, _token_cost(prices, r.usage), r.usage.model_dump())
        return r  # no usage: the reservation stays


class MeteredFireworks(Metered):
    provider = "fireworks"


class MeteredMeta(Metered):
    provider = "meta"

    def upload(self, path):
        """Files API upload (no published charge)."""
        with open(path, "rb") as fh:
            return self._client.files.create(file=fh, purpose="user_data").id

    def sam_video(self, *, file_id, prompt, frames, metadata=None, on_delta=None, source=None):
        """Stream SAM 3.1 over an uploaded video of `frames` frames; returns (text, final_response). `source` is a
        replay key, not sent."""
        worst = max(frames, SAM_MIN_FRAMES) * SAM_PER_FRAME * SAFETY
        rid = self.ledger.reserve(worst, f"sam-3.1 video frames={frames}")
        text, final = self._sam(rid, {"type": "input_video", "file_id": file_id}, prompt, metadata, on_delta)
        usage = final.usage.model_dump() if getattr(final, "usage", None) else {}
        billed = usage.get("video_frames_processed")
        if billed is not None:
            self.ledger.settle(rid, max(int(billed), SAM_MIN_FRAMES) * SAM_PER_FRAME, usage)
        return text, final  # unknown billing: the reservation stays

    def sam_image(self, *, image_url, prompt, metadata=None, source=None):
        """SAM 3.1 on one image (public URL or base64 data URL); billed per image. `source` is a replay key, not sent."""
        rid = self.ledger.reserve(SAM_PER_IMAGE * SAFETY, "sam-3.1 image")
        text, final = self._sam(rid, {"type": "input_image", "image_url": image_url}, prompt, metadata, None)
        self.ledger.settle(rid, SAM_PER_IMAGE, final.usage.model_dump() if getattr(final, "usage", None) else {})
        return text, final

    def _sam(self, rid, media, prompt, metadata, on_delta):
        chunks, seen = [], []
        try:
            stream_cm = self._client.responses.stream(
                model="sam-3.1",
                input=[{"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}, media]}],
                metadata=metadata or None,
            )
            with stream_cm as stream:
                # video streams emit text deltas; the parser README says some responses carry the text only in
                # response.content_part.done, so read both
                for ev in stream:
                    seen.append(ev.type if ev.type not in ("error", "response.failed") else f"{ev.type}: {ev}")
                    if ev.type == "response.output_text.delta":
                        piece = ev.delta
                    elif ev.type == "response.content_part.done" and getattr(ev.part, "type", None) == "output_text":
                        piece = None if chunks else ev.part.text
                    else:
                        continue
                    if piece:
                        chunks.append(piece)
                        if on_delta:
                            on_delta(piece)
                final = stream.get_final_response()
        except APIStatusError as e:
            if _rejected(e) and not chunks:
                self.ledger.settle(rid, 0.0, {"rejected": e.status_code})
            raise
        except RuntimeError as e:  # stream ended without response.completed; reservation stays
            err = RuntimeError(f"{e} events={seen[-5:]}")
            err.partial_text = "".join(chunks)
            raise err from e
        return "".join(chunks) or (getattr(final, "output_text", "") or ""), final
