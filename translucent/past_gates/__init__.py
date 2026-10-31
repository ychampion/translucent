"""Earlier versions of translucent/gate.py, frozen byte for byte (`git show <commit>:translucent/gate.py`).

`translucent verify` re-checks each finding with the gate rules it was recorded under, so a later gate change does not
turn a recorded verdict into a mismatch. Never edit these files: a campaign's setup record names its gate by the
file's sha256. Before changing translucent/gate.py, copy it here as gate_<its last commit>.py and add the new row
below (tests/test_past_gates.py fails until both are done).
"""
import importlib

# (commit, committed at in unix seconds, sha256 with LF line endings), oldest first. The last row is the current
# translucent/gate.py; every earlier row has its file gate_<commit>.py here.
VERSIONS = [
    ("185894a", 1793178238, "85baf323657849f0c34da63846f2c0ed86b783b1abfcf2325c39ff3f4cc3bf0b"),
    ("aefb44d", 1793187606, "db45a20385f52af641ee142c4fc17f4154654931bf976bc8d98d67ca968ec270"),
    ("0115acf", 1793208628, "d5b1437be9721e42fa4e38a85322832b4f6ec2bc281da71b00c5616aaaaf8e44"),
    ("4ab0a85", 1793269459, "1d0ebffc5bfae6469dff3dd59be4a0e41b67b50702078ddef01e951cb7cdd94e"),
    ("6ee5937", 1793295367, "7febed5591d30858f98f79d1c5244128240c68a9bd52bd2bd2d3fc14388d4f2a"),
    ("1e164e1", 1793296017, "4245d5a3599bd946e790de43910a36fb92e244de32b2d95aa2e34ecef1cac8c9"),
]


def by_hash(sha256):
    """The commit whose gate.py has this sha256, or None when no frozen copy matches (a changed working tree)."""
    return next((commit for commit, _, h in VERSIONS if h == sha256), None)


def in_force(t):
    """The commit whose gate was current at unix time t, or None before the first one."""
    return next((commit for commit, at, _ in reversed(VERSIONS) if at <= t), None)


def module(commit):
    """The gate module for a commit: translucent.gate itself for the current one."""
    if commit == VERSIONS[-1][0]:
        from translucent import gate
        return gate
    return importlib.import_module(f"translucent.past_gates.gate_{commit}")
