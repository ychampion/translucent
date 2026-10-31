import hashlib
import importlib.util
from pathlib import Path

import pytest

from translucent.instruments import RECORDED_FILES

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fetch_data", ROOT / "scripts" / "fetch_data.py")
fetch_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_data)


def test_every_recorded_movie_has_a_published_source():
    """The README's `translucent run --movie 4` and the tests read these git-ignored files; each has a source."""
    assert set(fetch_data.FILES) == set(RECORDED_FILES)
    for name, (rel, url, size) in fetch_data.FILES.items():
        assert url.startswith("https://static-content.springer.com/esm/") and size > 0
        assert rel == name + ".mp4"


def small(tmp_path, data):
    """One manifest entry served from a local file URL: the real download path, without the network."""
    source = tmp_path / "published.mp4"
    source.write_bytes(data)
    files = {"x/movie1": ("x/movie1.mp4", source.as_uri(), len(data))}
    return files, {"x/movie1": hashlib.sha256(data).hexdigest()}


def test_download_is_checked_then_kept(tmp_path):
    files, hashes = small(tmp_path, b"published movie bytes")
    raw = tmp_path / "raw"
    line = fetch_data.fetch("x/movie1", raw, files, hashes)
    assert "downloaded 21 bytes, checksum ok" in line
    assert (raw / "x/movie1.mp4").read_bytes() == b"published movie bytes"
    assert list((raw / "x").iterdir()) == [raw / "x/movie1.mp4"]  # no partial file left
    assert "already present" in fetch_data.fetch("x/movie1", raw, files, hashes)


def test_wrong_bytes_are_never_written(tmp_path):
    files, hashes = small(tmp_path, b"published movie bytes")
    (tmp_path / "published.mp4").write_bytes(b"something else entirely")  # the server sends other bytes
    raw = tmp_path / "raw"
    with pytest.raises(ValueError, match="not the published file"):
        fetch_data.fetch("x/movie1", raw, files, hashes)
    assert list((raw / "x").iterdir()) == []


def test_a_different_local_file_is_refused_not_overwritten(tmp_path):
    files, hashes = small(tmp_path, b"published movie bytes")
    dest = tmp_path / "raw" / "x" / "movie1.mp4"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"a re-encoded copy")
    with pytest.raises(ValueError, match="sha256 differs"):
        fetch_data.fetch("x/movie1", tmp_path / "raw", files, hashes)
    assert dest.read_bytes() == b"a re-encoded copy"


def test_unknown_names_are_refused(capsys):
    with pytest.raises(SystemExit) as stop:
        fetch_data.main(["reisecker2023/movie9"])
    assert stop.value.code == 2 and "choose from" in capsys.readouterr().err
