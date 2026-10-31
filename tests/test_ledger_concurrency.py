"""Several processes opening one ledger and storing the same bytes at once."""
import subprocess
import sys
import textwrap
from pathlib import Path

from translucent.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]

WORKER = textwrap.dedent("""
    import sys
    from translucent.ledger import Ledger
    root, n = sys.argv[1], int(sys.argv[2])
    for i in range(n):
        L = Ledger(root)                       # open while the other workers write
        art = L.put_bytes(b"same bytes in every worker %d" % (i % 5), kind="table", media_type="text/plain")
        L.db.close()
        print(art.id, flush=True)
""")


def test_workers_storing_identical_bytes_all_get_the_one_artifact(tmp_path):
    root = tmp_path / "ledger"
    Ledger(root).db.close()
    procs = [subprocess.Popen([sys.executable, "-c", WORKER, str(root), "40"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, cwd=ROOT) for _ in range(4)]
    outs = [p.communicate(timeout=300) for p in procs]
    for p, (_, err) in zip(procs, outs):
        assert p.returncode == 0, err
    ids = {line for out, _ in outs for line in out.split()}
    L = Ledger(root)
    stored = L.db.execute("SELECT id FROM record WHERE kind='artifact'").fetchall()
    assert ids == {row[0] for row in stored} and len(ids) == 5
    assert not list(root.rglob("*.tmp")) and not list(root.glob(".incoming-*"))
    for aid in ids:
        assert L.blob_path(aid).exists()
