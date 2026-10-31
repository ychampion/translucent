"""Download the published movies the CLI, evals and some tests read from data/raw/ (git-ignored), checking each file.

Every file is the paper's own supplementary movie (CC BY 4.0, see the README's "Data and licences"). Its sha256 is
the one translucent.instruments.RECORDED_FILES pins the experiment records to, so a file that differs is refused and
never written. Files already present with the right bytes are left alone; a present file with other bytes is an error.
Usage: uv run python scripts/fetch_data.py [reisecker2023/movie4 ...]   (no names: all six movies, about 106 MB)
"""
import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

from translucent.budget import ROOT
from translucent.instruments import RECORDED_FILES

ESM = "https://static-content.springer.com/esm/art%3A10.1038%2F{doi}/MediaObjects/{name}"
REISECKER, LU = "s41467-023-37476-y", "s41467-023-40574-6"
# experiment -> (path under data/raw, url, bytes). Reisecker Supplementary Movies 1-5 are MOESM4-8; Lu's is MOESM4.
FILES = {
    **{f"reisecker2023/movie{n}": (f"reisecker2023/movie{n}.mp4",
                                   ESM.format(doi=REISECKER, name=f"41467_2023_37476_MOESM{n + 3}_ESM.mp4"), size)
       for n, size in zip(range(1, 6), (6769719, 14145505, 31005884, 11670657, 25067249))},
    "lu2023/movie1": ("lu2023/movie1.mp4", ESM.format(doi=LU, name="41467_2023_40574_MOESM4_ESM.mp4"), 17032839),
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(name, raw=ROOT / "data" / "raw", files=FILES, hashes=RECORDED_FILES, opener=urllib.request.urlopen):
    """Download one file to raw/<path>, or confirm the copy there. Returns a line saying which; raises ValueError."""
    rel, url, size = files[name]
    dest, want = raw / rel, hashes[name]
    if dest.exists():
        if sha256(dest) != want:
            raise ValueError(f"{dest} exists but is not {name} (sha256 differs); move it away and run again")
        return f"{name}: already present, checksum ok"
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h, got = hashlib.sha256(), 0
    try:
        with opener(url, timeout=60) as response, open(part, "wb") as out:
            for chunk in iter(lambda: response.read(1 << 20), b""):
                h.update(chunk)
                got += len(chunk)
                out.write(chunk)
        if got != size or h.hexdigest() != want:
            raise ValueError(f"{name}: download from {url} is not the published file "
                             f"({got} bytes, sha256 {h.hexdigest()[:12]}; expected {size} bytes, {want[:12]})")
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return f"{name}: downloaded {got} bytes, checksum ok -> {dest}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Download the papers' supplementary movies into data/raw/")
    parser.add_argument("names", nargs="*", metavar="NAME", help=f"default: all of {', '.join(FILES)}")
    args = parser.parse_args(argv)
    if unknown := [n for n in args.names if n not in FILES]:
        parser.error(f"unknown {', '.join(unknown)}; choose from {', '.join(FILES)}")
    failed = 0
    for name in args.names or FILES:
        try:
            print(fetch(name))
        except (ValueError, OSError) as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
