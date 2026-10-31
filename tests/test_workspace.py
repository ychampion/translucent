"""A data root holds many ledgers; an id is one plain folder name and nothing but `create` makes a ledger."""
import json

import pytest

from translucent.ledger import Ledger
from translucent.workspace import (DEFAULT, LedgerExists, UnknownLedger, Workspace, check_id, init_ledger,
                                   ledger_ids, valid_id)


def tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")) if root.exists() else []


@pytest.mark.parametrize("lid", ["acme", "default", "a1", "team_7-b", "0x", "a" * 64])
def test_valid_ids(lid):
    assert valid_id(lid) and check_id(lid) == lid


@pytest.mark.parametrize("lid", ["", "a", "a" * 65, "Acme", "-acme", "_acme", "..", ".", "../acme", "acme/x",
                                 "acme\\x", "a.b", "a b", "acmé", "con", "nul", "aux", "prn", "com1", "lpt9",
                                 "acme\x00", "C:", None, 7])
def test_invalid_ids_are_refused_and_reach_no_path(tmp_path, lid):
    ws = Workspace(tmp_path / "data")
    assert not valid_id(lid)
    with pytest.raises(ValueError, match="not a ledger id"):
        check_id(lid)
    for call in (ws.path, ws.state_dir, ws.title):
        with pytest.raises(UnknownLedger):
            call(lid)
    with pytest.raises(UnknownLedger):
        with ws.open(lid):
            pass
    assert not ws.exists(lid) and tree(tmp_path) == []


def test_unknown_ids_create_nothing(tmp_path):
    root = tmp_path / "data"
    ws = Workspace(root)
    assert ws.ids() == [] and ledger_ids(root) == []
    for call in (ws.path, ws.state_dir):
        with pytest.raises(UnknownLedger):
            call("acme")
    with pytest.raises(UnknownLedger):
        with ws.open("acme"):
            pass
    assert tree(tmp_path) == []
    (root / "ledgers" / "half").mkdir(parents=True)    # a folder without ledger.sqlite is not a ledger
    with pytest.raises(UnknownLedger):
        ws.path("half")
    assert ws.ids() == [] and tree(root / "ledgers" / "half") == []


def test_create_lists_opens_and_titles(tmp_path):
    root = tmp_path / "data"
    path = init_ledger(root, "acme", title="  Acme cells  ")
    assert path == root / "ledgers" / "acme" and (path / "ledger.sqlite").is_file()
    ws = Workspace(root)
    ws.create("beta")
    assert ws.ids() == ["acme", "beta"] and ledger_ids(root) == ["acme", "beta"]
    assert ws.title("acme") == "Acme cells" and ws.title("beta") == "beta"
    assert json.loads((path / "workspace.json").read_text())["id"] == "acme"
    assert ws.state_dir("acme") == root / "state" / "acme" and not (root / "state").exists()
    assert Workspace(root, state_root=tmp_path / "s").state_dir("beta") == tmp_path / "s" / "beta"
    with ws.open("acme") as L:
        assert L.root == path and L.list("campaign") == []
    with pytest.raises(LedgerExists):
        ws.create("acme")
    for bad in ("x", "../up"):
        with pytest.raises(ValueError):
            ws.create(bad)
    with pytest.raises(ValueError, match="title"):
        ws.create("gamma", title="a\nb")
    assert ws.ids() == ["acme", "beta"] and not (root / "up").exists()


def test_an_unreadable_title_falls_back_to_the_id(tmp_path):
    ws = Workspace(tmp_path)
    (ws.create("acme") / "workspace.json").write_text("{not json", encoding="utf-8")
    assert ws.title("acme") == "acme"


def test_an_invalid_folder_name_is_not_listed(tmp_path):
    for name in ("Upper", "con", "a"):
        Ledger(tmp_path / "ledgers" / name).db.close()
    Workspace(tmp_path).create("ok")
    assert Workspace(tmp_path).ids() == ["ok"]


def test_legacy_mapping_keeps_the_default_state_in_the_state_root(tmp_path):
    ws = Workspace(ledgers={DEFAULT: tmp_path / "ledger", "other": tmp_path / "o"}, state_root=tmp_path / "api")
    assert ws.legacy and ws.ids() == ["default", "other"]
    assert ws.path(DEFAULT) == tmp_path / "ledger" and ws.state_dir(DEFAULT) == tmp_path / "api"
    assert ws.state_dir("other") == tmp_path / "api" / "ledgers" / "other"
    with pytest.raises(UnknownLedger):
        ws.path("acme")
    with pytest.raises(ValueError, match="data root"):
        ws.create("acme")
    with ws.open(DEFAULT) as L:            # the configured folder is opened (and made) as the one-ledger server did
        assert (L.root / "ledger.sqlite").is_file()


@pytest.mark.parametrize("kwargs", [{}, {"data_root": "d", "ledgers": {"default": "x"}}, {"ledgers": {"default": "x"}},
                                    {"ledgers": {"../x": "x"}, "state_root": "s"}])
def test_bad_construction(kwargs):
    with pytest.raises(ValueError):
        Workspace(**kwargs)
