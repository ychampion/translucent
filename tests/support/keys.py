"""Credentials for server tests (translucent.auth): a stored key with a known value, as `translucent keys add` makes
one, and the bearer header that carries it."""
from pathlib import Path

from translucent.auth import CredentialStore


def bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def add_key(folder, name: str, role: str, key: str, ledgers="*") -> dict:
    """Store a credential in `folder`/credentials.json (a data root, or a one-ledger server's state folder) and
    return its Authorization header."""
    CredentialStore(Path(folder) / "credentials.json").add(name, role, ledgers, key=key)
    return bearer(key)
