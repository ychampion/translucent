"""Campaign runtime: the closed loop. Orchestrator plans and chooses tests; worker reads images; gate decides findings.

Profiles
- "dev": every role on Fireworks Muse Spark Open (build and internal tests; Fireworks cap).
- "meta": orchestrator Muse Wonder, worker Muse Spark (Meta Model API); critic Muse Spark Open on Fireworks.
SAM 3.1 is always the Meta Model API. Every paid call is metered against the provider caps (translucent.budget).
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from translucent import analysis, instruments, tools  # noqa: F401 - analysis registers run_analysis_code
from translucent.budget import BudgetExceeded
from translucent.contracts import Campaign
from translucent.ledger import Ledger

log = logging.getLogger(__name__)

SPARK_OPEN = "accounts/fireworks/models/muse-spark-open"
PROFILES = {
    "dev": {"orchestrator": ("fireworks", SPARK_OPEN), "worker": ("fireworks", SPARK_OPEN),
            "critic": ("fireworks", SPARK_OPEN)},
    "meta": {"orchestrator": ("meta", "muse-wonder-1.0"), "worker": ("meta", "muse-spark-1.3"),
             "critic": ("fireworks", SPARK_OPEN)},
}
# Orchestrators not offered run_analysis_code: Spark Open writes its code argument as chat-template text and runs on
# to the token limit (every such attempt in the 2026-10-28 campaigns; none succeeded)
NO_CODE_TOOL = {SPARK_OPEN}

ORCHESTRATOR = """You run a research campaign on recorded lab experiments (operando battery microscopy video, with the \
lab's own records). Objective: {objective}

How you work
- Every number comes from a tool. Never state a number you did not get from a tool result.
- Findings answer the objective (growth, shorting, recovery...), never restate a calibration or a tool output.
- Findings count only when accepted by propose_finding, which checks them with trusted code. If a finding is refused \
or not proven, read why, run the test that would settle it, and propose again (use supersedes). Never argue with a check.
- To point at things in the video, ask the worker (locate_objects); it looks at a gridded frame and returns pixel points.
- Prefer the cheapest test that can change your mind. Recorded clocks and scale bars beat assumptions.
- When you learn a procedure or pitfall that future campaigns should know, record_lesson with its evidence ids. \
Lessons are about the experiment or the measurement method, never about this software, recordings or tool availability.
- Start each turn with one short plain sentence saying what you are doing next (no jargon; a non-specialist reads it).
- When the objective is answered as well as the evidence allows, call finish. Honest "not proven" is a valid answer.
- When a result disagrees with a published statement, say under which assumption the two can't be reconciled; never say the paper is wrong or that the data contradicts it. Claim only what was measured: a voltage change is not evidence of what a filament did.
- People on the team may send you notes or challenge a finding; check what they say against the records. When only a \
person can settle something (a scale bar length, which cell, what the lab meant), ask_person; never ask permission for routine steps.

Experiments: {experiments}
{memory}"""

WORKER = """You look at one gridded microscope frame (gridlines labelled in source-video pixels) and point at objects. \
Task: {task}
Reply with JSON only: {{"points": [[x, y], ...], "note": "one short sentence"}}. Points must lie on the objects, \
in source-video pixel coordinates read from the grid labels. If you cannot see them, return {{"points": [], "note": "why"}}."""

CRITIC = """You review a finding against an evidence image. Finding: {statement}
Answer on the first line SUPPORTED, REFUTED or INCONCLUSIVE, then one sentence of evidence from the image."""

EXTRA_TOOLS = [
    {"type": "function", "function": {
        "name": "locate_objects",
        "description": "Ask the worker to look at a gridded frame and return pixel points on the objects you describe.",
        "parameters": {"type": "object", "required": ["video_id", "frame", "task"], "properties": {
            "video_id": {"type": "string"}, "frame": {"type": "integer"},
            "region": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
            "task": {"type": "string", "description": "What to point at, e.g. 'the root of each dark filament at the right electrode'"}}}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": "End the campaign with the answer to the objective.",
        "parameters": {"type": "object", "required": ["answer", "not_proven"], "properties": {
            "answer": {"type": "string", "description": "2-4 plain sentences, citing finding ids"},
            "not_proven": {"type": "string", "description": "What the evidence could not settle, plainly"}}}}},
    {"type": "function", "function": {
        "name": "ask_person",
        "description": "Ask a person on the team something only a person can settle (a scale bar length, which cell, "
                       "what the lab meant). Waits a while for an answer; if nobody answers, carry on with the records.",
        "parameters": {"type": "object", "required": ["question"], "properties": {
            "question": {"type": "string", "description": "One plain question"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "Optional answers to pick from"}}}}},
]
NOBODY = ("Nobody is available to answer right now. Carry on with what the records show, and say plainly what a "
          "person would still need to confirm.")


class ProviderFailed(RuntimeError):
    """A model call kept failing with transient errors after every retry, or the service refused it outright."""

    def __init__(self, error: str, refused: bool = False):
        super().__init__(error)
        self.refused = refused


def _transient(e: Exception) -> bool:
    """Timeouts, dropped connections, rate limits and server errors are worth another try; nothing else is."""
    import openai
    if isinstance(e, (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError)):
        return True
    return isinstance(e, openai.APIStatusError) and (e.status_code == 429 or e.status_code >= 500)


def _refused(e: Exception) -> bool:
    """A 4xx from the model service (bad request, auth, not found): retrying the same request cannot help."""
    import openai
    return isinstance(e, openai.APIStatusError) and 400 <= e.status_code < 500


def campaign_state(ledger: Ledger, campaign_id: str) -> tuple[str, str | None]:
    """From the event log: 'new', 'running', 'paused' or 'ended', and the last campaign.finished status (or None)."""
    state, last = "new", None
    for ev in ledger.events(campaign_id):
        if ev.type in ("campaign.started", "campaign.resumed"):
            state = "running"
        elif ev.type == "campaign.paused":
            state = "paused"
        elif ev.type == "campaign.finished":
            state, last = "ended", ev.data.get("status", "finished")
    return state, last


def explain_check(check):
    detail = check["detail"].lower()
    name = check["name"].lower()
    if "evidence is sound" in name or "ancestry" in name:
        return "The evidence depends on a tool result that failed or was later withdrawn."
    if "scale and timing" in name:
        if "another recording" in detail or "different recording" in detail or "different experiment" in detail:
            return "These measurements do not come from the same recorded experiment."
        if "time_base" in detail or "playback" in detail:
            return ("The time axis came from playback speed, not the recorded clock." if "playback" in detail else
                    "This finding needs timing from the experiment's recorded clock.")
        if "spatial_scale" in detail or "image scale" in detail:
            return "The recorded image scale does not reliably support this measurement."
        return "The recorded scale or clock cannot support the physical units in this finding."
    if "same experiment" in name:
        return "These measurements do not come from the same recorded experiment."
    if "recording" in name:
        return "This measurement cannot be traced back to the recorded experiment."
    if "corroborat" in name:
        return "The finding needs an independent check against the recorded voltage."
    if "unit" in name:
        return "The measurements use units that cannot be compared in this check."
    if "specific" in name:
        return "The allowed range is too broad to settle this finding."
    if "well-formed" in name:
        return "The finding does not state a complete comparison that can be checked."
    if "uncertainty" in name:
        return "The measurements need a stated margin of uncertainty before they can be compared."
    if "enough points" in name or "series" in name:
        return "The recorded values and times do not provide enough aligned observations."
    return "The recorded measurements did not pass this comparison: " + check["detail"].rstrip(".") + "."


def _value(m) -> str:
    if isinstance(m.value, list):
        return (f"{len(m.value)} values from {min(m.value):.4g} to {max(m.value):.4g} {m.unit}" if m.value
                else f"no values ({m.unit})")
    u = m.uncertainty.value
    return f"{m.value:.6g} {m.unit}" + (f" ± {u:.3g}" if u is not None else "")


def _client(provider):
    from translucent.providers import MeteredFireworks, MeteredMeta
    return {"meta": MeteredMeta, "fireworks": MeteredFireworks}[provider]()


TEMPLATE = re.compile(r"<\|[^|>]*\|>|</?[a-z]+:(?:function_calls|invoke|parameter)")  # chat-template tokens a model can leak into its text


def _voltage_plot(series: dict, marks: list[float], out: Path) -> Path:
    """Cell voltage vs recorded time with vertical marks at the finding's times (for the critic to read)."""
    import cv2
    import numpy as np
    W, H, pad = 1000, 420, 60
    t, v = np.asarray(series["t"], float), np.asarray(series["v"], float)
    # a multi-segment log is drawn only over the segments the finding is about, so a small dip stays visible
    segs = [s for s in series.get("segments", []) if any(s["t_start_s"] <= m <= s["t_end_s"] for m in marks)]
    if segs:
        keep = (t >= min(s["t_start_s"] for s in segs)) & (t <= max(s["t_end_s"] for s in segs))
        t, v = t[keep], v[keep]
    t0, t1 = float(t.min()), float(t.max())
    v0, v1 = float(min(v.min(), 0)), float(max(v.max(), 0))
    def xy(tt, vv):
        return (int(pad + (tt - t0) / (t1 - t0 or 1) * (W - 2 * pad)),
                int(H - pad - (vv - v0) / (v1 - v0 or 1) * (H - 2 * pad)))
    img = np.full((H, W, 3), 255, np.uint8)
    cv2.line(img, xy(t0, 0), xy(t1, 0), (200, 200, 200), 1)
    cv2.putText(img, "0 V", (W - pad + 5, xy(t1, 0)[1] + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (90, 90, 90), 1)
    step, unit = (3600, "h") if t1 - t0 > 2 * 3600 else (60, "s")  # a day-long log is read in hours
    every = max(1, int((t1 - t0) / step / 12))
    for k in range(int(t0 // step) + 1, int(t1 // step) + 1, every):
        x = xy(k * step, v0)[0]
        cv2.putText(img, f"{k if unit == 'h' else k * step} {unit}", (x - 18, H - pad + 25), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (90, 90, 90), 1)
    for vv in (v0, v1):
        cv2.putText(img, f"{vv:.1f} V", (5, xy(t0, vv)[1] + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (90, 90, 90), 1)
    cv2.polylines(img, [np.array([xy(a, b) for a, b in zip(t, v)], np.int32)], False, (20, 20, 20), 2)
    for m in marks:
        x = xy(m, v0)[0]
        cv2.line(img, (x, pad // 2), (x, H - pad), (40, 40, 200), 2)
        cv2.putText(img, f"{m / step:.1f} h" if unit == "h" else f"{m:.0f} s", (x + 4, pad // 2 + 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (40, 40, 200), 1)
    if series.get("short_threshold_v"):  # what a short looks like on this trace, so "recovers" is read the right way
        cv2.putText(img, "Shorted: voltage pinned near 0 V. Recovered: voltage away from 0 V again (either sign).",
                    (pad, H - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (20, 20, 20), 1)
    cv2.putText(img, f"Cell voltage vs recorded time ({unit}); red lines: times in the finding", (pad, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (20, 20, 20), 1)
    cv2.imwrite(str(out), img)
    return out


_VERDICT_LINE = re.compile(r"(?:(?:final\s+)?(?:verdict|answer)\s*[:\-]\s*)?(supported|refuted|inconclusive)"
                           r"(?:\s*(?:[.:!,;\-–—]|\s)\s*(.*))?", re.I | re.S)
_HEDGE = re.compile(r"^(?:no\b|not\b|but\b|however\b|partial|partly|maybe|perhaps|unclear|\?)"
                    r"|\b(?:supported|refuted|inconclusive)\b|\b(?:not|cannot|can't|n't)\s+(?:\w+\s+){0,2}support",
                    re.I)


def critic_messages(statement: str, image: Path) -> list[dict]:
    return [{"role": "user", "content": [{"type": "text", "text": CRITIC.format(statement=statement)},
                                         {"type": "image_url", "image_url": {"url": tools.image_data_url(image)}}]}]


def critic_verdict(text: str | None) -> tuple[str, str]:
    """Read the critic's answer, failing closed: only a first line that is one plain verdict word (markdown, a
    'Verdict:' label and an explanation after punctuation allowed) counts. Negated, hedged, questioned, mixed or
    late verdicts ("Not supported", "SUPPORTED? No.", a verdict on line 2) read as inconclusive, never as agreement."""
    raw = (text or "").strip()
    lines = [x for x in raw.splitlines() if x.strip()]
    if not lines or TEMPLATE.search(raw):
        return "inconclusive", raw or "no answer"
    rest = "\n".join(lines[1:]).strip()
    m = _VERDICT_LINE.fullmatch(re.sub(r"[*_#`>]", "", lines[0]).strip())
    if not m or re.search(r"\b(?:supported|refuted|inconclusive)\b", rest, re.I):
        return "inconclusive", raw
    explanation = (m.group(2) or "").strip() or rest
    if m.group(1).lower() == "supported" and (_HEDGE.search(explanation)
            or re.search(r"^(?:\(|only\b|if\b|although\b)|\bdoes(?:\s+not|n't)\b", explanation, re.I)):
        return "inconclusive", raw
    return m.group(1).lower(), explanation or m.group(1).upper()


def _first_sentence(text: str | None) -> str | None:
    if not text or TEMPLATE.search(text):
        return None
    s = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return s or None  # the ledger cuts long titles at a word


@dataclass
class Settings:
    profile: str = "dev"
    max_steps: int = 30
    max_tokens: int = 6000          # reasoning models spend most of this thinking
    reasoning_effort: str | None = "medium"
    ask_wait_s: float = 0           # how long ask_person waits for a person; 0 = nobody is there
    pause_limit_s: float = 3600     # a pause longer than this stops the campaign
    retries: int = 3                # transient provider failures retried per call
    retry_base_s: float = 2         # backoff before retry n: retry_base_s * 2**(n-1)
    poll_s: float = 1.0             # how often a wait re-reads the ledger
    on_status: Callable[[str], None] | None = None  # told "paused" / "running" (the API's job status)
    shutting_down: Callable[[], bool] | None = None  # true when the host is stopping: waits end at once


class Campaigner:
    def __init__(self, ledger: Ledger, campaign: Campaign, settings: Settings = Settings(), resume: bool = False):
        self.L, self.c, self.s = ledger, campaign, settings
        self.roles = PROFILES[settings.profile]
        self.clients = {}
        self.ctx = tools.Context(ledger, campaign.id)
        self.spent = 0.0
        self.resuming = resume
        self.cursor = 0                 # last event seq the inbox has read
        self.answered: set[str] = set()  # question ids answered inside ask_person (not repeated by the inbox)

    def _brain(self, role):
        provider, model = self.roles[role]
        if provider not in self.clients:
            self.clients[provider] = _client(provider)
        return self.clients[provider], model

    def _chat(self, role, messages, tools_=None):
        client, model = self._brain(role)
        kw = {"tools": tools_, "tool_choice": "auto"} if tools_ else {}
        if self.s.reasoning_effort and self.roles[role][0] == "fireworks":
            kw["reasoning_effort"] = self.s.reasoning_effort
        cost, attempt = 0.0, 0
        try:
            while True:  # every attempt is a separately metered request (the clients never retry on their own)
                before = client.ledger.balance()["committed_usd"]
                try:
                    r = client.chat(model=model, messages=messages, max_tokens=self.s.max_tokens, **kw)
                    break
                except BudgetExceeded:
                    raise
                except Exception as e:  # noqa: BLE001 - only transient failures are retried; the rest re-raise
                    if not _transient(e):
                        if _refused(e):  # the service turned the request down: end the campaign plainly, no retry
                            raise ProviderFailed(f"{type(e).__name__}: {e}"[:300], refused=True) from e
                        raise
                    attempt += 1
                    error = f"{type(e).__name__}: {e}"[:300]
                    if attempt > self.s.retries:
                        raise ProviderFailed(error) from e
                    wait = self.s.retry_base_s * 2 ** (attempt - 1)
                    self.L.emit(self.c.id, "campaign.retry", "runtime",
                                f"The model service did not answer; trying again ({attempt} of {self.s.retries})",
                                detail=error, data={"role": role, "attempt": attempt, "error": error, "wait_s": wait})
                    time.sleep(wait)
                finally:
                    cost += client.ledger.balance()["committed_usd"] - before
        except BaseException:
            self.spent += cost
            if cost:
                self.L.emit(self.c.id, "cost.updated", "runtime", "Spending updated",
                            data={"usd": round(cost, 6), "role": role})
            raise
        self.spent += cost
        self.L.emit(self.c.id, "cost.updated", "runtime", "Spending updated", data={"usd": round(cost, 6), "role": role})
        return r.choices[0].message

    # ------------------------------------------------------------ memory

    def _memory(self) -> str:
        if self.c.memory == "off":
            return ""
        lessons = self.L.list("lesson")
        if not lessons:
            return ""
        self.L.emit(self.c.id, "memory.loaded", "runtime", f"Remembering {len(lessons)} lesson{'s' if len(lessons) != 1 else ''} from earlier campaigns",
                    refs=[x.id for x in lessons], data={"lessons": [x.text for x in lessons]})
        return "Lessons from earlier campaigns (with evidence):\n" + "\n".join(f"- {x.text} [{x.scope}]" for x in lessons)

    # ------------------------------------------------------------ worker + critic

    def locate(self, video_id, frame, task, region=None) -> tuple[str, list]:
        res = tools.call(self.ctx, "inspect_video", {"video_id": video_id, "frame": frame,
                                                     **({"region": region} if region else {})})
        if res.status != "ok" or not res.images:
            return res.summary or "No frame image to look at.", []
        msg = self._chat("worker", [{"role": "user", "content": [
            {"type": "text", "text": WORKER.format(task=task) + "\n" + res.summary},
            {"type": "image_url", "image_url": {"url": tools.image_data_url(res.images[0])}}]}])
        m = re.search(r"\{.*\}", msg.content or "", re.S)
        try:
            out = json.loads(m.group(0)) if m else {"points": [], "note": "no answer"}
        except json.JSONDecodeError:
            out = {"points": [], "note": "unreadable answer"}
        pts = [p for p in out.get("points", []) if isinstance(p, list) and len(p) == 2][:16]
        self.L.emit(self.c.id, "step.finished", "worker", f"Found {len(pts)} places to track" if pts
                    else "Couldn't find the objects in this frame", detail=out.get("note"), refs=res.outputs,
                    data={"points": pts, "frame": frame})
        return f"Worker points on frame {frame}: {pts}. Note: {out.get('note')}", pts

    def critique(self, claim_id: str, image: Path | None) -> None:
        """Advisory review; recorded, never decides status."""
        if image is None:
            return
        from translucent.contracts import Review
        claim = self.L.get("claim", claim_id)
        try:
            msg = self._chat("critic", critic_messages(claim.statement, image))
        except Exception as e:  # noqa: BLE001 - advisory step: a failure is recorded, campaign continues
            self.L.emit(self.c.id, "step.finished", "critic", "Second opinion unavailable", detail=str(e)[:200])
            return
        verdict, reason = critic_verdict(msg.content)
        rv = Review(id=f"rev_{uuid.uuid4().hex[:12]}", claim_id=claim_id, reviewer="critic", verdict=verdict,
                    reason=reason[:400], model=self.roles["critic"][1],
                    created_at=time.time())
        self.L.add(rv)
        self.L.emit(self.c.id, "claim.reviewed", "critic", "Second opinion: " + {"supported": "agrees",
                    "refuted": "disagrees", "inconclusive": "unsure"}[verdict], detail=rv.reason,
                    refs=[claim_id, rv.id], data={"review": rv.model_dump()})

    # ------------------------------------------------------------ loop

    def _system(self) -> str:
        exps = []
        for vid in self.c.inputs:
            a = self.L.get("artifact", vid)
            exps.append(f"{vid} — {a.label} ({a.source.citation if a.source else ''})")
        return ORCHESTRATOR.format(objective=self.c.objective, experiments="; ".join(exps), memory=self._memory())

    def _last_seq(self) -> int:
        return self.L.db.execute("SELECT COALESCE(MAX(seq), 0) FROM event WHERE campaign_id=?", (self.c.id,)).fetchone()[0]

    def run(self) -> dict:
        if self.resuming:
            return self.resume()
        self.cursor = self._last_seq()  # anything a person sent while the campaign was queued is read at step 1
        self.L.emit(self.c.id, "campaign.started", "runtime", self.c.title, detail=self.c.objective, data=self._models())
        return self._loop([{"role": "system", "content": self._system()},
                           {"role": "user", "content": "Begin the campaign."}])

    def _models(self) -> dict:
        """Which models play which role, recorded so a receipt can say who planned, pointed and second-guessed."""
        return {"profile": self.s.profile, "models": {"planner": self.roles["orchestrator"][1],
                                                      "pointer": self.roles["worker"][1],
                                                      "second_opinion": self.roles["critic"][1]}}

    def resume(self) -> dict:
        """Continue an interrupted, stopped or stalled campaign: the agent's context is rebuilt from the ledger."""
        state, last = campaign_state(self.L, self.c.id)
        previous = "interrupted" if state in ("running", "paused") else last or "not started"
        self.cursor = self._last_seq()
        context = self._rebuild(previous)
        self.L.emit(self.c.id, "campaign.resumed", "runtime", "Campaign picked up where it left off",
                    detail=f"It was {previous}; the agent was given what the records hold so far.",
                    data={"by": "runtime", "previous": previous, "budget_usd": self.c.budget_usd, **self._models()})
        return self._loop([{"role": "system", "content": self._system()}, {"role": "user", "content": context}])

    def _rebuild(self, previous: str) -> str:
        L, cid = self.L, self.c.id
        out = [f"This campaign is resuming (it was {previous}). The records below are what it has done so far: build "
               "on them and do not repeat work that is already recorded. Measurement ids can be cited as they are."]
        ms = [m for r in L.list("run", campaign_id=cid) if r.status == "ok" for m in L.list("measurement", ref=r.id)]
        if ms:
            out.append(f"Measurements ({len(ms)}{', latest 80 shown' if len(ms) > 80 else ''}):")
            for m in ms[-80:]:
                stale = L.stale_reason(m.id)
                out.append(f"- {m.id}: {m.label or m.quantity}{f' ({m.subject})' if m.subject else ''} = {_value(m)}"
                           + (f" [withdrawn: {stale}]" if stale else ""))
        claims = L.list("claim", campaign_id=cid)
        if claims:
            out.append("Findings (the gate's verdict decides; people and the second opinion never do):")
        for k in claims:
            reviews = L.list("review", ref=k.id)
            gate = [r for r in reviews if r.reviewer == "gate"]
            line = f"- {k.id}: \"{k.statement}\" Gate verdict: {L.claim_status(k.id)}."
            if gate:
                line += " " + gate[-1].reason
                failed = [explain_check(c.model_dump()) for c in gate[-1].checks if not c.passed]
                line += (" Why: " + " ".join(dict.fromkeys(failed))) if failed else ""
            for r in reviews:
                if r.reviewer == "human":
                    line += (f" {r.author} challenged it: {r.reason}" if r.verdict == "refuted"
                             else f" {r.author} signed off on it: {r.reason}")
                elif r.reviewer == "critic":
                    line += f" Second opinion: {r.verdict}."
            out.append(line + (f" (replaces {k.supersedes})" if k.supersedes else ""))
        # tool work that left no measurement (frames looked at, points, outlines) would otherwise be redone from scratch
        done, seen = [], []
        for ev in L.events(cid):
            text = ev.data.get("summary") or ev.detail or ""
            if ev.type == "tool.finished" and ev.actor == "inspect_video":
                seen.append(text.split("Image shows ")[-1].split(", gridlines")[0].removeprefix("frame "))
            elif ev.type == "tool.finished" and ev.data.get("status") == "ok":
                # long summaries keep their end, where the ids of what the tool made are
                done.append(f"- {ev.actor}: {text if len(text) <= 420 else text[:280] + ' ... ' + text[-140:]}")
            elif ev.type == "step.finished" and ev.actor == "worker":
                done.append(f"- worker on frame {ev.data.get('frame')}: {ev.detail or ''} Points {ev.data.get('points')}"[:400])
        if seen:
            out.append(f"Frames already looked at ({len(seen)}; images are not shown again, so look again only at a "
                       f"frame you need to see): " + "; ".join(seen[-40:]))
        if done:
            out.append(f"Other tool work already done{' (latest 30)' if len(done) > 30 else ''}:")
            out += done[-30:]
        lessons = [x for x in L.list("lesson") if x.learned_in == cid]
        if lessons:
            out += ["Lessons recorded in this campaign:"] + [f"- {x.text}" for x in lessons]
        asked, notes = {}, []
        for ev in L.events(cid):
            if ev.type == "human.note":
                notes.append(f"- {ev.data.get('author')}: {ev.data.get('text')}")
            elif ev.type == "question.asked":
                asked[ev.data.get("question_id")] = [ev.data.get("question"), None]
            elif ev.type == "question.answered" and ev.data.get("question_id") in asked:
                asked[ev.data["question_id"]][1] = f"{ev.data.get('author')} answered: {ev.data.get('text')}"
        if notes:
            out += ["Notes from people on the team:"] + notes
        if asked:
            out += ["Questions you asked the team:"] + [f"- {q} -> {a or 'nobody answered'}" for q, a in asked.values()]
        out.append("Continue the campaign: call a tool, or finish if the objective is already answered.")
        text = "\n".join(out)
        return text if len(text) <= 16000 else text[:16000].rsplit("\n", 1)[0] + "\n(more records are in the ledger)"

    # ------------------------------------------------------------ people

    def _shutting_down(self) -> bool:
        return bool(self.s.shutting_down and self.s.shutting_down())

    def _status(self, status: str):
        if self.s.on_status:
            try:
                self.s.on_status(status)
            except Exception:  # noqa: BLE001 - a job-file hiccup must not stop the campaign
                log.exception("status callback failed")

    def _person_message(self, ev, who: str) -> str | None:
        d = ev.data
        if ev.type == "human.note":
            return f"Note from {who}, a person on the team: {d.get('text', '')}"
        if ev.type == "question.answered" and d.get("question_id") not in self.answered:
            q = next((e.data.get("question") for e in self.L.events(self.c.id) if e.type == "question.asked"
                      and e.data.get("question_id") == d.get("question_id")), "")
            return f"{who}, a person on the team, answered your earlier question \"{q}\": {d.get('text', '')}"
        rv = d.get("review") if ev.type == "claim.reviewed" else None
        if isinstance(rv, dict) and rv.get("reviewer") == "human":
            if rv.get("verdict") == "refuted":
                return (f"{who} challenged finding {rv['claim_id']}: {rv.get('reason', '').rstrip('. ')}. Re-examine it: "
                        "if the evidence holds, say why in one sentence; if not, propose a corrected finding with "
                        "supersedes.")
            return f"{who}, a person on the team, signed off on finding {rv['claim_id']}: {rv.get('reason', '')}"
        return None

    def _inbox(self, messages) -> str | None:
        """Hand new person events to the agent as user messages; hold here while paused. Returns None to carry on,
        else why to stop: {"by": name} or {"by": None, "reason": "pause_limit" | "shutdown"}."""
        paused_at = None
        while True:
            for ev in self.L.events(self.c.id, self.cursor):
                self.cursor = ev.seq
                if not ev.actor.startswith("person:"):
                    continue
                who = ev.data.get("author") or ev.actor.removeprefix("person:")
                if ev.type == "campaign.control":
                    action = ev.data.get("action")
                    if action == "stop":
                        return {"by": who}
                    if action == "pause" and paused_at is None:
                        paused_at = time.monotonic()
                        self.L.emit(self.c.id, "campaign.paused", "runtime", f"Paused by {who}", data={"by": who})
                        self._status("paused")
                    elif action == "resume" and paused_at is not None:
                        paused_at = None
                        self.L.emit(self.c.id, "campaign.resumed", "runtime", f"Resumed by {who}", data={"by": who})
                        self._status("running")
                elif text := self._person_message(ev, who):
                    messages.append({"role": "user", "content": text})
            if paused_at is None:
                return None
            if time.monotonic() - paused_at >= self.s.pause_limit_s:
                return {"by": None, "reason": "pause_limit"}
            if self._shutting_down():
                return {"by": None, "reason": "shutdown"}
            time.sleep(self.s.poll_s)

    def ask(self, question: str, options=None) -> str:
        """ask_person: record the question, wait up to ask_wait_s for an answer (re-reading the ledger, which the API
        writes from another connection), else record that it expired. Spends nothing."""
        qid = f"q_{uuid.uuid4().hex[:10]}"
        question = str(question).strip()[:1000]
        options = [str(o)[:200] for o in options][:8] if isinstance(options, list) and options else None
        asked = self.L.emit(self.c.id, "question.asked", "orchestrator", "Asked the team: " + question,
                            detail=question, data={"question_id": qid, "question": question, "options": options})
        deadline, stopped = time.monotonic() + max(0.0, self.s.ask_wait_s), False
        while not stopped:
            for ev in self.L.events(self.c.id, asked.seq):
                if ev.type == "question.answered" and ev.data.get("question_id") == qid:
                    self.answered.add(qid)
                    return f"{ev.data.get('author')}, a person on the team, answered: {ev.data.get('text')}"
                stopped = stopped or (ev.type == "campaign.control" and ev.data.get("action") == "stop")
            left = deadline - time.monotonic()
            if left <= 0 or self._shutting_down():
                break
            if not stopped:
                time.sleep(min(self.s.poll_s, left))
        self.L.emit(self.c.id, "question.expired", "runtime", "No answer from the team; carrying on", refs=[],
                    data={"question_id": qid})
        return "The team stopped the campaign while you waited." if stopped else NOBODY

    # ------------------------------------------------------------ loop

    def _loop(self, messages) -> dict:
        schemas = [t["schema"] for n, t in tools.REGISTRY.items()
                   if n != "run_analysis_code" or self.roles["orchestrator"][1] not in NO_CODE_TOOL] + EXTRA_TOOLS
        result, idle = {"status": "max_steps"}, 0
        for step in range(1, self.s.max_steps + 1):
            self.ctx.step_id = f"s{step}"
            stop = self._inbox(messages)
            if stop is not None:
                result = {"status": "stopped", **stop, "spent_usd": round(self.spent, 4)}
                break
            if self.spent >= self.c.budget_usd:
                result = {"status": "budget", "spent_usd": round(self.spent, 4)}
                break
            try:
                msg = self._chat("orchestrator", messages, schemas)
            except ProviderFailed as e:
                result = {"status": "provider_error", "error": str(e), "refused": e.refused,
                          "spent_usd": round(self.spent, 4)}
                break
            calls = msg.tool_calls or []
            headline = _first_sentence(msg.content)
            if headline:
                self.L.emit(self.c.id, "step.started", "orchestrator", headline, detail=(msg.content or "")[:600])
            # a reply without a tool call is kept short: a runaway 6000-token reply in the history begets more of them
            messages.append({"role": "assistant", "content": (msg.content or "") if calls else (msg.content or "")[:1500],
                             **({"tool_calls": [c.model_dump() for c in calls]} if calls else {})})
            idle = 0 if calls else idle + 1
            if idle >= 3:  # three replies in a row without a tool call: the model is stuck, stop spending
                result = {"status": "stalled", "spent_usd": round(self.spent, 4)}
                break
            if not calls:  # tool_choice can only be "auto": a reply without a tool call is a nudge, not an error
                garbled = bool(msg.content and TEMPLATE.search(msg.content))
                log.warning("step %d: reply without a tool call (%d chars, garbled=%s): %r ... %r", step,
                            len(msg.content or ""), garbled, (msg.content or "")[:300], (msg.content or "")[-200:])
                messages.append({"role": "user", "content": "Your tool call arrived as plain text and did not run; "
                                 "call the tool again using the tool-call format." if garbled else
                                 "Continue: call a tool, or finish if you are done."})
                continue
            done = False
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except json.JSONDecodeError:
                    out = "Arguments were not valid JSON; call again."
                else:
                    out, done = self._dispatch(c.function.name, args, result)
                messages.append({"role": "tool", "tool_call_id": c.id, "content": out[:6000]})
            if done:
                break
        status, detail = result["status"], None
        if status == "stopped":
            title = (f"Stopped by {result['by']}" if result["by"] else "Campaign stopped (the server shut down)"
                     if result.get("reason") == "shutdown" else "Campaign stopped (paused for too long)")
        elif status == "provider_error" and result.get("refused"):
            title = "Campaign stopped (the model service refused the request)"
            detail = (f"The model service turned the request down ({result['error']}). Everything recorded so far is "
                      "kept, and the campaign can be resumed once the cause is fixed.")
        elif status == "provider_error":
            title = "Campaign stopped (the model service kept failing)"
            detail = (f"The model service failed {self.s.retries + 1} times in a row ({result['error']}). Everything "
                      "recorded so far is kept, and the campaign can be resumed.")
        else:
            title = {"finished": "Campaign finished", "budget": "Campaign stopped (budget spent)",
                     "stalled": "Campaign stopped (no progress)"}.get(status, "Campaign stopped (step limit)")
        self.L.emit(self.c.id, "campaign.finished", "runtime", title, detail=detail, data=result)
        return result

    def _dispatch(self, name, args, result) -> tuple[str, bool]:
        if name == "ask_person":
            if not str(args.get("question") or "").strip():
                return "ask_person needs a question.", False
            return self.ask(args["question"], args.get("options")), False
        if name == "locate_objects":
            try:
                text, _ = self.locate(args["video_id"], args["frame"], args["task"], args.get("region"))
            except BudgetExceeded:
                raise
            except Exception as e:  # noqa: BLE001 - the worker is one step: its failure is shown, campaign continues
                self.L.emit(self.c.id, "step.finished", "worker", "Couldn't look at that frame",
                            detail=f"{type(e).__name__}: {e}")
                text = f"locate_objects failed: {type(e).__name__}: {e}"
            return text, False
        if name == "finish":
            result.update(status="finished", answer=args.get("answer"), not_proven=args.get("not_proven"))
            return "Campaign closed.", True
        res = tools.call(self.ctx, name, args)
        if name == "propose_finding" and res.data.get("verdict") == "supported":
            self.critique(res.data["claim_id"], self._evidence_image(res.data["claim_id"]))
        return f"[{res.status}] {res.summary}", False

    def _evidence_image(self, claim_id) -> Path | None:
        """What the critic looks at: for a timing finding, the cell's voltage log with the cited times marked;
        otherwise the most recent frame image this campaign produced."""
        claim = self.L.get("claim", claim_id)
        ms = [m for i in claim.predicate.measurement_ids if (m := self.L.get("measurement", i))]
        # a log marker (time into a charge, in h) carries its window on the log's own clock
        times = [sum(m.time_range) / 2 if m.time_range else m.value for m in ms
                 if m.time_range or (m.unit == "s" and isinstance(m.value, (int, float)))]
        series = instruments.voltage(tools._experiment(self.L, self.c.inputs[0])) if times else None
        if series:
            return _voltage_plot(series, times, self.ctx.scratch() / f"{claim_id}_voltage.png")
        frames = [a for a in self.L.list("artifact") if a.kind == "frame"]
        return self.L.blob_path(frames[-1].id) if frames else None


def new_campaign(ledger: Ledger, objective: str, title: str, inputs: list[str], memory="on", budget_usd=5.0) -> Campaign:
    c = Campaign(id=f"cmp_{uuid.uuid4().hex[:10]}", objective=objective, title=title, inputs=inputs, memory=memory,
                 budget_usd=budget_usd, created_at=time.time())
    ledger.add(c)
    return c
