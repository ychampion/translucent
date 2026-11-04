"""M2 authorization (docs: translucent/auth.py, M2 contract §1): identity, role and ledger scope come from the
credential, never from a request body. Role spoofing, cross-ledger reads and writes, unauthenticated reads, stale
approvals, browser sessions (cookie, CSRF header, restart, sign-out, rate limit), revoked keys, budget amendments,
planner readiness, idempotency per credential, the keys CLI and the team key a data-root server makes. Offline: the
toy cell only; no paid calls."""
import json
import os
import stat
import sys
import uuid

import pytest
from fastapi.testclient import TestClient
from support.keys import add_key, bearer
from support.loop_systems import PREDICTION, objective, register

from translucent import auth, budget, chain, cli, providers, runtime, server
from translucent.ledger import Ledger
from translucent.loop import current_objective
from translucent.workspace import init_ledger

KEYS = {"ana": "engineer-ana-0123456789abcdef", "ben": "engineer-ben-0123456789abcdef",
        "bot": "planner-bot-0123456789abcdef", "vic": "viewer-vic-0123456789abcdef",
        "root": "admin-root-0123456789abcdef"}
ANA, BEN, BOT, VIC, ROOT = (bearer(KEYS[k]) for k in ("ana", "ben", "bot", "vic", "root"))
A, B = "/api/v1/ledgers/lab-a", "/api/v1/ledgers/lab-b"
WEB = {"X-Translucent-Client": "web"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Paid model calls are forbidden in auth tests")
    monkeypatch.setattr(runtime, "_client", denied)
    for name in ("TRANSLUCENT_LIVE", "TRANSLUCENT_ADMIN_TOKEN", "TRANSLUCENT_TEAM_TOKEN", "TRANSLUCENT_CORS_ORIGINS",
                 "TRANSLUCENT_IMPORT_ROOT", "TRANSLUCENT_DATA_ROOT", "TRANSLUCENT_PROFILE", "TRANSLUCENT_STATE_DIR",
                 "TRANSLUCENT_ADMIN_NAME", "TRANSLUCENT_TEAM_NAME"):
        monkeypatch.delenv(name, raising=False)
    register(monkeypatch)


@pytest.fixture
def root(tmp_path):
    """A data root with ledgers a and b; Ana (engineer, every ledger), Ben (engineer, ledger a only), bot (planner),
    Vic (viewer) and Root (admin)."""
    data = tmp_path / "data"
    init_ledger(data, "lab-a", title="Lab A")
    init_ledger(data, "lab-b", title="Lab B")
    add_key(data, "Ana", "engineer", KEYS["ana"])
    add_key(data, "Ben", "engineer", KEYS["ben"], ledgers=["lab-a"])
    add_key(data, "bot", "planner", KEYS["bot"])
    add_key(data, "Vic", "viewer", KEYS["vic"])
    add_key(data, "Root", "admin", KEYS["root"])
    return data


def app(data, **kw):
    return server.create_app(data_root=data, poll_interval=0.01, loop_workers=0, **kw)


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def toy(**kw):
    return {k: v for k, v in objective(**kw).items() if k != "registered_by"}


def open_campaign(c, base=A, headers=ANA, **kw):
    return ok(c.post(f"{base}/campaigns", headers=headers, json={"title": "t", "objective": toy(**kw)}),
              201)["campaign"]["campaign_id"]


def proposal(**kw):
    return {"action": "simulate_charge", "params": {"current_A": 2.0, "ambient_C": 25.0}, "rationale": "r",
            "prediction": PREDICTION, "expected_effect": "e"} | kw


def detail(response):
    return response.json()["detail"]


# ------------------------------------------------------------------ role spoofing

def test_identity_comes_from_the_key_never_from_the_body(root):
    with TestClient(app(root)) as c:
        cid = open_campaign(c)
        # a planner key naming a person, an engineer key naming someone else or an agent: refused
        r = c.post(f"{A}/campaigns/{cid}/requests", headers=BOT, json=proposal(author="Ana"))
        assert r.status_code == 403 and detail(r) == "This key acts as bot"
        r = c.post(f"{A}/campaigns/{cid}/requests", headers=ANA, json=proposal(author="Ben"))
        assert r.status_code == 403 and detail(r) == "This key acts as Ana"
        r = c.post(f"{A}/campaigns/{cid}/requests", headers=ANA, json=proposal(agent="Ana"))
        assert r.status_code == 403 and detail(r) == "This key acts as Ana"
        r = c.post(f"{A}/campaigns", headers=ANA, json={"author": "Ben", "title": "t", "objective": toy()})
        assert r.status_code == 403
        r = c.post(f"{A}/campaigns", headers=ANA, json={"title": "t", "objective": {**toy(), "registered_by": "Ben"}})
        assert r.status_code == 403, r.text                     # nor registers an objective in someone else's name
        # without the optional fields, the key's identity is recorded
        mine = ok(c.post(f"{A}/campaigns/{cid}/requests", headers=BOT, json=proposal()), 201)
        assert mine["proposed_by"] == "agent:bot"
        named = ok(c.post(f"{A}/campaigns/{cid}/requests", headers=BOT,
                          json=proposal(agent="bot", params={"current_A": 3.0, "ambient_C": 25.0})), 201)
        assert named["proposed_by"] == "agent:bot"
        approved = ok(c.post(f"{A}/campaigns/{cid}/requests/{mine['request_id']}/approve", headers=ANA,
                             json={"content_hash": mine["content_hash"]}))
        assert approved["approved_by"] == "person:Ana"
    L = Ledger(root / "ledgers" / "lab-a")
    try:
        recs = chain.records(L, cid)
        assert recs[0].actor == "person:Ana" and recs[0].data["registered_by"] == "Ana"
        assert {r.actor for r in recs if r.type == "request"} == {"agent:bot"}
    finally:
        L.db.close()


def test_a_planner_key_never_does_a_persons_work(root):
    with TestClient(app(root)) as c:
        cid = open_campaign(c)
        req = ok(c.post(f"{A}/campaigns/{cid}/requests", headers=BOT, json=proposal()), 201)
        rq, h = req["request_id"], req["content_hash"]
        upload_id = uuid.uuid4().hex
        refused = [
            ("post", f"/campaigns/{cid}/requests/{rq}/approve", {"content_hash": h}),
            ("post", f"/campaigns/{cid}/requests/{rq}/reject", {"content_hash": h, "reason": "no"}),
            ("post", f"/campaigns/{cid}/requests/{rq}/revise", {"params": {"current_A": 1.0}, "reason": "r"}),
            ("post", f"/campaigns/{cid}/amendments", {"reason": "r", "budget": {"experiments": 2}}),
            ("post", f"/campaigns/{cid}/requests/{rq}/results", {"request_id": rq, "content_hash": h}),
            ("post", f"/campaigns/{cid}/conclusion", {"text": "done"}),
            ("post", f"/campaigns/{cid}/notes", {"text": "hello"}),
            ("post", "/campaigns", {"title": "t", "objective": toy()}),
            ("post", "/datasets", {"manifest": {}, "path": "x"}),
        ]
        for method, path, body in refused:
            r = getattr(c, method)(A + path, headers=BOT, json=body)
            assert r.status_code == 403 and "planner key cannot" in detail(r), (path, r.text)
        r = c.put(f"{A}/uploads/{upload_id}/run.csv", headers=BOT, content=b"t,T\n0,25\n")
        assert r.status_code == 403 and "planner key cannot upload" in detail(r)
        # nothing was written: the request still waits for a person
        assert ok(c.get(f"{A}/campaigns/{cid}/requests/{rq}", headers=BOT))["state"] == "awaiting_approval"
        # a planner may cancel its own request before approval
        assert ok(c.post(f"{A}/campaigns/{cid}/requests/{rq}/cancel", headers=BOT,
                         json={"reason": "not needed"}))["state"] == "cancelled"
        # a planner key cannot sign in to a browser session either
        r = c.post("/api/v1/session", json={"key": KEYS["bot"]})
        assert r.status_code == 403 and "set-cookie" not in r.headers


def test_a_viewer_reads_and_never_writes(root):
    with TestClient(app(root)) as c:
        cid = open_campaign(c)
        assert ok(c.get(f"{A}/campaigns/{cid}/view", headers=VIC))["campaign"]["id"] == cid
        assert [x["id"] for x in ok(c.get(f"{A}/campaigns", headers=VIC))["items"]] == [cid]
        for path, body in [(f"/campaigns/{cid}/notes", {"text": "x"}), ("/campaigns", {"title": "t",
                                                                                      "objective": toy()}),
                           (f"/campaigns/{cid}/requests", proposal())]:
            r = c.post(A + path, headers=VIC, json=body)
            assert r.status_code == 403 and detail(r) == "A viewer key can only read"
        assert c.put(f"{A}/uploads/{uuid.uuid4().hex}/f.csv", headers=VIC, content=b"x").status_code == 403
        r = c.post("/api/v1/session", json={"key": KEYS["vic"]})      # a viewer may browse
        assert ok(r)["role"] == "viewer"
        assert c.post(f"{A}/campaigns/{cid}/notes", headers=WEB, json={"text": "x"}).status_code == 403


# ------------------------------------------------------------------ budgets

def test_only_an_administrator_raises_a_budget(root):
    with TestClient(app(root)) as c:
        cid = open_campaign(c, budget={"model_usd": 0.0, "experiments": 8, "experiment_usd": 10.0,
                                       "max_wall_s": 300.0})
        for budget in ({"experiments": 9}, {"experiment_usd": 10.5}, {"model_usd": 0.5}, {"max_wall_s": 600.0},
                       {"experiments": 2, "model_usd": 1.0}):
            r = c.post(f"{A}/campaigns/{cid}/amendments", headers=ANA, json={"reason": "more", "budget": budget})
            assert r.status_code == 403 and "administrator" in detail(r), (budget, r.text)
        lowered = ok(c.post(f"{A}/campaigns/{cid}/amendments", headers=ANA,
                            json={"reason": "fewer", "budget": {"experiments": 4, "experiment_usd": 5.0}}))
        assert lowered["amendment"]["data"]["budget"] == {"experiments": 4, "experiment_usd": 5.0}
        raised = ok(c.post(f"{A}/campaigns/{cid}/amendments", headers=ROOT,
                           json={"reason": "a bigger screen", "budget": {"experiments": 12}}))
        assert raised["amendment"]["actor"] == "person:Root"
        # an administrator is held to the provider caps and to well-formed budgets
        r = c.post(f"{A}/campaigns/{cid}/amendments", headers=ROOT, json={"reason": "r", "budget": {"model_usd": 1e6}})
        assert r.status_code == 422 and "cap" in detail(r)
        for bad in ({"experiments": 2.5}, {"experiment_usd": -1}, {"tokens": 3}, {}):
            r = c.post(f"{A}/campaigns/{cid}/amendments", headers=ROOT, json={"reason": "r", "budget": bad})
            assert r.status_code == 422, (bad, r.text)
        # the planner's budget is spent against the amended one: 12 experiments, $5
        assert ok(c.get(f"{A}/campaigns/{cid}/view", headers=VIC))["campaign"]["id"] == cid
    L = Ledger(root / "ledgers" / "lab-a")
    try:
        obj = current_objective(L, cid)
        assert obj.budget == {"model_usd": 0.0, "experiments": 12, "experiment_usd": 5.0, "max_wall_s": 300.0}
        assert obj.version == 3
        assert chain.problems(L, cid) == []            # amendments without a budget keep their canonical form
        amendments = [r for r in chain.records(L, cid) if r.type == "amendment"]
        assert len(amendments) == 2 and all("budget" in r.data for r in amendments)   # refused ones are not kept
    finally:
        L.db.close()


def test_an_amendment_without_a_budget_stores_no_budget_field(root):
    with TestClient(app(root)) as c:
        cid = open_campaign(c)
        out = ok(c.post(f"{A}/campaigns/{cid}/amendments", headers=ANA, json={
            "reason": "Cooler", "constraints": [{"quantity": "max_temperature", "op": "<=", "limit": 40.0,
                                                 "unit": "degC", "label": "40"}]}))
        assert "budget" not in out["amendment"]["data"]


# ------------------------------------------------------------------ ledger scope

def test_a_key_scoped_to_one_ledger_cannot_see_or_touch_another(root):
    with TestClient(app(root)) as c:
        in_b = open_campaign(c, base=B)
        assert [x["id"] for x in ok(c.get("/api/v1/ledgers", headers=BEN))] == ["lab-a"]
        assert [x["id"] for x in ok(c.get("/api/v1/ledgers", headers=ANA))] == ["lab-a", "lab-b"]
        unknown = c.get("/api/v1/ledgers/zzz/campaigns", headers=BEN)
        for method, path, body in [("get", "/campaigns", None), ("get", f"/campaigns/{in_b}/view", None),
                                   ("get", f"/campaigns/{in_b}/events", None), ("get", "/datasets", None),
                                   ("get", "/systems", None), ("get", "/planners", None),
                                   ("get", f"/campaigns/{in_b}/export", None),
                                   ("post", f"/campaigns/{in_b}/notes", {"text": "x"}),
                                   ("post", "/campaigns", {"title": "t", "objective": toy()})]:
            r = getattr(c, method)(B + path, headers=BEN, **({"json": body} if body else {}))
            assert (r.status_code, r.json()) == (404, {"detail": "Ledger not found"}) == \
                (unknown.status_code, unknown.json()), (path, r.text)
        assert ok(c.get(f"{A}/campaigns", headers=BEN))["items"] == []
    L = Ledger(root / "ledgers" / "lab-b")
    try:
        assert L.events(in_b)[-1].type != "human.note"
    finally:
        L.db.close()


# ------------------------------------------------------------------ private reads

def test_every_read_needs_a_credential(root, tmp_path):
    with TestClient(app(root)) as c:
        cid = open_campaign(c)
        reads = ["/api/v1/ledgers", f"{A}/campaigns", f"{A}/campaigns/{cid}/view", f"{A}/campaigns/{cid}/events",
                 f"{A}/campaigns/{cid}/series?ref=x&quantity=temperature", f"{A}/artifacts/sha256:{'0' * 64}",
                 f"{A}/campaigns/{cid}/export", f"{A}/datasets", f"{A}/datasets/d", f"{A}/systems",
                 f"{A}/planners", f"{A}/campaigns/{cid}/requests/rq_{'0' * 20}", "/api/budget"]
        for path in reads:
            r = c.get(path)
            assert (r.status_code, r.json()) == (401, {"detail": "Sign in"}), path
            assert c.get(path, headers={"Authorization": "Bearer not-a-key-0123456789"}).status_code == 401
            c.app.state.translucent.auth.throttle.reset()
        session = ok(c.get("/api/session"))                         # public: booleans and the model's name
        assert session == {"live": False, "team": True, "model": "Muse Spark Open"}
        assert ok(c.get("/api/health")) == {"ok": True}
    legacy = tmp_path / "legacy"
    with TestClient(server.create_app(legacy / "ledger", legacy / "state", loop_workers=0)) as c:
        for path in ("/api/campaigns", "/api/campaigns/x", "/api/campaigns/x/events", "/api/campaigns/x/bundle",
                     "/api/campaigns/x/people", "/api/experiments", f"/api/artifacts/sha256:{'0' * 64}",
                     "/api/findings/x/evidence", "/api/compare?a=x&b=y", "/api/v1/ledgers/default/campaigns"):
            r = c.get(path)
            assert (r.status_code, r.json()) == (401, {"detail": "Sign in"}), path
        add_key(legacy / "state", "Ana", "engineer", KEYS["ana"])        # a one-ledger server's keys: its state
        assert ok(c.get("/api/campaigns", headers=ANA)) == []
        add_key(legacy / "state", "Ben", "engineer", KEYS["ben"], ledgers=["elsewhere"])
        assert c.get("/api/campaigns", headers=BEN).status_code == 404     # the legacy routes are ledger default


def test_errors_never_echo_a_key(root):
    with TestClient(app(root)) as c:
        secret = "wrong-but-secret-0123456789abcdefgh"
        for r in (c.get(f"{A}/campaigns", headers=bearer(secret)), c.post("/api/v1/session", json={"key": secret})):
            assert r.status_code == 401 and secret not in r.text
    text = (root / "credentials.json").read_text(encoding="utf-8")
    assert not any(key in text for key in KEYS.values())                  # only hashes are stored
    assert auth.digest(KEYS["ana"]) in text


# ------------------------------------------------------------------ stale approval

def test_a_stale_approval_is_still_a_409(root):
    with TestClient(app(root)) as c:
        cid = open_campaign(c)
        v1 = ok(c.post(f"{A}/campaigns/{cid}/requests", headers=BOT, json=proposal()), 201)
        rq = v1["request_id"]
        ok(c.post(f"{A}/campaigns/{cid}/requests/{rq}/revise", headers=ANA,
                  json={"params": {"current_A": 1.5}, "reason": "Rated 1.5 A"}))
        r = c.post(f"{A}/campaigns/{cid}/requests/{rq}/approve", headers=ANA, json={"content_hash": v1["content_hash"]})
        assert r.status_code == 409 and "review version 2" in r.text


# ------------------------------------------------------------------ browser sessions

def test_a_browser_signs_in_once_and_writes_with_the_csrf_header(root):
    with TestClient(app(root)) as c:
        cid = open_campaign(c)
        r = c.post("/api/v1/session", json={"key": KEYS["ana"]})
        body = ok(r)
        assert {k: body[k] for k in ("name", "role", "kind", "ledgers")} == \
            {"name": "Ana", "role": "engineer", "kind": "person", "ledgers": "*"} and body["expires_at"] > 0
        cookie = r.headers["set-cookie"]
        parts = {p.strip().split("=")[0].lower() for p in cookie.split(";")}
        assert cookie.startswith(f"{auth.SESSION_COOKIE}=") and {"httponly", "path", "max-age", "samesite"} <= parts
        assert "Path=/api" in cookie and "Max-Age=43200" in cookie and "samesite=strict" in cookie.lower()
        assert "secure" not in parts                                    # plain http: no Secure flag
        token = c.cookies.get(auth.SESSION_COOKIE)
        assert token and token not in (root / "state" / "sessions.json").read_text(encoding="utf-8")
        assert ok(c.get("/api/v1/session"))["name"] == "Ana"
        assert ok(c.get(f"{A}/campaigns/{cid}/view"))["campaign"]["id"] == cid     # the cookie reads
        # a cookie write without the client header is refused (CSRF), with it accepted as the key's person
        r = c.post(f"{A}/campaigns/{cid}/notes", json={"text": "from the browser"})
        assert r.status_code == 403 and "X-Translucent-Client" in detail(r)
        ok(c.post(f"{A}/campaigns/{cid}/notes", headers={"X-Translucent-Client": "other"}, json={"text": "x"}),
           403)
        note = ok(c.post(f"{A}/campaigns/{cid}/notes", headers=WEB, json={"text": "from the browser"}))
        assert note["actor"] == "person:Ana"
        # a bearer write needs no client header
        ok(c.post(f"{A}/campaigns/{cid}/notes", headers=BEN, json={"text": "by key"}))
        # sign out: the stored session and the cookie go
        r = c.post("/api/v1/session/end")
        assert r.status_code == 204 and auth.SESSION_COOKIE in r.headers.get("set-cookie", "")
        c.cookies.set(auth.SESSION_COOKIE, token, path="/api")          # a copy of the old cookie no longer works
        assert c.get("/api/v1/session").status_code == 401
        assert c.post(f"{A}/campaigns/{cid}/notes", headers=WEB, json={"text": "x"}).status_code == 401


def test_a_session_cookie_is_secure_over_https(root):
    with TestClient(app(root), base_url="https://testserver") as c:
        r = c.post("/api/v1/session", json={"key": KEYS["ana"]})
        assert "secure" in {p.strip().lower() for p in r.headers["set-cookie"].split(";")}
    with TestClient(app(root)) as c:      # a forwarded https header from a client the server does not trust
        r = c.post("/api/v1/session", json={"key": KEYS["ana"]}, headers={"X-Forwarded-Proto": "https"})
        assert "secure" not in {p.strip().lower() for p in r.headers["set-cookie"].split(";")}


def test_a_session_survives_a_server_restart(root):
    with TestClient(app(root)) as c:
        ok(c.post("/api/v1/session", json={"key": KEYS["ben"]}))
        token = c.cookies.get(auth.SESSION_COOKIE)
    with TestClient(app(root)) as c:
        c.cookies.set(auth.SESSION_COOKIE, token, path="/api")
        me = ok(c.get("/api/v1/session"))
        assert (me["name"], me["ledgers"]) == ("Ben", ["lab-a"])
        assert ok(c.get(f"{A}/campaigns"))["items"] == []
        assert c.get(f"{B}/campaigns").status_code == 404                 # the session keeps the key's scope


def test_a_revoked_key_and_its_sessions_stop_working(root):
    store = auth.CredentialStore(root / "credentials.json")
    ana = next(x for x in store.list() if x["name"] == "Ana")
    with TestClient(app(root)) as c:                                # a bearer header wins over the cookie
        ok(c.post("/api/v1/session", json={"key": KEYS["ana"]}))
        assert c.get(f"{A}/campaigns").status_code == 200              # by the cookie
        assert c.get(f"{A}/campaigns", headers=ANA).status_code == 200
        assert store.revoke(ana["id"]) and not store.revoke(ana["id"])
        r = c.get(f"{A}/campaigns", headers=ANA)
        assert (r.status_code, r.json()) == (401, {"detail": "Sign in"})
        assert c.get("/api/v1/session").status_code == 401
        assert c.get(f"{A}/campaigns").status_code == 401
        assert c.get(f"{A}/campaigns", headers=BEN).status_code == 200    # other keys are untouched


def test_failed_sign_ins_are_rate_limited(root):
    with TestClient(app(root)) as c:
        for n in range(auth.FAILED_SIGN_INS):
            assert c.post("/api/v1/session", json={"key": f"guess-{n:030d}"}).status_code == 401
        r = c.post("/api/v1/session", json={"key": "guess-again-0123456789"})
        assert r.status_code == 429 and r.headers["Retry-After"]
        # a key that matches is never refused (review F1): only the wrong ones are
        assert c.post("/api/v1/session", json={"key": KEYS["ana"]}).status_code == 200
        c.cookies.clear()                                               # the rest of this test sends no session
        assert c.get(f"{A}/campaigns", headers=ANA).status_code == 200
        assert c.post("/api/v1/session", json={"key": "guess-once-more-0123456789"}).status_code == 429
        assert c.get(f"{A}/campaigns", headers=bearer("guess-0123456789-y")).status_code == 429
        c.app.state.translucent.auth.throttle.reset()
        assert c.get(f"{A}/campaigns", headers=ANA).status_code == 200
        for n in range(auth.FAILED_SIGN_INS):                           # wrong bearer keys count too
            assert c.get(f"{A}/campaigns", headers=bearer(f"guess-{n:030d}")).status_code == 401
        assert c.get(f"{A}/campaigns", headers=bearer("guess-0123456789-x")).status_code == 429
        assert c.get(f"{A}/campaigns").status_code == 401             # no key presented: not a guess


def test_the_cors_policy_names_origins_and_allows_the_session(root, monkeypatch):
    monkeypatch.setenv("TRANSLUCENT_CORS_ORIGINS", "http://localhost:3000")
    with TestClient(app(root)) as c:
        r = c.options(f"{A}/campaigns", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method":
                                                 "POST", "Access-Control-Request-Headers": "x-translucent-client"})
        assert r.status_code == 200 and r.headers["access-control-allow-credentials"] == "true"
        assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
        r = c.get(f"{A}/campaigns", headers={"Origin": "http://evil.example"} | ANA)
        assert "access-control-allow-origin" not in r.headers
    monkeypatch.setenv("TRANSLUCENT_CORS_ORIGINS", "*")
    with pytest.raises(ValueError):
        app(root)


# ------------------------------------------------------------------ idempotency per credential

def test_idempotency_replies_are_per_credential(root):
    with TestClient(app(root)) as c:
        cid = open_campaign(c)
        key = {"Idempotency-Key": "note-1"}
        first = ok(c.post(f"{A}/campaigns/{cid}/notes", headers=ANA | key, json={"text": "once"}))
        ok(c.post("/api/v1/session", json={"key": KEYS["ana"]}))
        again = ok(c.post(f"{A}/campaigns/{cid}/notes", headers=WEB | key, json={"text": "once"}))   # the cookie
        assert again == first                         # one credential, one slot, whichever way it signs in
        c.cookies.clear()
        other = ok(c.post(f"{A}/campaigns/{cid}/notes", headers=BEN | key, json={"text": "once"}))
        assert other["seq"] != first["seq"] and other["actor"] == "person:Ben"   # never another's reply
    L = Ledger(root / "ledgers" / "lab-a")
    try:
        assert [e.actor for e in L.events(cid) if e.type == "human.note"] == ["person:Ana", "person:Ben"]
    finally:
        L.db.close()


# ------------------------------------------------------------------ planners: readiness, never a substitute

def test_planner_readiness_and_no_substitution(root, monkeypatch):
    with TestClient(app(root)) as c:
        def muse(headers):
            listed = ok(c.get(f"{A}/planners", headers=headers))
            assert [p["id"] for p in listed] == ["bo", "external", "muse"]
            return {p["id"]: p for p in listed}

        mine = muse(ANA)
        assert mine["bo"]["ready"] and mine["external"]["ready"] and not mine["bo"]["paid"]
        m = mine["muse"]
        assert m["paid"] and not m["ready"] and "not admin" in m["reason"]
        assert m["model"] == runtime.PROFILES[m["profile"]]["orchestrator"][1]
        assert m["cap_usd"] > 0 and m["remaining_usd"] <= m["cap_usd"] and m["default_budget_usd"] == 1.5
        # each provider's hard cap is reported apart, never summed into one figure
        rows = {p["provider"]: p for p in m["providers"]}
        # the planner calls the profile's orchestrator only, so only its provider is reported and checked
        assert list(rows) == [runtime.PROFILES[m["profile"]]["orchestrator"][0]]
        assert m["cap_usd"] == rows[runtime.PROFILES[m["profile"]]["orchestrator"][0]]["cap_usd"]
        assert all(r["cap_usd"] == budget.CAPS_USD[name] for name, r in rows.items())
        assert not muse(VIC)["bo"]["ready"] and not muse(BOT)["external"]["ready"]
        assert "live mode is off" in muse(ROOT)["muse"]["reason"]
        paid = {"title": "t", "objective": toy(planner={"kind": "muse", "profile": None, "client": None},
                                               budget={"model_usd": 1.5, "experiments": 4})}
        r = c.post(f"{A}/campaigns", headers=ANA, json=paid)
        assert r.status_code == 403 and "not admin" in detail(r)
        r = c.post(f"{A}/campaigns", headers=ROOT, json=paid)
        assert r.status_code == 403 and "live mode is off" in detail(r)
        monkeypatch.setenv("TRANSLUCENT_LIVE", "1")
        key = providers.KEY_VARS[runtime.PROFILES[m["profile"]]["orchestrator"][0]][0]
        other = next(v[0] for p, v in providers.KEY_VARS.items() if v[0] != key)
        monkeypatch.setattr(server, "_missing_keys", lambda profile: [other])
        assert muse(ROOT)["muse"]["ready"]          # a key the planner never uses does not hold it back
        monkeypatch.setattr(server, "_missing_keys", lambda profile: [key, other])
        assert key in muse(ROOT)["muse"]["reason"] and other not in muse(ROOT)["muse"]["reason"]
        r = c.post(f"{A}/campaigns", headers=ROOT, json=paid)
        assert r.status_code == 503 and key in detail(r)
        assert ok(c.get(f"{A}/campaigns", headers=ROOT))["items"] == []     # nothing opened, nothing substituted
        monkeypatch.setattr(server, "_missing_keys", lambda profile: [])
        ready = muse(ROOT)["muse"]
        assert ready["ready"] and ready["reason"] is None
        opened = ok(c.post(f"{A}/campaigns", headers=ROOT, json=paid), 201)
        assert opened["campaign"]["objective"]["data"]["planner"]["kind"] == "muse"
        assert opened["campaign"]["objective"]["data"]["registered_by"] == "Root"
        assert ok(c.get("/api/session"))["live"] is True


def test_paid_video_runs_need_an_administrator(root, tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    add_key(legacy / "state", "Ana", "engineer", KEYS["ana"])
    add_key(legacy / "state", "Root", "admin", KEYS["root"])
    body = {"objective": "o", "experiment_id": "x", "budget": 1.0}
    with TestClient(server.create_app(legacy / "ledger", legacy / "state", loop_workers=0)) as c:
        r = c.post("/api/campaigns", headers=ANA, json=body)
        assert r.status_code == 403 and "engineer key cannot start a paid run" in detail(r)
        r = c.post("/api/campaigns", headers=ROOT, json=body)
        assert r.status_code == 403 and "Live campaigns are off" in detail(r)
        r = c.post("/api/campaigns/x/resume", headers=ANA, json={"budget": 1.0})
        assert r.status_code == 403
        monkeypatch.setenv("TRANSLUCENT_LIVE", "1")
        monkeypatch.setattr(server, "_missing_keys", lambda profile: ["FIREWORKS_API_KEY"])
        r = c.post("/api/campaigns", headers=ROOT, json=body)
        assert r.status_code == 503 and "FIREWORKS_API_KEY" in detail(r)
        r = c.post("/api/campaigns", headers=ROOT, json=body | {"protocol": {"author": "Ana", "metrics": [
            {"name": "m", "quantities": ["q"], "unit": "s", "test": {"type": "trend", "params": {}}}]}})
        assert r.status_code == 403 and detail(r) == "This key acts as Root"


def test_legacy_peoples_writes_use_the_keys_person(tmp_path):
    legacy = tmp_path / "legacy"
    add_key(legacy / "state", "Ana", "engineer", KEYS["ana"])
    add_key(legacy / "state", "bot", "planner", KEYS["bot"])
    with TestClient(server.create_app(legacy / "ledger", legacy / "state", loop_workers=0)) as c:
        for path, body in (("/api/campaigns/x/control", {"action": "pause"}),
                           ("/api/campaigns/x/answers", {"question_id": "q", "text": "t"}),
                           ("/api/campaigns/x/reviews", {"claim_id": "k", "verdict": "agree"})):
            r = c.post(path, headers=BOT, json=body)
            assert r.status_code == 403 and "planner key cannot" in detail(r), (path, r.text)
        r = c.post("/api/campaigns/x/notes", headers=BOT, json={"text": "t"})
        assert r.status_code == 403 and "planner key cannot act as a person" in detail(r)
        r = c.post("/api/campaigns/x/notes", headers=ANA, json={"author": "Ben", "text": "t"})
        assert r.status_code == 403 and detail(r) == "This key acts as Ana"
        r = c.post("/api/campaigns/x/notes", headers=ANA, json={"text": "t"})
        assert r.status_code == 404                                     # past authorization: no such campaign
        r = c.put(f"/api/uploads/{uuid.uuid4().hex}/v.mp4", headers=BOT, content=b"x")
        assert r.status_code == 403


# ------------------------------------------------------------------ keys on disk and the CLI

def test_the_keys_command_shows_a_key_once_and_stores_its_hash(tmp_path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    assert cli.main(["keys", "add", "--name", "Ana", "--role", "engineer", "--data-root", str(data)]) == 0
    out = capsys.readouterr().out
    key = out.split("Key (shown once, only its hash is kept): ")[1].split()[0]
    assert len(key) >= 43                                            # 32 random bytes, url-safe
    cred_id = out.split("Added ")[1].split(":")[0]
    stored = (data / "credentials.json").read_text(encoding="utf-8")
    assert key not in stored and auth.digest(key) in stored
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(data / "credentials.json").st_mode) == 0o600
    assert cli.main(["keys", "add", "--name", "lab-bot", "--role", "planner", "--ledger", "lab-a", "--ledger", "lab-b",
                     "--data-root", str(data)]) == 0
    capsys.readouterr()
    assert cli.main(["keys", "list", "--data-root", str(data)]) == 0
    listing = capsys.readouterr().out
    assert "Ana" in listing and "lab-bot" in listing and "ledgers lab-a,lab-b" in listing and key not in listing
    store = auth.CredentialStore(data / "credentials.json")
    assert store.find(key).name == "Ana"
    assert cli.main(["keys", "revoke", cred_id, "--data-root", str(data)]) == 0
    assert store.find(key) is None
    assert cli.main(["keys", "revoke", cred_id, "--data-root", str(data)]) == 1
    with pytest.raises(SystemExit):
        cli.main(["keys", "add", "--name", "x", "--role", "owner", "--data-root", str(data)])
    with pytest.raises(SystemExit):
        cli.main(["keys", "add", "--name", "x", "--role", "viewer", "--ledger", "../up", "--data-root", str(data)])
    capsys.readouterr()


def test_a_data_root_server_makes_one_engineer_key_for_its_person(tmp_path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    (data / "team-key").write_text("an-older-shorter-key\n", encoding="utf-8")   # from before credentials existed
    cli._team_server(data, "Ana")
    out = capsys.readouterr().out
    key = (data / "team-key").read_text(encoding="utf-8").strip()
    assert key != "an-older-shorter-key" and len(key) >= 43 and key in out and "Ana" in out
    assert "TRANSLUCENT_TEAM_TOKEN" not in os.environ
    cred = auth.CredentialStore(data / "credentials.json").find(key)
    assert (cred.name, cred.role, cred.ledgers) == ("Ana", "engineer", "*")
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(data / "team-key").st_mode) == 0o600
    cli._team_server(data, "Ben")                                   # once: a second start makes and shows nothing
    again = capsys.readouterr().out
    assert key not in again and "1 active" in again
    assert len(auth.CredentialStore(data / "credentials.json").list()) == 1


def test_env_tokens_are_an_administrator_and_an_engineer(root, monkeypatch):
    monkeypatch.setenv("TRANSLUCENT_ADMIN_TOKEN", "env-admin-0123456789abcdef-32chars-minimum")
    monkeypatch.setenv("TRANSLUCENT_TEAM_TOKEN", "env-team-0123456789abcdef-32chars-minimum")
    monkeypatch.setenv("TRANSLUCENT_TEAM_NAME", "Lab team")
    with TestClient(app(root)) as c:
        team = ok(c.post("/api/v1/session", json={"key": "env-team-0123456789abcdef-32chars-minimum"}))
        assert (team["name"], team["role"]) == ("Lab team", "engineer")
        admin = ok(c.post("/api/v1/session", json={"key": "env-admin-0123456789abcdef-32chars-minimum"}))
        assert (admin["name"], admin["role"]) == ("administrator", "admin")
        monkeypatch.delenv("TRANSLUCENT_ADMIN_TOKEN")            # unset: its sessions end with it
        assert c.get("/api/v1/session").status_code == 401


def test_credentials_reject_bad_input(tmp_path):
    store = auth.CredentialStore(tmp_path / "credentials.json")
    for name, role, ledgers in [("", "engineer", "*"), ("a\nb", "engineer", "*"), ("x" * 81, "engineer", "*"),
                                ("Ana", "root", "*"), ("Ana", "viewer", []), ("Ana", "viewer", ["a/b"])]:
        with pytest.raises(ValueError):
            store.add(name, role, ledgers)
    with pytest.raises(ValueError):
        store.add("Ana", "engineer", "*", key="short")
    assert not store.exists() and store.find("anything") is None
    cred, key = store.add("Ana", "engineer", ["lab-b", "lab-a", "lab-a"])
    assert cred.ledgers == ("lab-a", "lab-b") and cred.reads("lab-a") and not cred.reads("lab-c")
    assert json.loads((tmp_path / "credentials.json").read_text())["credentials"][0]["ledgers"] == ["lab-a", "lab-b"]
