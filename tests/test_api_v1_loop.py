"""S5: loop campaigns over HTTP (/api/v1/ledgers/{lid}/...). A person and an agent's client complete a campaign with
a computational and a physical request; every refusal of the loop service is its HTTP status; writes need the team
token and people's actions a person; budgets are held to the provider caps; every write honours Idempotency-Key
across a server restart, and the record-writing ones even with the API's state lost. Offline: the toy cell only."""
import asyncio
import json
import shutil
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from support.analytic_system import simulate
from support.loop_systems import PREDICTION, objective, register

from translucent import chain, runtime, server
from translucent.ledger import Ledger
from translucent.verify import verify
from translucent.workspace import init_ledger

DATA = Path(__file__).parent / "data" / "import_v2"
TEAM = {"Authorization": "Bearer team-secret"}
ADMIN = {"Authorization": "Bearer test-administrator"}
BASE = "/api/v1/ledgers/lab"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Paid model calls are forbidden in API tests")
    monkeypatch.setattr(runtime, "_client", denied)
    for name in ("TRANSLUCENT_LIVE", "TRANSLUCENT_ADMIN_TOKEN", "TRANSLUCENT_CORS_ORIGINS", "TRANSLUCENT_IMPORT_ROOT",
                 "TRANSLUCENT_DATA_ROOT", "TRANSLUCENT_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRANSLUCENT_TEAM_TOKEN", "team-secret")
    register(monkeypatch)


@pytest.fixture
def lab(tmp_path):
    data = tmp_path / "data"
    init_ledger(data, "lab", title="Battery lab")
    imports = tmp_path / "imports"
    shutil.copytree(DATA, imports / "bench", ignore=shutil.ignore_patterns("v1"))
    return data, imports


def app(lab, **kw):
    data, imports = lab
    return server.create_app(data_root=data, import_root=imports, poll_interval=0.01, **kw)


def toy(**kw):
    return {k: v for k, v in objective(**kw).items() if k != "registered_by"}


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def bench_upload(c, req, *, temp_unit="degC", key=None):
    """Upload a complete bench result for 2 A / 25 degC (the analytic cell's own curve) and return it."""
    t, y, charge = simulate(2.0, 25.0)
    upload_id = uuid.uuid4().hex
    csv = "# logger export\ntime_s,T\n" + "".join(f"{a!r},{b!r}\n" for a, b in zip(t, y))
    ok(c.put(f"{BASE}/uploads/{upload_id}/run.csv", content=csv.encode(), headers=TEAM), 201)
    body = {"upload_id": upload_id, "request_id": req["request_id"], "content_hash": req["content_hash"],
            "executed_configuration": {"current_A": 2000.0, "ambient_C": 25.0},
            "executed_units": {"current_A": "mA", "ambient_C": "degC"}, "specimen_id": "cell-07",
            "table": {"file": "run.csv", "header_row": 1, "time_column": "time_s",
                      "columns": {"T": {"quantity": "temperature", "unit": temp_unit}}},
            "time": {"unit": "s", "origin": "charger switched on", "source": "logger clock"},
            "scalars": [{"quantity": "max_temperature", "value": max(y), "unit": "degC", "source": "logger peak"},
                        {"quantity": "charge_time_80", "value": charge, "unit": "s", "source": "charger log"}],
            "author": "Ana"}
    return c.post(f"{BASE}/campaigns/{body_cid(req)}/requests/{req['request_id']}/results", json=body,
                  headers=TEAM | ({"Idempotency-Key": key} if key else {})), body


def body_cid(req):
    return req["_cid"]


def propose(c, cid, action="simulate_charge", current=2.0, who=None, key=None, **extra):
    body = {"agent": "external-agent", "action": action, "params": {"current_A": current, "ambient_C": 25.0},
            "rationale": "Higher current, shorter charge.", "prediction": PREDICTION,
            "expected_effect": "Under 45 degC we go higher."} | (who or {}) | extra
    if action == "cell_test":
        body["specimen"] = {"id": "cell-07"}
    r = c.post(f"{BASE}/campaigns/{cid}/requests", json=body, headers=TEAM | ({"Idempotency-Key": key} if key else {}))
    return r


def loop_records(data, cid):
    L = Ledger(data / "ledgers" / "lab")
    try:
        return chain.records(L, cid), len(L.events(cid))
    finally:
        L.db.close()


# ------------------------------------------------------------------ the journey over HTTP

def test_person_and_agent_complete_a_campaign_over_http(lab):
    data, _ = lab
    with TestClient(app(lab)) as c:
        assert "loop-test-cell" in {s["id"] for s in ok(c.get(f"{BASE}/systems"))}
        dataset = ok(c.post(f"{BASE}/datasets", json={"manifest": json.loads((DATA / "manifest.json").read_text(
            encoding="utf-8")), "path": "bench"}, headers=TEAM), 201)
        assert [d["id"] for d in ok(c.get(f"{BASE}/datasets"))] == [dataset["id"]]
        assert ok(c.get(f"{BASE}/datasets/{dataset['id']}"))["records"][0]["id"] == "cell07-cc"
        opened = ok(c.post(f"{BASE}/campaigns", json={"author": "Ana", "title": "Faster charge", "objective": toy(
            datasets=[dataset["id"]], baseline=[f"{dataset['id']}/cell07-cc"])}, headers=TEAM), 201)
        cid = opened["campaign"]["campaign_id"]
        assert opened["campaign"]["objective"]["data"]["registered_by"] == "Ana"
        assert ok(c.get(f"{BASE}/campaigns"))["items"][0]["kind"] == "loop"
        derived = ok(c.post(f"{BASE}/campaigns/{cid}/measurements", headers=TEAM, json={
            "agent": "external-agent", "name": "peak", "spec": {"op": "max", "channel": "temperature",
                                                               "unit": "degC"}}))
        assert derived["record"]["actor"] == "agent:external-agent" and len(derived["measurements"]) == 1

        req = ok(propose(c, cid), 201)
        assert req["state"] == "awaiting_approval" and req["proposed_by"] == "agent:external-agent"
        assert ok(c.get(f"{BASE}/campaigns/{cid}/view"))["next_action"]["content_hash"] == req["content_hash"]
        queued = ok(c.post(f"{BASE}/campaigns/{cid}/requests/{req['request_id']}/approve", headers=TEAM,
                           json={"author": "Ana", "content_hash": req["content_hash"], "note": "Go"}))
        assert queued["approved_by"] == "person:Ana"
        deadline = time.monotonic() + 90
        while (done := ok(c.get(f"{BASE}/campaigns/{cid}/requests/{req['request_id']}")))["state"] != "completed":
            assert time.monotonic() < deadline, done        # the API's own runner thread runs it
            time.sleep(0.1)
        assert done["timeline"][-1]["actor"] == "system:runner"
        series = ok(c.get(f"{BASE}/campaigns/{cid}/series", params={"ref": req["request_id"],
                                                                  "quantity": "temperature", "max_points": 10}))
        assert series["downsampled"] and series["unit"] == "degC" and len(series["t"]) <= 10

        bench = ok(propose(c, cid, action="cell_test", who={"agent": None, "author": "Ana"}), 201)
        bench = ok(c.post(f"{BASE}/campaigns/{cid}/requests/{bench['request_id']}/approve", headers=TEAM,
                          json={"author": "Ana", "content_hash": bench["content_hash"]})) | {"_cid": cid}
        wrong, _ = bench_upload(c, bench, temp_unit="V")
        assert wrong.status_code == 422
        refusal = wrong.json()["detail"]["result_rejected"]
        assert refusal["record_id"] and any("V" in r for r in refusal["reasons"])
        good, body = bench_upload(c, bench)
        assert ok(good)["state"] == "completed" and ok(good)["result"]["basis"] == "physical"
        assert not (data / "state" / "lab" / "dataset-uploads" / body["upload_id"]).exists()

        ok(c.post(f"{BASE}/campaigns/{cid}/notes", headers=TEAM, json={"author": "Ana", "text": "Bench booked"}))
        amended = ok(c.post(f"{BASE}/campaigns/{cid}/amendments", headers=TEAM, json={
            "author": "Ana", "reason": "Cooler", "constraints": [{"quantity": "max_temperature", "op": "<=",
                                                                  "limit": 40.0, "unit": "degC", "label": "40"}]}))
        assert amended["amendment"]["data"]["version"] == 2
        concluded = ok(c.post(f"{BASE}/campaigns/{cid}/conclusion", headers=TEAM,
                              json={"agent": "external-agent", "text": "Two steps."}))
        assert concluded["type"] == "conclusion"
        for path, body in [("requests", {"agent": "external-agent", "action": "simulate_charge",
                                          "params": {"current_A": 3.0, "ambient_C": 25.0}, "rationale": "x",
                                          "prediction": PREDICTION, "expected_effect": "y"}),
                           ("notes", {"author": "Ana", "text": "late"}),
                           ("measurements", {"author": "Ana", "name": "late",
                                             "spec": {"op": "min", "channel": "temperature", "unit": "degC"}}),
                           ("conclusion", {"author": "Ana", "text": "Again"})]:
            r = c.post(f"{BASE}/campaigns/{cid}/{path}", json=body, headers=TEAM)
            assert r.status_code == 409 and "concluded" in r.json()["detail"], (path, r.text)
        assert ok(c.get(f"{BASE}/campaigns/{cid}/view"))["source"]["api"] == "http://testserver"
        export = c.get(f"{BASE}/campaigns/{cid}/export")
        assert export.status_code == 200 and f"{cid}.loop.json" in export.headers["content-disposition"]
        exported = export.json()
        assert exported["view"]["campaign"]["id"] == cid and exported["view"]["source"]["kind"] == "recorded"
        assert c.get(f"{BASE}/campaigns/loop_nothing/export").status_code == 404
    path = data / f"{cid}.loop.json"
    path.write_bytes(export.content)
    report = verify(path)                                   # the export's receipt re-checks offline
    assert report.ok, report.problems
    assert verify(data / "ledgers" / "lab", campaign_id=cid).ok
    records, _ = loop_records(data, cid)
    L = Ledger(data / "ledgers" / "lab")
    try:
        assert chain.problems(L, cid) == []
    finally:
        L.db.close()
    assert [r.actor for r in records if r.type == "approval"] == ["person:Ana", "person:Ana"]


# ------------------------------------------------------------------ refusals and authorization

def test_wrong_ledger_is_a_404_and_creates_nothing(lab):
    data, _ = lab
    with TestClient(app(lab, loop_workers=0)) as c:
        for method, path, body in [("get", "/systems", None), ("get", "/campaigns/x/view", None),
                                   ("post", "/campaigns", {"author": "Ana", "title": "t", "objective": toy()}),
                                   ("post", "/campaigns/x/requests/rq_x/approve",
                                    {"author": "Ana", "content_hash": "0" * 64})]:
            r = getattr(c, method)(f"/api/v1/ledgers/nope{path}", headers=TEAM, **({"json": body} if body else {}))
            assert r.status_code == 404, (path, r.text)
    assert sorted(p.name for p in (data / "ledgers").iterdir()) == ["lab"]
    assert sorted(p.name for p in (data / "state").iterdir()) == ["lab", "server.lock"] or \
        "nope" not in {p.name for p in (data / "state").iterdir()}


def test_writes_need_the_team_token_and_people_actions_need_a_person(lab):
    with TestClient(app(lab, loop_workers=0)) as c:
        cid = ok(c.post(f"{BASE}/campaigns", json={"author": "Ana", "title": "t", "objective": toy()},
                        headers=TEAM), 201)["campaign"]["campaign_id"]
        req = ok(propose(c, cid), 201)
        rq = req["request_id"]
        writes = [("/campaigns", {"author": "Ana", "title": "t", "objective": toy()}),
                  (f"/campaigns/{cid}/measurements", {"author": "Ana", "name": "p", "spec": {}}),
                  (f"/campaigns/{cid}/requests", {"author": "Ana"}),
                  (f"/campaigns/{cid}/requests/{rq}/approve", {"author": "Ana", "content_hash": "0" * 64}),
                  (f"/campaigns/{cid}/requests/{rq}/reject", {}), (f"/campaigns/{cid}/requests/{rq}/revise", {}),
                  (f"/campaigns/{cid}/requests/{rq}/cancel", {}), (f"/campaigns/{cid}/requests/{rq}/results", {}),
                  (f"/campaigns/{cid}/amendments", {}), (f"/campaigns/{cid}/notes", {}),
                  (f"/campaigns/{cid}/conclusion", {}), ("/datasets", {})]
        for path, body in writes:
            assert c.post(BASE + path, json=body).status_code == 403, path
            assert c.post(BASE + path, json=body, headers={"Authorization": "Bearer wrong"}).status_code == 403
        # an agent's client has no person's route: the body names a person, and the record says the person
        for action in ("approve", "reject", "revise"):
            r = c.post(f"{BASE}/campaigns/{cid}/requests/{rq}/{action}", headers=TEAM,
                       json={"agent": "external-agent", "content_hash": req["content_hash"], "reason": "r",
                             "params": {}})
            assert r.status_code == 422 and "author" in r.text, (action, r.text)
        r = c.post(f"{BASE}/campaigns", headers=TEAM, json={"agent": "external-agent", "title": "t",
                                                            "objective": toy(baseline_simulation=True)})
        assert r.status_code == 422 and "cannot pre-approve" in r.text
        r = c.post(f"{BASE}/campaigns", headers=TEAM, json={"author": "Ana", "title": "t",
                                                            "objective": objective(registered_by="Ben")})
        assert r.status_code == 403 and "cannot act as Ben" in r.text
        r = c.post(f"{BASE}/campaigns/{cid}/requests/{rq}/cancel", headers=TEAM,
                   json={"agent": "someone-else", "reason": "not mine"})
        assert r.status_code == 422 and "only its own" in r.text


def asgi_write(application, path, headers=(), chunks=(), method="POST"):
    """One raw ASGI request: (status, how many body reads the app made). Chunks stream as a chunked body."""
    chunks, reads, sent = list(chunks), [], []

    async def receive():
        reads.append(1)
        body = chunks.pop(0) if chunks else b""
        return {"type": "http.request", "body": body, "more_body": bool(chunks)}

    async def send(message):
        sent.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "root_path": "", "query_string": b"",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers], "client": ("c", 1),
             "server": ("testserver", 80)}
    asyncio.run(application(scope, receive, send))
    return sent[0]["status"], len(reads)


def test_writes_without_a_token_or_too_large_are_refused_before_the_body_is_read(lab):
    application = app(lab, loop_workers=0)
    huge = [("Content-Type", "application/json"), ("Content-Length", str(10**9))]
    # no token: refused before one byte is read, whatever the body claims to be
    assert asgi_write(application, f"{BASE}/campaigns", huge) == (403, 0)
    assert asgi_write(application, f"{BASE}/uploads/{'a' * 32}/run.csv", huge, method="PUT") == (403, 0)
    assert asgi_write(application, "/api/experiments", huge) == (403, 0)
    # with the token: a body over its route's limit is a 413, by its length or as it streams
    auth = [("Authorization", TEAM["Authorization"])]
    assert asgi_write(application, f"{BASE}/campaigns", auth + huge) == (413, 0)
    assert asgi_write(application, f"{BASE}/uploads/{'a' * 32}/run.csv", auth + huge, method="PUT") == (413, 0)
    status, reads = asgi_write(application, f"{BASE}/campaigns", auth + [("Content-Type", "application/json")],
                               [b"{" + b" " * 2**18] * 8 + [b"}"])
    assert status == 413 and reads == 4      # stopped at the chunk that passed 1 MB, not at the end
    # a manifest may be larger than other JSON: 2 MB reaches the route (an incomplete body: 422)
    status, _ = asgi_write(application, f"{BASE}/datasets", auth + [("Content-Type", "application/json")],
                           [b'{"manifest": {}, "path": "' + b"x" * 2**21 + b'"}'])
    assert status == 422
    with TestClient(application) as c:
        r = c.post(f"{BASE}/campaigns", content=b"{not json", headers={"Content-Type": "application/json"})
        assert r.status_code == 403            # not parsed first (that was a 422 "JSON decode error")
        r = c.post(f"{BASE}/campaigns", content=b"{not json", headers=TEAM | {"Content-Type": "application/json"})
        assert r.status_code == 422


def test_service_refusals_are_http_statuses(lab):
    with TestClient(app(lab, loop_workers=0)) as c:
        cid = ok(c.post(f"{BASE}/campaigns", json={"author": "Ana", "title": "t", "objective": toy()},
                        headers=TEAM), 201)["campaign"]["campaign_id"]
        v1 = ok(propose(c, cid), 201)
        rq = v1["request_id"]
        v2 = ok(c.post(f"{BASE}/campaigns/{cid}/requests/{rq}/revise", headers=TEAM,
                       json={"author": "Ana", "params": {"current_A": 1.5}, "reason": "Rated 1.5 A"}))
        stale = c.post(f"{BASE}/campaigns/{cid}/requests/{rq}/approve", headers=TEAM,
                       json={"author": "Ana", "content_hash": v1["content_hash"]})
        assert stale.status_code == 409 and "review version 2" in stale.text
        assert c.get(f"{BASE}/campaigns/{cid}/requests/rq_{'0' * 20}").status_code == 404
        assert c.get(f"{BASE}/campaigns/loop_nothing/view").status_code == 404
        bad = propose(c, cid, current=99.0)
        assert bad.status_code == 422 and "current_A" in bad.text
        assert propose(c, cid, action="no_such_action").status_code == 422
        inf = c.post(f"{BASE}/campaigns/{cid}/requests", headers={**TEAM, "Content-Type": "application/json"},
                     content=b'{"agent": "a", "action": "simulate_charge", "params": {"current_A": 1e999, '
                             b'"ambient_C": 25}, "rationale": "r", "prediction": {}, "expected_effect": "e"}')
        assert inf.status_code == 422
        assert ok(c.post(f"{BASE}/campaigns/{cid}/requests/{rq}/approve", headers=TEAM,
                         json={"author": "Ana", "content_hash": v2["content_hash"]}))["state"] == "queued"
        bad_key = c.post(f"{BASE}/campaigns/{cid}/notes", headers=TEAM | {"Idempotency-Key": "a b"},
                         json={"author": "Ana", "text": "x"})
        assert bad_key.status_code == 400


def test_open_reads_cost_bounded_work(lab, monkeypatch):
    """Reads need no token, so none may cost unbounded work: an export is made once per ledger revision, series
    reads parse at most two blobs at once, a campaigns page is at most 50, and a loop campaign's event stream ends
    at its conclusion."""
    import gzip
    import threading
    from translucent import api_v1, loop_export
    from translucent.loop_service import LoopService
    made, real = [], loop_export.export
    monkeypatch.setattr(loop_export, "export", lambda *a, **k: made.append(1) or real(*a, **k))
    levels, gz = [], gzip.compress
    monkeypatch.setattr(gzip, "compress", lambda d, **k: levels.append(k.get("compresslevel")) or gz(d, **k))
    with TestClient(app(lab, loop_workers=0)) as c:
        cid = ok(c.post(f"{BASE}/campaigns", json={"agent": "bot", "title": "t", "objective": toy()},
                        headers=TEAM), 201)["campaign"]["campaign_id"]
        first, again = c.get(f"{BASE}/campaigns/{cid}/export"), c.get(f"{BASE}/campaigns/{cid}/export")
        assert first.status_code == 200 and first.content == again.content and len(made) == 1
        assert first.json()["view"]["campaign"]["id"] == cid and set(levels) == {6}   # not gzip's slow level 9
        ok(c.post(f"{BASE}/campaigns/{cid}/notes", headers=TEAM, json={"author": "Ana", "text": "new evidence"}))
        assert c.get(f"{BASE}/campaigns/{cid}/export").status_code == 200 and len(made) == 2
        assert c.get(f"{BASE}/campaigns", params={"limit": 51}).status_code == 422

        running, peak, lock = [0], [0], threading.Lock()

        def slow(self, *a, **k):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.2)
            with lock:
                running[0] -= 1
            return {"t": [], "y": [], "unit": "degC", "t_unit": "s", "n": 0, "downsampled": False}
        monkeypatch.setattr(LoopService, "series", slow)
        readers = [threading.Thread(target=lambda: c.get(f"{BASE}/campaigns/{cid}/series",
                                                          params={"ref": "x", "quantity": "temperature"}))
                   for _ in range(6)]
        for t in readers:
            t.start()
        for t in readers:
            t.join()
        assert peak[0] == api_v1.SERIES_READS._initial_value == 2
        monkeypatch.setattr(api_v1, "BUSY_WAIT_S", 0.05)            # a read that waits too long is told to retry
        for _ in range(2):
            api_v1.SERIES_READS.acquire()
        try:
            busy = c.get(f"{BASE}/campaigns/{cid}/series", params={"ref": "x", "quantity": "temperature"})
            assert busy.status_code == 503 and busy.headers["Retry-After"]
        finally:
            for _ in range(2):
                api_v1.SERIES_READS.release()
        mutex = c.app.state.translucent.host(BASE.rsplit("/", 1)[1]).loop_export_mutex
        with mutex:                                                   # another export is being made
            busy = c.get(f"{BASE}/campaigns/{cid}/export")
            assert busy.status_code == 503 and busy.headers["Retry-After"]

        ok(c.post(f"{BASE}/campaigns/{cid}/conclusion", headers=TEAM, json={"agent": "bot", "text": "Done."}))
        got = {}
        stream = threading.Thread(target=lambda: got.update(r=c.get(f"{BASE}/campaigns/{cid}/events")), daemon=True)
        stream.start()
        stream.join(10)
        assert not stream.is_alive(), "the event stream of a concluded loop campaign never ended"
        assert got["r"].status_code == 200 and "loop.recorded" in got["r"].text


def test_campaign_free_text_is_scrubbed_before_the_ledger_keeps_it(lab):
    """The objective's statement and constraint labels, the title, an amendment's labels and a conclusion's
    unresolved reasons are free text: a pasted key never reaches the insert-only ledger. Ids stay as written."""
    data, _ = lab
    secret = "sk-" + "Zq8" * 10
    obj = toy()
    obj["statement"] = f"Charge faster. temp key {secret}"
    obj["constraints"] = [dict(obj["constraints"][0], label=f"limit {secret}")]
    obj["planner"] = dict(obj["planner"], client={"name": f"bot {secret}", "version": "1.0"})
    with TestClient(app(lab, loop_workers=0)) as c:
        opened = ok(c.post(f"{BASE}/campaigns", headers=TEAM | {"Idempotency-Key": "open-s"},
                           json={"title": f"Title {secret}", "agent": "bot", "objective": obj}), 201)
        cid = opened["campaign"]["campaign_id"]
        stored = opened["campaign"]["objective"]["data"]
        assert stored["statement"] == "Charge faster. temp key [secret removed]"
        assert stored["planner"]["client"] == {"name": "bot [secret removed]", "version": "1.0"}
        assert stored["constraints"][0]["label"] == "limit [secret removed]"
        assert {k: v for k, v in stored["constraints"][0].items() if k != "label"} == \
            {k: v for k, v in obj["constraints"][0].items() if k != "label"}      # only the label is free text
        ok(c.post(f"{BASE}/campaigns/{cid}/amendments", headers=TEAM, json={
            "author": "Ana", "reason": "Cooler", "constraints": [dict(obj["constraints"][0], label=f"40 {secret}")]}))
        done = ok(c.post(f"{BASE}/campaigns/{cid}/conclusion", headers=TEAM, json={
            "agent": "bot", "text": f"done {secret}", "unresolved_reasons": [f"pasted {secret}", "plain reason"]}))
        assert done["data"]["unresolved_reasons"] == ["pasted [secret removed]", "plain reason"]
        assert c.get(f"{BASE}/campaigns").json()["items"][0]["title"] == "Title [secret removed]"
    L = Ledger(data / "ledgers" / "lab")
    try:
        assert L.db.execute("SELECT COUNT(*) FROM record WHERE body LIKE ?", (f"%{secret}%",)).fetchone()[0] == 0
        assert L.db.execute("SELECT COUNT(*) FROM event WHERE body LIKE ?", (f"%{secret}%",)).fetchone()[0] == 0
        assert chain.problems(L, cid) == []
    finally:
        L.db.close()


def test_paid_planners_need_the_administrator_and_budgets_are_capped(lab, monkeypatch):
    with TestClient(app(lab, loop_workers=0)) as c:
        paid = toy(planner={"kind": "muse", "profile": "dev", "client": None})
        r = c.post(f"{BASE}/campaigns", json={"author": "Ana", "title": "t", "objective": paid}, headers=TEAM)
        assert r.status_code == 403 and "administrator" in r.text
        for budget, why in [({"model_usd": 26, "experiments": 4}, "more than the $25 cap"),
                            ({"model_usd": -1, "experiments": 4}, "finite number of dollars"),
                            ({"model_usd": 0, "experiments": 4, "experiment_usd": -1}, "experiment_usd")]:
            r = c.post(f"{BASE}/campaigns", json={"author": "Ana", "title": "t", "objective": toy(budget=budget)},
                       headers=TEAM)
            assert r.status_code == 422 and why in r.text, r.text
        r = c.post(f"{BASE}/campaigns", headers={**TEAM, "Content-Type": "application/json"},
                   content=b'{"author": "Ana", "title": "t", "objective": {"planner": {"kind": "external"}, '
                           b'"budget": {"model_usd": 1e999}}}')
        assert r.status_code == 422 and "finite" in r.text
        monkeypatch.setenv("TRANSLUCENT_PROFILE", "meta")
        r = c.post(f"{BASE}/campaigns", json={"author": "Ana", "title": "t",
                                              "objective": toy(budget={"model_usd": 100, "experiments": 4})},
                   headers=TEAM)
        assert r.status_code == 201, r.text           # the meta profile's cap is $170
        # a paid planner without a profile is checked against the server's and stored with it, so it runs on it
        monkeypatch.setenv("TRANSLUCENT_PROFILE", "dev")
        monkeypatch.setenv("TRANSLUCENT_LIVE", "1")
        monkeypatch.setenv("TRANSLUCENT_ADMIN_TOKEN", "test-administrator")
        unset = toy(planner={"kind": "muse", "client": None}, budget={"model_usd": 5, "experiments": 4})
        opened = ok(c.post(f"{BASE}/campaigns", json={"author": "Ana", "title": "t", "objective": unset},
                           headers=ADMIN), 201)
        assert opened["campaign"]["objective"]["data"]["planner"]["profile"] == "dev"


# ------------------------------------------------------------------ 5. Idempotency-Key across a restart

def test_every_write_honours_its_idempotency_key_across_a_restart(lab):
    data, _ = lab
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    calls, answers = [], {}

    def send(c, name, path, body, key, status=200, record=True):
        r = c.post(BASE + path, json=body, headers=TEAM | {"Idempotency-Key": key})
        assert r.status_code == status, (name, r.text)
        calls.append((name, path, body, key, status, record))
        answers[name] = r.json()
        return r.json()

    with TestClient(app(lab, loop_workers=0)) as c:
        send(c, "dataset", "/datasets", {"manifest": manifest, "path": "bench"}, "k-ds", 201)
        cid = send(c, "open", "/campaigns", {"author": "Ana", "title": "Keyed", "objective": toy(
            datasets=["fastcharge-screen-2026q3"], baseline=["fastcharge-screen-2026q3/cell07-cc"])},
            "k-open", 201)["campaign"]["campaign_id"]
        send(c, "measure", f"/campaigns/{cid}/measurements", {"author": "Ana", "name": "peak", "spec": {
            "op": "max", "channel": "temperature", "unit": "degC"}}, "k-msr")
        proposal = {"agent": "external-agent", "action": "simulate_charge", "rationale": "r",
                    "prediction": PREDICTION, "expected_effect": "e"}
        r1 = send(c, "p1", f"/campaigns/{cid}/requests", proposal | {"params": {"current_A": 2.0, "ambient_C": 25.0}},
                  "k-p1", 201)
        r2 = send(c, "p2", f"/campaigns/{cid}/requests", proposal | {"params": {"current_A": 3.0, "ambient_C": 25.0}},
                  "k-p2", 201)
        r3 = send(c, "p3", f"/campaigns/{cid}/requests", proposal | {
            "action": "cell_test", "params": {"current_A": 2.0, "ambient_C": 25.0}, "specimen": {"id": "cell-07"}},
            "k-p3", 201)
        r4 = send(c, "p4", f"/campaigns/{cid}/requests", proposal | {"params": {"current_A": 4.0, "ambient_C": 25.0}},
                  "k-p4", 201)
        send(c, "approve", f"/campaigns/{cid}/requests/{r1['request_id']}/approve",
             {"author": "Ana", "content_hash": r1["content_hash"]}, "k-a1")
        send(c, "reject", f"/campaigns/{cid}/requests/{r2['request_id']}/reject",
             {"author": "Ana", "content_hash": r2["content_hash"], "reason": "Too hot"}, "k-rj")
        send(c, "revise", f"/campaigns/{cid}/requests/{r4['request_id']}/revise",
             {"author": "Ana", "params": {"current_A": 3.5}, "reason": "Rated 3.5 A"}, "k-rv")
        send(c, "cancel", f"/campaigns/{cid}/requests/{r4['request_id']}/cancel",
             {"author": "Ana", "reason": "Not needed"}, "k-c4")
        bench = send(c, "approve3", f"/campaigns/{cid}/requests/{r3['request_id']}/approve",
                     {"author": "Ana", "content_hash": r3["content_hash"]}, "k-a3")
        response, body = bench_upload(c, bench | {"_cid": cid}, key="k-res")
        assert response.status_code == 200, response.text
        calls.append(("result", f"/campaigns/{cid}/requests/{r3['request_id']}/results", body, "k-res", 200, False))
        answers["result"] = response.json()
        send(c, "amend", f"/campaigns/{cid}/amendments", {"author": "Ana", "reason": "Cooler", "constraints": [
            {"quantity": "max_temperature", "op": "<=", "limit": 40.0, "unit": "degC", "label": "40"}]}, "k-am")
        send(c, "note", f"/campaigns/{cid}/notes", {"author": "Ana", "text": "Booked"}, "k-n", record=False)
        send(c, "cancel1", f"/campaigns/{cid}/requests/{r1['request_id']}/cancel",     # no runner: r1 stays queued
             {"author": "Ana", "reason": "No solver on this server"}, "k-c1")
        send(c, "conclude", f"/campaigns/{cid}/conclusion", {"author": "Ana", "text": "Done."}, "k-con")
    before = loop_records(data, cid)
    assert {r.type for r in before[0]} >= {"objective", "measurement", "request", "approval", "rejection",
                                           "cancellation", "result", "amendment", "conclusion"}

    with TestClient(app(lab, loop_workers=0)) as c:      # a restart with the API's state kept
        for name, path, body, key, status, _ in calls:
            r = c.post(BASE + path, json=body, headers=TEAM | {"Idempotency-Key": key})
            assert (r.status_code, r.json()) == (status, answers[name]), name
        r = c.post(f"{BASE}/campaigns/{cid}/notes", json={"author": "Ana", "text": "Other"},
                   headers=TEAM | {"Idempotency-Key": "k-n"})
        assert r.status_code == 409
    after = loop_records(data, cid)
    assert [r.id for r in after[0]] == [r.id for r in before[0]] and after[1] == before[1]

    shutil.rmtree(data / "state" / "lab" / "idempotency")          # the API's state lost: the ledger ids remain
    with TestClient(app(lab, loop_workers=0)) as c:
        for name, path, body, key, status, record in calls:
            if not record:                  # a note is an event and a result's files are gone: replies only
                continue
            r = c.post(BASE + path, json=body, headers=TEAM | {"Idempotency-Key": key})
            assert r.status_code == status, (name, r.text)
        r = c.post(f"{BASE}/campaigns/{cid}/requests", headers=TEAM | {"Idempotency-Key": "k-p1"},
                   json=proposal | {"params": {"current_A": 2.5, "ambient_C": 25.0}})
        assert r.status_code == 409, r.text       # the same key, a different request: never a second record
    final = loop_records(data, cid)
    assert [r.id for r in final[0]] == [r.id for r in before[0]] and final[1] == before[1]


def test_the_server_runs_a_baseline_campaign_to_its_conclusion_on_its_own(lab):
    """No client drives it: the planner thread proposes, the objective's rule approves, the runner executes, and
    the planner concludes when the budget is spent."""
    rule = {"executor": "computational", "max_count": 3, "max_wall_s": 60.0}
    with TestClient(app(lab)) as c:
        opened = ok(c.post(f"{BASE}/campaigns", headers=TEAM, json={"author": "Ana", "title": "Sweep", "objective": toy(
            planner={"kind": "domain_baseline", "profile": None, "client": None}, auto_approve=[rule],
            budget={"model_usd": 0.0, "experiments": 3, "experiment_usd": 0.0})}), 201)
        cid = opened["campaign"]["campaign_id"]
        deadline = time.time() + 90
        while time.time() < deadline:
            view = ok(c.get(f"{BASE}/campaigns/{cid}/view"))
            if view["campaign"]["status"] == "concluded":
                break
            time.sleep(0.2)
        assert view["campaign"]["status"] == "concluded", view["campaign"]
        done = [r for r in view["requests"] if r["state"] == "completed"]
        assert len(done) == 3 and all(r["proposed_by"].startswith("agent:baseline-") for r in done)
