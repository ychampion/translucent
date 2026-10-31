"""A caller of the loop service names files for import only inside the server's import folder."""
import json
import os
import shutil
from pathlib import Path

import pytest

from translucent.ledger import Ledger
from translucent.loop import NotAllowed
from translucent.loop_service import Actor, LoopService

DATA = Path(__file__).parent / "data" / "import_v2"
AGENT = Actor("agent", "external-test")


@pytest.fixture
def staged(tmp_path):
    root = tmp_path / "imports"
    shutil.copytree(DATA, root / "bench")
    outside = tmp_path / "secret"
    outside.mkdir()
    return root, outside, json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))


def service(tmp_path, root):
    return LoopService(Ledger(tmp_path / "ledger"), AGENT, import_root=root)


def test_files_inside_the_import_folder_import(tmp_path, staged):
    root, _, manifest = staged
    out = service(tmp_path, root).import_dataset(manifest, "bench")
    assert out["records"][0]["specimen"]["id"] == "cell-0007"


@pytest.mark.parametrize("where", ["absolute", "dotdot", "mapping"])
def test_a_path_outside_the_import_folder_is_refused(tmp_path, staged, where):
    root, outside, manifest = staged
    for f in (DATA).iterdir():
        if f.is_file():
            shutil.copy(f, outside / f.name)
    files = {"absolute": str(outside), "dotdot": "../secret",
             "mapping": {n: str(outside / n) for n in ("eclab-cell07.mpt",)}}[where]
    with pytest.raises(NotAllowed, match="not a file in the import folder"):
        service(tmp_path, root).import_dataset(manifest, files)


def test_without_an_import_folder_no_path_is_read(tmp_path, staged):
    _, _, manifest = staged
    with pytest.raises(NotAllowed, match="no import folder"):
        service(tmp_path, None).import_dataset(manifest, "bench")


def test_a_manifest_path_is_refused(tmp_path, staged):
    root, _, _ = staged
    with pytest.raises(NotAllowed, match="not a path"):
        service(tmp_path, root).import_dataset(str(root / "bench" / "manifest.json"), "bench")


def test_a_link_inside_the_import_folder_is_not_followed(tmp_path, staged):
    root, outside, manifest = staged
    try:
        os.symlink(outside, root / "link", target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs a privilege this account lacks")
    with pytest.raises(NotAllowed, match="links are not followed"):
        service(tmp_path, root).import_dataset(manifest, "link")


def test_a_link_to_a_file_outside_inside_a_confined_folder_is_not_followed(tmp_path, staged):
    root, outside, manifest = staged
    (outside / "eclab-cell07.mpt").write_bytes((DATA / "eclab-cell07.mpt").read_bytes())
    target = root / "bench" / "eclab-cell07.mpt"
    target.unlink()
    try:
        os.symlink(outside / "eclab-cell07.mpt", target)
    except OSError:
        pytest.skip("creating symlinks needs a privilege this account lacks")
    with pytest.raises(NotAllowed, match="links are not followed"):
        service(tmp_path, root).import_dataset(manifest, "bench")


def test_missing_and_outside_paths_get_the_same_answer(tmp_path, staged):
    root, outside, manifest = staged
    answers = set()
    for files in ({"eclab-cell07.mpt": str(outside / "nothing-here")}, {"eclab-cell07.mpt": "bench/nothing-here"},
                  {"eclab-cell07.mpt": str(DATA / "eclab-cell07.mpt")}):
        with pytest.raises(NotAllowed) as e:
            service(tmp_path, root).import_dataset(manifest, files)
        answers.add(str(e.value).split(": ", 1)[1])
    assert answers == {"not a file in the import folder"}
