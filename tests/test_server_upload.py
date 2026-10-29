"""A person adds their own recording over HTTP: stream the files, then register the manifest. Offline only."""
import uuid

from fastapi.testclient import TestClient

from tests.test_experiments import recording  # noqa: F401 - fixture: synthetic movie, CSVs, manifest
from tests.test_server_people import TEAM, make_app, no_live_models, store, team  # noqa: F401 - fixtures


def _upload(client, r, uid, headers=TEAM):
    for p in sorted(r.source.iterdir()):
        resp = client.put(f"/api/uploads/{uid}/{p.name}", content=p.read_bytes(), headers=headers)
        assert resp.status_code == 201, resp.text
        assert resp.json() == {"name": p.name, "bytes": p.stat().st_size}


def test_upload_then_register_lists_the_experiment(store, team, recording):
    uid = uuid.uuid4().hex
    with TestClient(make_app(store)) as client:
        _upload(client, recording, uid)
        resp = client.post("/api/experiments", json={"upload_id": uid, "manifest": recording.manifest}, headers=TEAM)
        assert resp.status_code == 201, resp.text
        assert resp.json()["experiment"] == "user/ana-cell-01"
        listed = client.get("/api/experiments").json()
        assert "user/ana-cell-01" in [x["experiment"] for x in listed]
        # the staging folder is gone once registered
        assert client.post("/api/experiments", json={"upload_id": uid, "manifest": recording.manifest},
                           headers=TEAM).status_code == 404


def test_register_explains_a_missing_source_plainly(store, team, recording):
    uid = uuid.uuid4().hex
    bad = {**recording.manifest, "clock": {"interval_s": 2.0}}
    with TestClient(make_app(store)) as client:
        _upload(client, recording, uid)
        resp = client.post("/api/experiments", json={"upload_id": uid, "manifest": bad}, headers=TEAM)
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert isinstance(detail, str) and "source" in detail and "Value error" not in detail


def test_uploads_need_a_token_and_plain_names(store, team, recording):
    uid = uuid.uuid4().hex
    with TestClient(make_app(store)) as client:
        assert client.put(f"/api/uploads/{uid}/movie.mp4", content=b"x").status_code == 403
        assert client.put(f"/api/uploads/{uid}/manifest.json", content=b"{}", headers=TEAM).status_code == 400
        assert client.put(f"/api/uploads/{uid}/.hidden", content=b"x", headers=TEAM).status_code == 400
        assert client.put(f"/api/uploads/not-hex/movie.mp4", content=b"x", headers=TEAM).status_code == 400
        assert client.post("/api/experiments", json={"upload_id": uid, "manifest": {}}).status_code == 403


def test_a_playlist_posing_as_a_video_never_reaches_a_decoder(store, team, recording):
    uid = uuid.uuid4().hex
    (recording.source / "movie.mp4").write_text("#EXTM3U\n#EXTINF:1,\nfile:///etc/passwd\n")
    with TestClient(make_app(store)) as client:
        _upload(client, recording, uid)
        resp = client.post("/api/experiments", json={"upload_id": uid, "manifest": recording.manifest}, headers=TEAM)
        assert resp.status_code == 422 and "not an MP4 or MOV" in resp.json()["detail"]


def test_an_upload_holds_at_most_three_files(store, team, recording):
    uid = uuid.uuid4().hex
    with TestClient(make_app(store)) as client:
        _upload(client, recording, uid)
        assert client.put(f"/api/uploads/{uid}/extra.csv", content=b"x", headers=TEAM).status_code == 409
        # replacing a file already there is fine
        assert client.put(f"/api/uploads/{uid}/times.csv", content=(recording.source / "times.csv").read_bytes(),
                          headers=TEAM).status_code == 201
