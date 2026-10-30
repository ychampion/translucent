from translucent.scrub import MARK, error_summary, relative_paths, scrub

HASH = "sha256:" + "ab" * 32


def test_secrets_removed_hashes_kept():
    text = (f"key sk-{'Q' * 4}1a2b3c4d5e6f7g8h9 and fw_{'a1B2' * 6} token=hunter2hunter2 "
            f"Bearer abc.def.ghi123 {HASH} and run_4f2a9c1b2d3e plain words stay")
    out = scrub(text)
    assert "hunter2" not in out and "sk-QQQQ" not in out and "fw_a1B2" not in out and "abc.def" not in out
    assert HASH in out and "run_4f2a9c1b2d3e" in out and "plain words stay" in out and MARK in out


def test_long_random_token_removed_but_words_and_paths_kept():
    token = "aB3" * 15
    out = scrub(f"value {token} in um/s from data/groundtruth/reisecker2023.json")
    assert token not in out and "um/s" in out and "data/groundtruth/reisecker2023.json" in out


def test_error_summary_is_relative_and_capped():
    try:
        raise ValueError("bad file /home/someone/secret/dir/file.csv " + "y" * 4000)
    except ValueError as e:
        s = error_summary(e)
    assert s.startswith("ValueError: bad file …/file.csv") and len(s) <= 2048
    assert relative_paths("um/s and px/mm stay") == "um/s and px/mm stay"


def test_an_assigned_secret_is_marked_once():
    assert scrub("API_KEY = 'sk-a1B2c3D4e5F6g7H8i9J0'") == "API_KEY = '[secret removed]'"
    assert scrub("token: [secret removed]") == "token: [secret removed]"
