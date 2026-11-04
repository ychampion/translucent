"""Regressions for the authorization review's findings 1-8: the
failed-key throttle never refuses a key that matches and forgets idle addresses; the deploy chain trusts only its own
proxy hops; GET /api/v1/session goes through the throttle; a browser session ends when its credential's secret
changes; an open event stream closes when its credential or session ends; a path import is confined to the ledger's
own folder; the session file stays bounded and is not re-read per request; the API docs are off unless asked for.
Offline: the toy cell only; no paid calls."""
import ipaddress
import json
from pathlib import Path
import re
import shutil
import threading
import types

import pytest
import yaml
from fastapi.testclient import TestClient
from support.keys import add_key, bearer
from support.loop_systems import objective, register

from translucent import auth, runtime, server
from translucent.workspace import init_ledger

REPO = Path(__file__).resolve().parents[1]
DATA = Path(__file__).parent / "data" / "import_v2"
KEYS = {"ana": "engineer-ana-0123456789abcdef", "ben": "engineer-ben-0123456789abcdef",
        "bot": "planner-bot-0123456789abcdef", "eve": "viewer-eve-0123456789abcdef"}
ANA, BEN, BOT, EVE = (bearer(KEYS[k]) for k in ("ana", "ben", "bot", "eve"))
A, B = "/api/v1/ledgers/lab-a", "/api/v1/ledgers/lab-b"
WEB = {"X-Translucent-Client": "web"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Paid model calls are forbidden in auth tests")
    monkeypatch.setattr(runtime, "_client", denied)
    for name in ("TRANSLUCENT_LIVE", "TRANSLUCENT_ADMIN_TOKEN", "TRANSLUCENT_TEAM_TOKEN", "TRANSLUCENT_CORS_ORIGINS",
                 "TRANSLUCENT_IMPORT_ROOT", "TRANSLUCENT_DATA_ROOT", "TRANSLUCENT_PROFILE", "TRANSLUCENT_STATE_DIR",
                 "TRANSLUCENT_ADMIN_NAME", "TRANSLUCENT_TEAM_NAME", "TRANSLUCENT_API_DOCS"):
        monkeypatch.delenv(name, raising=False)
    register(monkeypatch)


@pytest.fixture
def root(tmp_path):
    data = tmp_path / "data"
    init_ledger(data, "lab-a", title="Lab A")
    init_ledger(data, "lab-b", title="Lab B")
    add_key(data, "Ana", "engineer", KEYS["ana"])
    add_key(data, "Ben", "engineer", KEYS["ben"], ledgers=["lab-a"])
    add_key(data, "bot", "planner", KEYS["bot"])
    add_key(data, "Eve", "viewer", KEYS["eve"])
    return data


def app(data, **kw):
    return server.create_app(data_root=data, poll_interval=0.01, loop_workers=0, **kw)


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def open_campaign(c, base=A, headers=ANA):
    toy = {k: v for k, v in objective().items() if k != "registered_by"}
    return ok(c.post(f"{base}/campaigns", headers=headers, json={"title": "t", "objective": toy}),
              201)["campaign"]["campaign_id"]


def guess(n):
    return bearer(f"guess-{n:030d}")


# ------------------------------------------------------------------ F1: a matching key is never throttled

def test_a_matching_key_works_from_a_throttled_address(root):
    with TestClient(app(root)) as c:
        for n in range(auth.FAILED_SIGN_INS):
            assert c.get(f"{A}/campaigns", headers=guess(n)).status_code == 401
        r = c.get(f"{A}/campaigns", headers=guess(99))
        assert r.status_code == 429 and r.headers["Retry-After"]                # more wrong keys: refused
        assert c.get(f"{A}/campaigns", headers=ANA).status_code == 200          # the right key still works
        assert c.get("/api/v1/ledgers", headers=BOT).status_code == 200         # an agent's key too
        assert ok(c.post("/api/v1/session", json={"key": KEYS["ana"]}))["name"] == "Ana"   # and a sign-in
        assert c.post("/api/v1/session", json={"key": "guess-again-0123456789"}).status_code == 429
        assert c.get(f"{A}/campaigns", headers=guess(100)).status_code == 429


# ------------------------------------------------------------------ F2: the throttle forgets idle addresses

def test_the_throttle_forgets_idle_addresses_and_caps_its_table(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(auth, "time", types.SimpleNamespace(monotonic=lambda: clock[0], time=lambda: clock[0],
                                                            sleep=lambda s: None))
    throttle = auth.Throttle()
    for i in range(50):
        throttle.fail(f"10.0.0.{i}")
    clock[0] += auth.FAILED_WINDOW_S + 1
    throttle.fail("10.0.1.1")
    assert set(throttle._failures) == {"10.0.1.1"}          # stale entries go, not only empty ones
    for i in range(10_050):                                  # many fresh addresses inside one window
        throttle.fail(f"10.{i // 65536}.{i // 256 % 256}.{i % 256}")
    assert len(throttle._failures) <= 10_000


# ------------------------------------------------------------------ F2: the deploy chain trusts its own hops only

def _deploy():
    compose = yaml.safe_load((REPO / "deploy" / "compose.yaml").read_text(encoding="utf-8"))
    caddyfile = (REPO / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    return compose, caddyfile


def test_the_deploy_chain_trusts_only_its_proxy_hops():
    compose, caddyfile = _deploy()
    services = compose["services"]
    (net_name, web_net), = services["web"]["networks"].items()
    caddy_ip = ipaddress.ip_address(web_net["ipv4_address"])
    (pool,) = compose["networks"][net_name]["ipam"]["config"]
    subnet, gateway = ipaddress.ip_network(pool["subnet"]), ipaddress.ip_address(pool["gateway"])
    assert caddy_ip in subnet and gateway in subnet
    # uvicorn trusts the Caddy container's address and nothing else
    allowed = [x.strip() for x in services["api"]["environment"]["FORWARDED_ALLOW_IPS"].split(",")]
    assert allowed == [str(caddy_ip)]
    # Caddy trusts only the bridge gateway (where a front door on this host connects), reads the client address
    # right to left, and sends the API one address it resolved itself rather than the client's list
    assert "private_ranges" not in caddyfile
    assert re.search(r"^\s*trusted_proxies\s+static\s+(\S+)\s*$", caddyfile, re.M).group(1) == str(gateway)
    assert re.search(r"^\s*trusted_proxies_strict\s*$", caddyfile, re.M)
    assert re.search(r"^\s*header_up\s+X-Forwarded-For\s+\{client_ip\}\s*$", caddyfile, re.M)


@pytest.mark.parametrize("peer,xff,seen", [
    ("caddy", "203.0.113.7", "203.0.113.7"),          # Caddy's own header is believed
    ("10.8.0.5", "1.2.3.4", "10.8.0.5"),              # a LAN or VPN client cannot choose its address
    ("172.16.0.9", "10.8.0.9", "172.16.0.9"),
    ("192.168.1.4", "1.2.3.4, 192.168.1.4", "192.168.1.4"),
    ("gateway", "1.2.3.4", "gateway"),                 # the gateway is Caddy's hop to trust, not uvicorn's
])
def test_uvicorn_resolves_the_client_address_from_the_deploy_setting(peer, xff, seen):
    import asyncio
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    compose, _ = _deploy()
    names = {}
    if peer in ("caddy", "gateway"):            # the LAN cases need only the uvicorn setting
        (net_name, web_net), = compose["services"]["web"]["networks"].items()
        names = {"caddy": web_net["ipv4_address"],
                 "gateway": compose["networks"][net_name]["ipam"]["config"][0]["gateway"]}
    got = {}

    async def inner(scope, receive, send):
        got["client"] = scope["client"][0]
    middleware = ProxyHeadersMiddleware(inner, trusted_hosts=compose["services"]["api"]["environment"]
                                        ["FORWARDED_ALLOW_IPS"])
    scope = {"type": "http", "scheme": "http", "client": (names.get(peer, peer), 5000),
             "headers": [(b"x-forwarded-for", xff.encode())]}
    asyncio.run(middleware(scope, None, None))
    assert got["client"] == names.get(seen, seen)


# ------------------------------------------------------------------ F3: GET /api/v1/session is throttled too

def test_reading_the_session_with_a_key_goes_through_the_throttle(root):
    with TestClient(app(root)) as c:
        for n in range(auth.FAILED_SIGN_INS):
            r = c.get("/api/v1/session", headers=guess(n))
            assert (r.status_code, r.json()) == (401, {"detail": "Sign in"})
        assert c.get("/api/v1/session", headers=guess(99)).status_code == 429
        assert c.get(f"{A}/campaigns", headers=guess(100)).status_code == 429    # one count for every route
        assert ok(c.get("/api/v1/session", headers=ANA))["name"] == "Ana"       # the right key still answers
        c.app.state.translucent.auth.throttle.reset()
        ok(c.post("/api/v1/session", json={"key": KEYS["eve"]}))
        assert ok(c.get("/api/v1/session"))["name"] == "Eve"                    # by the cookie, as before
        c.cookies.clear()
        assert c.get("/api/v1/session").status_code == 401


# ------------------------------------------------------------------ F4: a session is bound to the credential's secret

def test_rotating_an_env_token_ends_its_sessions(root, monkeypatch):
    old, new = "old-leaked-admin-token-0123456789", "new-rotated-admin-token-9876543210"
    monkeypatch.setenv("TRANSLUCENT_ADMIN_TOKEN", old)
    with TestClient(app(root)) as c:
        assert ok(c.post("/api/v1/session", json={"key": old}))["role"] == "admin"
        token = c.cookies.get(auth.SESSION_COOKIE)
        assert ok(c.get("/api/v1/session"))["role"] == "admin"
        monkeypatch.setenv("TRANSLUCENT_ADMIN_TOKEN", new)
        assert c.get("/api/v1/session").status_code == 401
        assert c.get(f"{A}/campaigns").status_code == 401
        assert c.get(f"{A}/campaigns", headers=bearer(old)).status_code == 401
        assert ok(c.post("/api/v1/session", json={"key": new}))["role"] == "admin"   # the new token signs in
        assert ok(c.get("/api/v1/session"))["role"] == "admin"
    with TestClient(app(root)) as c:                                         # and a restart does not revive it
        c.cookies.set(auth.SESSION_COOKIE, token, path="/api")
        assert c.get("/api/v1/session").status_code == 401


def test_replacing_a_stored_key_ends_its_sessions(root):
    path = root / "credentials.json"
    with TestClient(app(root)) as c:
        ok(c.post("/api/v1/session", json={"key": KEYS["ana"]}))
        assert c.get(f"{A}/campaigns").status_code == 200
        body = json.loads(path.read_text(encoding="utf-8"))
        entry = next(e for e in body["credentials"] if e["name"] == "Ana")
        entry["key_sha256"] = auth.digest("engineer-ana-replaced-0123456789")      # same id, a new key
        path.write_text(json.dumps(body), encoding="utf-8")
        assert c.get(f"{A}/campaigns").status_code == 401
        assert c.get("/api/v1/session").status_code == 401
        assert c.get(f"{A}/campaigns", headers=bearer("engineer-ana-replaced-0123456789")).status_code == 200


# ------------------------------------------------------------------ F5: an open stream closes when its access ends

def _watch(c, url, opened, got, ended, headers=None):
    def reader():
        with c.stream("GET", url, headers=headers or {}) as s:
            for line in s.iter_lines():
                if line.startswith("data:"):
                    opened.set()
                if "after the access ended" in line:
                    got.append(line)
                    return
        ended.set()
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    assert opened.wait(10), "the stream sent nothing"
    return t


@pytest.mark.parametrize("how", ["revoked-key", "ended-session"])
def test_an_open_event_stream_closes_when_its_access_ends(root, monkeypatch, how):
    monkeypatch.setattr(server, "STREAM_RECHECK_S", 0.0, raising=False)
    store = auth.CredentialStore(root / "credentials.json")
    with TestClient(app(root)) as c:
        cid = open_campaign(c)
        ok(c.post(f"{A}/campaigns/{cid}/notes", headers=ANA, json={"text": "before"}))
        opened, ended, got = threading.Event(), threading.Event(), []
        url = f"{A}/campaigns/{cid}/events"
        if how == "revoked-key":
            t = _watch(c, url, opened, got, ended, headers=EVE)
            assert store.revoke(next(x for x in store.list() if x["name"] == "Eve")["id"])
        else:
            ok(c.post("/api/v1/session", json={"key": KEYS["eve"]}))
            token = c.cookies.get(auth.SESSION_COOKIE)
            t = _watch(c, url, opened, got, ended)
            c.app.state.translucent.auth.sessions.end(token)                  # signed out in another tab
        ok(c.post(f"{A}/campaigns/{cid}/notes", headers=ANA, json={"text": "after the access ended"}))
        t.join(10)
        assert not got, "the stream delivered a note after its access ended"
        assert ended.is_set(), "the stream stayed open"


def test_an_open_event_stream_stays_open_while_access_holds(root, monkeypatch):
    monkeypatch.setattr(server, "STREAM_RECHECK_S", 0.0, raising=False)
    with TestClient(app(root)) as c:
        cid = open_campaign(c)
        ok(c.post(f"{A}/campaigns/{cid}/notes", headers=ANA, json={"text": "before"}))
        opened, ended, got = threading.Event(), threading.Event(), []
        t = _watch(c, f"{A}/campaigns/{cid}/events", opened, got, ended, headers=EVE)
        ok(c.post(f"{A}/campaigns/{cid}/notes", headers=ANA, json={"text": "after the access ended"}))
        t.join(10)
        assert got and not ended.is_set()


# ------------------------------------------------------------------ F6: a path import reads the ledger's own folder

@pytest.fixture
def staged(tmp_path):
    """An import root laid out per ledger: <root>/lab-b/bench holds a dataset staged for lab-b only."""
    folder = tmp_path / "imports"
    shutil.copytree(DATA, folder / "lab-b" / "bench")
    return folder, json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))


def test_a_path_import_reads_only_the_ledgers_own_folder(root, staged):
    folder, manifest = staged
    with TestClient(app(root, import_root=folder)) as c:
        for path in ("lab-b/bench", str(folder / "lab-b" / "bench"), "bench", "../lab-b/bench"):
            r = c.post(f"{A}/datasets", headers=BEN, json={"manifest": manifest, "path": path})
            assert r.status_code == 403, (path, r.text)
            assert "not a file in the import folder" in r.json()["detail"]     # one answer, no probing
        assert ok(c.get(f"{A}/datasets", headers=BEN)) == []
        made = ok(c.post(f"{B}/datasets", headers=ANA, json={"manifest": manifest, "path": "bench"}), 201)
        assert made["id"] == manifest["id"]
        r = c.post(f"{B}/datasets", headers=ANA, json={"manifest": manifest, "path": "lab-b/bench"})
        assert r.status_code == 403 and "not a file in the import folder" in r.json()["detail"]


# ------------------------------------------------------------------ F7: the session file stays bounded and cached

def test_the_session_file_is_bounded_and_not_reread_per_request(root, monkeypatch):
    sessions = root / "state" / "sessions.json"
    with TestClient(app(root)) as c:
        first = None
        for n in range(30):
            ok(c.post("/api/v1/session", json={"key": KEYS["eve"]}))
            first = first or c.cookies.get(auth.SESSION_COOKIE)
        stored = json.loads(sessions.read_text(encoding="utf-8"))["sessions"]
        assert 0 < len(stored) <= 20                                          # the oldest are dropped
        assert ok(c.get("/api/v1/session"))["name"] == "Eve"                  # the newest works
        reads, real = [], Path.read_text

        def counting(self, *a, **kw):
            if Path(self).name == "sessions.json":
                reads.append(1)
            return real(self, *a, **kw)
        monkeypatch.setattr(Path, "read_text", counting)
        for _ in range(25):
            assert c.get("/api/v1/session").status_code == 200
        assert len(reads) <= 1, f"sessions.json was read {len(reads)} times for 25 requests"
        monkeypatch.undo()
        c.cookies.clear()
        c.cookies.set(auth.SESSION_COOKIE, first, path="/api")
        assert c.get("/api/v1/session").status_code == 401                    # the oldest session ended


# ------------------------------------------------------------------ Info: the API docs are off unless asked for

def test_the_api_docs_are_off_by_default(root, monkeypatch):
    with TestClient(app(root)) as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert c.get(path).status_code == 404, path
    monkeypatch.setenv("TRANSLUCENT_API_DOCS", "1")
    with TestClient(app(root)) as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert c.get(path).status_code == 200, path


@pytest.mark.parametrize("name", ["TRANSLUCENT_ADMIN_TOKEN", "TRANSLUCENT_TEAM_TOKEN"])
def test_a_short_env_token_is_refused_at_startup(tmp_path, monkeypatch, name):
    """A correct key is never throttled, so a guessable env token would be the weak point."""
    from translucent import server
    monkeypatch.setenv(name, "short-token")
    with pytest.raises(ValueError, match=f"{name} must be at least 32 characters"):
        server.create_app(tmp_path / "ledger", tmp_path / "state")
