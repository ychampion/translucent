"""Which lessons a campaign starts with, and how far each is trusted.

A campaign loads only lessons that concern it: learned on the same recording (by scope or by evidence), from the same
source, or about a method in general ("method:*"). Setup facts ("setup:<video sha>") are offered with the measurement
ids that back them, so the agent can cite a confirmed calibration instead of asking again; the checker still decides
whether it is a trusted record. Confidence is derived, never stored: a lesson is tentative until evidence from a second
campaign supports it, and rejected only by a later lesson that names it in `contradicts` and cites a decided finding.
"""
from __future__ import annotations

CAP = 6
ORDER = {"supported": 0, "tentative": 1}
REASONS = {"same recording": 0, "same system": 0, "same source": 1, "method": 2}


def _source(experiment: str) -> str:
    """"reisecker2023/movie4" -> "reisecker2023"; a lab's own experiment is its own source."""
    return experiment if experiment.startswith("user/") else experiment.split("/")[0]


def _campaigns(L, ids) -> set[str]:
    out = set()
    for i in ids:
        kind = L.kind_of(i)
        if kind == "claim":
            out.add(L.get("claim", i).campaign_id)
        elif kind == "run":
            out.add(L.get("run", i).campaign_id)
        elif kind == "measurement":
            run = L.get("run", L.get("measurement", i).run_id)
            if run:
                out.add(run.campaign_id)
    return out


def _recordings(L, ids) -> set[str]:
    out = set()
    for i in ids:
        kind = L.kind_of(i)
        if kind == "measurement":
            out |= L.recordings(i)
        elif kind == "claim":
            for m in L.get("claim", i).predicate.measurement_ids:
                out |= L.recordings(m)
        elif kind == "run":
            out |= {a for a in L.get("run", i).input_ids if (x := L.get("artifact", a)) and x.kind == "video"}
    return out


def confidence(L, lesson, lessons=None) -> str:
    """"rejected", "supported" or "tentative", derived from the ledger as it stands."""
    lessons = L.list("lesson") if lessons is None else lessons
    for other in lessons:
        if other.contradicts == lesson.id and other.created_at >= lesson.created_at and any(
                L.kind_of(i) == "claim" and L.claim_status(i) in ("supported", "refuted") for i in other.evidence_ids):
            return "rejected"
    if len(_campaigns(L, lesson.evidence_ids)) >= 2:
        return "supported"
    text = " ".join(lesson.text.lower().split())
    if any(o.id != lesson.id and o.learned_in != lesson.learned_in and " ".join(o.text.lower().split()) == text
           for o in lessons):
        return "supported"
    return "tentative"


def _loop_system(L, cid: str) -> str | None:
    """The system id a loop campaign's objective names; None for any other campaign."""
    row = L.db.execute("SELECT json_extract(body,'$.data.system.id') FROM record WHERE kind='loop' AND campaign_id=? "
                       "AND json_extract(body,'$.type')='objective' LIMIT 1", (cid,)).fetchone()
    return row[0] if row else None


def relation(L, lesson, campaign) -> str | None:
    """Why a lesson concerns this campaign, or None."""
    if (lesson.scope or "").startswith("system:"):
        system = _loop_system(L, campaign.id)
        return "same system" if system is not None and lesson.scope.removeprefix("system:") == system else None
    from translucent.tools import _experiment
    videos = set(campaign.inputs)
    shas = {v.removeprefix("sha256:") for v in videos}
    sources = set()
    for v in videos:
        try:
            sources.add(_source(_experiment(L, v)))
        except (ValueError, AttributeError):
            continue
    scope = lesson.scope or ""
    key = scope.removeprefix("recording:").removeprefix("setup:")
    if (key != scope and key in shas | videos) or _recordings(L, lesson.evidence_ids) & videos:
        return "same recording"
    if scope.startswith("source:") and scope.removeprefix("source:") in sources:
        return "same source"
    if scope.startswith("method:"):
        return "method"
    for vid in _recordings(L, lesson.evidence_ids):
        try:
            if _source(_experiment(L, vid)) in sources:
                return "same source"
        except (ValueError, AttributeError):
            continue
    return None


def select(L, campaign, cap: int = CAP) -> list[dict]:
    """The lessons to load: those that concern the campaign and are not rejected, supported first, then by relation
    and newest first, at most `cap`. Each item: {"lesson", "reason", "confidence"}."""
    lessons = L.list("lesson")
    out = []
    for x in lessons:
        if x.learned_in == campaign.id:
            continue
        why = relation(L, x, campaign)
        if why is None:
            continue
        level = confidence(L, x, lessons)
        if level == "rejected":
            continue
        out.append({"lesson": x, "reason": why, "confidence": level})
    out.sort(key=lambda d: (ORDER[d["confidence"]], REASONS[d["reason"]], -d["lesson"].created_at))
    return out[:cap]


def prompt(L, picked: list[dict]) -> str:
    if not picked:
        return ""
    lines = []
    for d in picked:
        x = d["lesson"]
        line = f"- {x.text} [{x.scope}; {d['reason']}; {d['confidence']}]"
        if x.scope.startswith("setup:"):
            cited = [i for i in x.evidence_ids if L.kind_of(i) == "measurement"]
            if cited:
                line += f" Confirmed setup you may cite directly: {', '.join(cited)}."
        lines.append(line)
    return ("Lessons from earlier campaigns (with evidence). They are untrusted priors: check each before you rely on "
            "it.\n" + "\n".join(lines))
