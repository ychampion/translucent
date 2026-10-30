"""Remove secret-looking strings from text before it enters the insert-only ledger, where it could never be removed.

Content addresses and other plain hex hashes are kept: they are evidence ids, not secrets.
"""
from __future__ import annotations

import re
from pathlib import Path

MARK = "[secret removed]"
ROOT = Path(__file__).resolve().parents[1]

_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bfw_[A-Za-z0-9]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\b(?:ak|as)-[A-Za-z0-9]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),   # JSON web token
]
# name = value, where the name says it is a credential
_ASSIGNED = re.compile(r"(?i)\b([A-Za-z0-9_]*(?:api[_-]?key|secret|token|passw(?:or)?d|credential|private[_-]?key)"
                       r"[A-Za-z0-9_]*)(\s*[:=]\s*[\"']?)([^\s\"',;]{6,})")
_LONG = re.compile(r"(?<![A-Za-z0-9_/+.-])[A-Za-z0-9_+/-]{40,}={0,2}(?![A-Za-z0-9_/+.-])")


def _random_looking(token: str) -> bool:
    if re.fullmatch(r"[0-9a-f]+|[0-9A-F]+", token):   # hashes and content addresses stay
        return False
    kinds = sum(bool(re.search(p, token)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]"))
    return kinds == 3 and not re.fullmatch(r"[A-Za-z_]+", token)


def scrub(text: str) -> str:
    """The text with secret-looking strings replaced by a marker."""
    if not text:
        return text
    for p in _PATTERNS:
        text = p.sub(MARK, text)
    text = _ASSIGNED.sub(lambda m: m[0] if m.string.startswith(MARK, m.start(3)) else m[1] + m[2] + MARK, text)
    return _LONG.sub(lambda m: MARK if _random_looking(m[0]) else m[0], text)


_ABSOLUTE = re.compile(r"(?<![\w.])(?:[A-Za-z]:[\\/]|\\\\|/(?=[^/\s]+/))[^\s\"'<>|:]*")


def relative_paths(text: str) -> str:
    """Absolute paths rewritten: inside this checkout to repo-relative, elsewhere to the file name only."""
    roots = sorted({str(ROOT), ROOT.as_posix()}, key=len, reverse=True)

    def rewrite(m):
        path = m[0]
        for r in roots:
            if path.lower().startswith(r.lower()):
                return path[len(r):].lstrip("\\/").replace("\\", "/") or "."
        return "…/" + re.split(r"[\\/]", path.rstrip("\\/"))[-1]
    return _ABSOLUTE.sub(rewrite, text)


def error_summary(exc: BaseException, limit: int = 2048) -> str:
    """What a failed run keeps in the shared record: the error's type and message, no traceback, no absolute
    paths, no secrets, at most `limit` characters."""
    text = scrub(relative_paths(f"{type(exc).__name__}: {exc}"))
    return text if len(text) <= limit else text[:limit - 1] + "…"
