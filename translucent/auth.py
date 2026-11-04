"""Who a request acts as (M2 contract §1): credentials, browser sessions and roles.

A credential is {id, name, role, ledgers}. Its key is random, shown once, and stored only as its sha256, in
`credentials.json` beside the server's ledgers (`<data_root>/credentials.json`, or `<state_dir>/credentials.json` for
a one-ledger server). Two environment tokens also work: TRANSLUCENT_ADMIN_TOKEN (role admin, named
TRANSLUCENT_ADMIN_NAME, default "administrator") and TRANSLUCENT_TEAM_TOKEN (role engineer, named
TRANSLUCENT_TEAM_NAME, default "team"), each over every ledger.

Identity comes from the credential, never from a request body: an engineer, admin or viewer acts as the person the
credential names, a planner as the agent it names. A browser signs in once with its key and then carries an opaque
session cookie; sessions are stored hashed in the server's state folder, so a restart keeps them, and each is bound
to the key that opened it, so rotating an env token or revoking or replacing a stored key ends it.

Roles: viewer reads; engineer reads and does a person's work (without raising a budget); planner reads and does an
agent's work (propose, define a measurement, cancel its own request); admin is an engineer who may also open a paid
campaign and raise a budget.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import uuid

ROLES = ("admin", "engineer", "viewer", "planner")
SESSION_COOKIE = "translucent_session"
SESSION_TTL_S = 12 * 3600
CLIENT_HEADER = "x-translucent-client"     # a cookie-authenticated write carries "X-Translucent-Client: web"
SIGN_IN = "Sign in"
FAILED_SIGN_INS, FAILED_WINDOW_S = 10, 60.0
THROTTLE_ADDRESSES = 10_000         # addresses the failed-key throttle remembers at most
SESSIONS_PER_CREDENTIAL = 20        # live browser sessions kept per credential; signing in again drops the oldest
NAME = re.compile(r"[^\x00-\x1f\x7f]{1,80}\Z")

# What each role may do over HTTP, beyond reading the ledgers in its scope. Loop capabilities keep their names
# (translucent.loop_service.CAPABILITIES); "upload" is a file sent to the server, "legacy" a person's write to a video
# campaign, "paid" a paid run, "raise_budget" an amendment that raises a model or experiment budget.
_PERSON = frozenset({"import_dataset", "open_campaign", "define_measurement", "propose", "cancel", "conclude",
                     "approve", "reject", "revise", "submit_result", "amend", "note", "upload", "legacy"})
ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "viewer": frozenset(),
    "planner": frozenset({"propose", "define_measurement", "cancel"}),
    "engineer": _PERSON,
    "admin": _PERSON | {"paid", "raise_budget"},
}
_WORDS = {"import_dataset": "import a dataset", "open_campaign": "open a campaign",
          "define_measurement": "define a measurement", "propose": "propose an experiment",
          "cancel": "cancel a request", "conclude": "conclude a campaign", "approve": "approve a request",
          "reject": "reject a request", "revise": "revise a request", "submit_result": "submit a result",
          "amend": "amend an objective", "note": "add a note", "upload": "upload files",
          "legacy": "act as a person", "paid": "start a paid run", "raise_budget": "raise a budget"}


class Refused(Exception):
    """A credential may not do this: HTTP 403 with the sentence."""


@dataclass(frozen=True)
class Credential:
    id: str
    name: str
    role: str
    ledgers: str | tuple[str, ...]      # "*" or ledger ids

    @property
    def kind(self) -> str:
        return "agent" if self.role == "planner" else "person"

    def reads(self, lid: str) -> bool:
        return self.ledgers == "*" or lid in self.ledgers

    def may(self, capability: str) -> bool:
        return capability in ROLE_CAPABILITIES.get(self.role, frozenset())

    def require(self, capability: str):
        if not self.may(capability):
            article = "An" if self.role[0] in "aeiou" else "A"
            raise Refused(f"{article} {self.role} key cannot {_WORDS.get(capability, capability.replace('_', ' '))}")

    def actor(self, via: str = "http"):
        from translucent.loop_service import Actor
        return Actor(self.kind, self.name, via)

    def acting_as(self, author: str | None = None, agent: str | None = None):
        """The actor this credential is, after checking the body's optional `author` / `agent`: if present they must
        be this credential's name and kind."""
        if author is not None and (self.kind != "person" or author != self.name) or \
                agent is not None and (self.kind != "agent" or agent != self.name):
            raise Refused(f"This key acts as {self.name}")
        return self.actor()

    def public(self) -> dict:
        return {"name": self.name, "role": self.role, "kind": self.kind,
                "ledgers": "*" if self.ledgers == "*" else list(self.ledgers)}


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def new_key() -> str:
    return secrets.token_urlsafe(32)


def _write_private(path: Path, obj):
    """Atomic replace of a JSON file readable by its owner only (where the OS allows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(100):      # Windows refuses to replace a file a reader has open: wait for it to close
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 99:
                    raise
                time.sleep(0.01)
    finally:
        temp.unlink(missing_ok=True)


def _ledgers(value) -> str | tuple[str, ...]:
    return "*" if value == "*" else tuple(value)


class CredentialStore:
    """credentials.json: {"credentials": [{id, name, role, ledgers, key_sha256, created_at, revoked_at}]}. The file is
    re-read when it changes, so `translucent keys add/revoke` take effect on a running server."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._mutex = threading.Lock()
        self._cache: tuple[tuple[int, int], list[dict]] | None = None

    def exists(self) -> bool:
        return self.path.is_file()

    def _entries(self) -> list[dict]:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return []
        stamp = (st.st_mtime_ns, st.st_size)
        with self._mutex:
            if self._cache is None or self._cache[0] != stamp:
                entries = json.loads(self.path.read_text(encoding="utf-8")).get("credentials", [])
                self._cache = (stamp, entries)
            return self._cache[1]

    @staticmethod
    def _credential(entry: dict) -> Credential:
        return Credential(entry["id"], entry["name"], entry["role"], _ledgers(entry["ledgers"]))

    def add(self, name: str, role: str, ledgers: str | list[str] = "*", *, key: str | None = None
            ) -> tuple[Credential, str]:
        """A new credential and its key (shown once; only its hash is kept). `key` adopts an existing secret, such as
        a team-key file made before credentials existed."""
        name = name.strip() if isinstance(name, str) else name
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("a credential's name is 1-80 characters, with no control characters")
        if role not in ROLES:
            raise ValueError(f"role is one of {', '.join(ROLES)}")
        if ledgers != "*":
            from translucent.workspace import valid_id
            ledgers = sorted(set(ledgers))
            if not ledgers or not all(valid_id(lid) for lid in ledgers):
                raise ValueError("ledgers is * or one or more ledger ids")
        key = key or new_key()
        if len(key) < 20:
            raise ValueError("a key is at least 20 characters")
        entry = {"id": "cred_" + secrets.token_hex(8), "name": name, "role": role, "ledgers": ledgers,
                 "key_sha256": digest(key), "created_at": time.time(), "revoked_at": None}
        with self._mutex:
            entries = self._read_fresh()
            entries.append(entry)
            _write_private(self.path, {"credentials": entries})
            self._cache = None
        return self._credential(entry), key

    def _read_fresh(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return json.loads(self.path.read_text(encoding="utf-8")).get("credentials", [])

    def list(self) -> list[dict]:
        """Every credential without its hash, revoked ones included (with revoked_at)."""
        return [{k: v for k, v in e.items() if k != "key_sha256"} for e in self._entries()]

    def revoke(self, cid: str) -> bool:
        with self._mutex:
            entries = self._read_fresh()
            hit = next((e for e in entries if e["id"] == cid and e.get("revoked_at") is None), None)
            if hit is None:
                return False
            hit["revoked_at"] = time.time()
            _write_private(self.path, {"credentials": entries})
            self._cache = None
        return True

    def find(self, key: str) -> Credential | None:
        """The active credential whose key this is. Compares the hash with every entry in constant time."""
        wanted, found = digest(key).encode(), None
        for entry in self._entries():
            match = secrets.compare_digest(wanted, str(entry.get("key_sha256", "")).encode())
            if match and entry.get("revoked_at") is None and found is None:
                found = entry
        return self._credential(found) if found else None

    def get(self, cid: str) -> Credential | None:
        entry = next((e for e in self._entries() if e["id"] == cid and e.get("revoked_at") is None), None)
        return self._credential(entry) if entry else None

    def secret(self, cid: str) -> str | None:
        """The key sha256 of an active credential, or None."""
        entry = next((e for e in self._entries() if e["id"] == cid and e.get("revoked_at") is None), None)
        return str(entry.get("key_sha256", "")) or None if entry else None

    def has_admin(self) -> bool:
        return any(e["role"] == "admin" and e.get("revoked_at") is None for e in self._entries())


ENV_TOKEN_MIN = 32     # an env token shorter than this is refused at startup (server.create_app): a matching key
                       # is never throttled (no lockout), so a chosen token's length is what stops online guessing


def env_credentials() -> list[tuple[str, Credential]]:
    """(token, credential) for TRANSLUCENT_ADMIN_TOKEN and TRANSLUCENT_TEAM_TOKEN when set."""
    out = []
    if token := os.environ.get("TRANSLUCENT_ADMIN_TOKEN"):
        out.append((token, Credential("env:admin", os.environ.get("TRANSLUCENT_ADMIN_NAME") or "administrator",
                                      "admin", "*")))
    if token := os.environ.get("TRANSLUCENT_TEAM_TOKEN"):
        out.append((token, Credential("env:team", os.environ.get("TRANSLUCENT_TEAM_NAME") or "team", "engineer", "*")))
    return out


def _binding(token: str, secret: str) -> str:
    """What ties a session to the credential's secret (its key's sha256) when it opened. Keyed by the session token,
    which the file does not hold, so the file alone cannot be used to test guesses at a short env token."""
    return hmac.new(token.encode("utf-8"), secret.encode("utf-8"), hashlib.sha256).hexdigest()


class SessionStore:
    """Browser sessions: {sha256(session token): {credential, binding, expires_at, created_at}} in one private JSON
    file. Writes drop expired sessions and keep at most SESSIONS_PER_CREDENTIAL per credential (the newest); reads
    use the parsed file until it changes on disk."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._mutex = threading.RLock()      # a write re-reads under the same lock
        self._cache: tuple[tuple[int, int, int], dict] | None = None

    def _read(self) -> dict:
        """The stored sessions, parsed once per version of the file. Callers must not change the dict."""
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return {}
        stamp = (st.st_mtime_ns, st.st_size, st.st_ino)     # a write replaces the file: a new inode as well
        with self._mutex:
            if self._cache is None or self._cache[0] != stamp:
                sessions = json.loads(self.path.read_text(encoding="utf-8")).get("sessions", {})
                self._cache = (stamp, sessions)
            return self._cache[1]

    def _write(self, sessions: dict, now: float):
        """Store `sessions` without the expired ones and with at most SESSIONS_PER_CREDENTIAL per credential.
        Call with the mutex held."""
        live = sorted(((h, s) for h, s in sessions.items() if s["expires_at"] > now),
                      key=lambda hs: hs[1].get("created_at", 0), reverse=True)
        kept, per = {}, {}
        for h, s in live:
            per[s["credential"]] = per.get(s["credential"], 0) + 1
            if per[s["credential"]] <= SESSIONS_PER_CREDENTIAL:
                kept[h] = s
        _write_private(self.path, {"sessions": kept})
        self._cache = None

    def open(self, credential: Credential, secret: str) -> tuple[str, float]:
        """A new session for `credential`, bound to `secret` (the sha256 of the key that signed in)."""
        token, now = secrets.token_urlsafe(32), time.time()
        expires = now + SESSION_TTL_S
        with self._mutex:
            sessions = dict(self._read())
            sessions[digest(token)] = {"credential": credential.id, "binding": _binding(token, secret),
                                       "expires_at": expires, "created_at": now}
            self._write(sessions, now)
        return token, expires

    def lookup(self, token_hash: str) -> dict | None:
        """The live session stored under sha256(token), or None."""
        entry = self._read().get(token_hash)
        if entry is None or entry["expires_at"] <= time.time():
            return None
        return entry

    def resolve(self, token: str) -> tuple[str, float] | None:
        """(credential id, expires_at) of a live session, or None."""
        entry = self.lookup(digest(token))
        return None if entry is None else (entry["credential"], entry["expires_at"])

    def end(self, token: str):
        with self._mutex:
            sessions = dict(self._read())
            if sessions.pop(digest(token), None) is not None:
                self._write(sessions, time.time())


class Throttle:
    """Failed key checks per client address. A key that matches is never refused; a failed one beyond FAILED_SIGN_INS
    in FAILED_WINDOW_S is answered 429 instead of 401. The table holds at most `max_addresses` addresses, ordered by
    their latest failure, so idle and stale ones are dropped first."""

    def __init__(self, limit: int = FAILED_SIGN_INS, window: float = FAILED_WINDOW_S,
                 max_addresses: int = THROTTLE_ADDRESSES):
        self.limit, self.window, self.max_addresses = limit, window, max_addresses
        self._failures: dict[str, deque] = {}        # insertion order = order of each address's latest failure
        self._mutex = threading.Lock()

    def reset(self):
        with self._mutex:
            self._failures.clear()

    def _count(self, q: deque | None, now: float) -> int:
        return 0 if q is None else sum(1 for t in q if t > now - self.window)

    def blocked(self, addr: str) -> bool:
        """Whether this address has used up its failed attempts in the window."""
        with self._mutex:
            return self._count(self._failures.get(addr), time.monotonic()) >= self.limit

    def fail(self, addr: str) -> bool:
        """Record a failed attempt from `addr`; True when it was already over the limit (answer 429, not 401)."""
        with self._mutex:
            now = time.monotonic()
            q = self._failures.pop(addr, None) or deque(maxlen=self.limit)
            over = self._count(q, now) >= self.limit
            q.append(now)
            self._failures[addr] = q                   # re-inserted last: the newest failure
            cutoff = now - self.window
            while self._failures:                      # oldest first: stale entries, then any over the cap
                first = next(iter(self._failures))
                if self._failures[first][-1] > cutoff and len(self._failures) <= self.max_addresses:
                    break
                del self._failures[first]
            return over


@dataclass(frozen=True)
class Principal:
    credential: Credential
    via: str                    # "bearer" or "cookie"
    expires_at: float | None = None
    secret: str = field(default="", repr=False)              # the credential's key sha256 when it was checked
    session: str | None = field(default=None, repr=False)    # sha256 of the session token (cookie)


class Auth:
    """The server's view of credentials and sessions."""

    def __init__(self, credentials: Path | str, sessions: Path | str):
        self.store = CredentialStore(credentials)
        self.sessions = SessionStore(sessions)
        self.throttle = Throttle()

    def by_key(self, key: str) -> Credential | None:
        wanted, found = digest(key).encode(), None
        for token, cred in env_credentials():
            if secrets.compare_digest(wanted, digest(token).encode()) and found is None:
                found = cred
        stored = self.store.find(key)
        return found or stored

    def by_id(self, cid: str) -> Credential | None:
        if cid.startswith("env:"):
            return next((c for _, c in env_credentials() if c.id == cid), None)
        return self.store.get(cid)

    def secret(self, cid: str) -> str | None:
        """The sha256 of the key an active credential has now: the env token's, or the stored key_sha256."""
        if cid.startswith("env:"):
            return next((digest(token) for token, c in env_credentials() if c.id == cid), None)
        return self.store.secret(cid)

    def open_session(self, credential: Credential, key: str) -> tuple[str, float]:
        """A browser session for the credential `key` matched, bound to that key."""
        return self.sessions.open(credential, digest(key))

    def has_admin(self) -> bool:
        return bool(os.environ.get("TRANSLUCENT_ADMIN_TOKEN")) or self.store.has_admin()

    def any_credential(self) -> bool:
        return bool(env_credentials()) or any(e.get("revoked_at") is None for e in self.store._entries())

    def authenticate(self, authorization: str | None, cookie: str | None) -> tuple[Principal | None, bool]:
        """(principal or None, whether a key was presented and refused). A bearer key wins over a cookie; a bad
        bearer key is refused even when a session cookie is valid. A session holds only while its credential's key
        is the one that opened it: rotating an env token, or revoking or replacing a stored key, ends it."""
        if authorization:
            scheme, _, key = authorization.partition(" ")
            key = key.strip()
            cred = self.by_key(key) if scheme.lower() == "bearer" and key else None
            return (Principal(cred, "bearer", secret=digest(key)), False) if cred else (None, True)
        if cookie:
            token_hash = digest(cookie)
            entry = self.sessions.lookup(token_hash)
            cred = self.by_id(entry["credential"]) if entry else None
            secret = self.secret(cred.id) if cred is not None else None
            if cred is not None and cred.kind == "person" and secret is not None and \
                    hmac.compare_digest(str(entry.get("binding", "")), _binding(cookie, secret)):
                return Principal(cred, "cookie", entry["expires_at"], secret=secret, session=token_hash), False
        return None, False

    def current(self, principal: Principal, lid: str | None = None) -> bool:
        """Whether an earlier-checked principal still holds: its credential is active with the same key, its session
        (for a cookie) is still open, and it still reads ledger `lid`. An open event stream asks this as it runs."""
        cred = self.by_id(principal.credential.id)
        secret = self.secret(principal.credential.id)
        if cred is None or secret is None or not hmac.compare_digest(secret, principal.secret):
            return False
        if principal.via == "cookie" and (principal.session is None or self.sessions.lookup(principal.session) is None):
            return False
        return lid is None or cred.reads(lid)


def cookie_value(raw_cookie_header: str | None) -> str | None:
    """translucent_session from a Cookie header."""
    if not raw_cookie_header:
        return None
    from http.cookies import CookieError, SimpleCookie
    jar = SimpleCookie()
    try:
        jar.load(raw_cookie_header)
    except CookieError:
        return None
    morsel = jar.get(SESSION_COOKIE)
    return morsel.value if morsel is not None and morsel.value else None
