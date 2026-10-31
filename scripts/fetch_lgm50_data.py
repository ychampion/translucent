"""Download the Chen 2020 LG M50 validation records into data/raw/chen2020-lgm50/ (git-ignored), checking each file.

Chen, Brosa Planella, O'Regan, Gastol, Widanage, Kendrick: experimental data for "Development of Experimental
Techniques for Parameterization of Multi-scale Lithium-ion Battery Models", Zenodo doi:10.5281/zenodo.4032561 (v1),
CC BY 4.0. Three Maccor exports, 1.8 MB in all. The sha256 and size of each file are pinned below (the Zenodo md5
matched on 2026-10-31); a download that differs is refused and never written, and a present file with other bytes is
an error. The import manifest for these records is data/chen2020-lgm50/manifest.json.
Usage: uv run python scripts/fetch_lgm50_data.py [LGM50_cell02.csv ...]   (no names: all three)
"""
import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data" / "raw" / "chen2020-lgm50"
URL = "https://zenodo.org/records/4032561/files/{name}?download=1"
# name -> (bytes, sha256)
FILES = {
    "LGM50_cell02.csv": (597715, "650e7f9ac5217b4db158047e50b6b464100b13f4859002421114edebca629b35"),
    "LGM50_cell03.csv": (590794, "fcc13423786d6044f3ec41b1b278fb1d42ff55018666be743243f7c34b1b98cb"),
    "LGM50_cell04.csv": (595967, "e1cbb60e79911824d5559f12db8e8ecdd4781d077a45f5b1ecbcf103aa271d9e"),
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(name, dest=DEST, files=FILES, url=URL, opener=urllib.request.urlopen):
    """Download one file to dest/<name>, or confirm the copy there. Returns a line saying which; raises ValueError."""
    size, want = files[name]
    path = dest / name
    if path.exists():
        if sha256(path) != want:
            raise ValueError(f"{path} exists but is not the published {name} (sha256 differs); move it away and run again")
        return f"{name}: already present, checksum ok"
    dest.mkdir(parents=True, exist_ok=True)
    part = path.with_name(name + ".part")
    source = url.format(name=name)
    h, got = hashlib.sha256(), 0
    try:
        with opener(source, timeout=60) as response, open(part, "wb") as out:
            for chunk in iter(lambda: response.read(1 << 20), b""):
                h.update(chunk)
                got += len(chunk)
                out.write(chunk)
        if got != size or h.hexdigest() != want:
            raise ValueError(f"{name}: download from {source} is not the published file "
                             f"({got} bytes, sha256 {h.hexdigest()[:12]}; expected {size} bytes, {want[:12]})")
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    return f"{name}: downloaded {got} bytes, checksum ok -> {path}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Download the Chen 2020 LG M50 records into data/raw/chen2020-lgm50/")
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
    if not failed:
        print("Import them into a ledger with:")
        print("  translucent import data/chen2020-lgm50/manifest.json --ledger <ledger> --files data/raw/chen2020-lgm50")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
