"""Read caches bounded to one immutable intervention verification snapshot.

Live campaign tools never attach this cache. The verifier enables it only after
historical gate savepoints finish, freezes SQLite writes for its lifetime, and
discards every cached record and derived example before closing the snapshot.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
import hashlib
import json


class VerificationCache:
    """Memoize trusted reads without caching a verdict or skipping a check."""

    def __init__(self, ledger, rows):
        self.ledger = ledger
        self.by_id = {r.id: r for r in rows}
        self.by_campaign = defaultdict(list)
        self.insertion_by_campaign = defaultdict(list)
        self.by_type = defaultdict(list)
        for row in rows:
            self.by_campaign[row.campaign_id].append(row)
        # Ledger.list orders by inserted_at/rowid, while typed intervention
        # queries and observations order by rowid alone. Preserve both orders.
        for (record_id,) in ledger.db.execute(
            "SELECT id FROM record WHERE kind='intervention' ORDER BY rowid"
        ):
            row = self.by_id[record_id]
            self.insertion_by_campaign[row.campaign_id].append(row)
            self.by_type[(row.campaign_id, row.type)].append(row)
        self.examples = {}
        self.traces = {}
        self.observation_features = {}
        self.observations = {}
        self.scores = {}
        self.fits = {}
        self.screens = {}
        self.memo = {}
        self._json_blobs = {}

    def records(self, campaign_id, kind=None):
        """Preserve each original query's order, with a fresh outer list."""
        rows = (
            self.by_campaign.get(campaign_id, ())
            if kind is None
            else self.by_type.get((campaign_id, kind), ())
        )
        return list(rows)

    def select(self, campaign_id, kinds):
        """Select record types while retaining their joint insertion order."""
        kinds = set(kinds)
        return [
            r
            for r in self.insertion_by_campaign.get(campaign_id, ())
            if r.type in kinds
        ]

    def blob_json(self, artifact_id):
        """Read each source once, checking its address before memoizing it."""
        if artifact_id not in self._json_blobs:
            data = self.ledger.blob_path(artifact_id).read_bytes()
            if "sha256:" + hashlib.sha256(data).hexdigest() != artifact_id:
                raise ValueError(
                    "Source bytes changed while verifying intervention data"
                )
            self._json_blobs[artifact_id] = json.loads(data)
        return self._json_blobs[artifact_id]


@contextmanager
def verification_cache(ledger):
    """Freeze and cache only the current verification snapshot, never live state."""
    existing = getattr(ledger, "_intervention_verification_cache", None)
    if existing is not None:
        yield existing
        return
    rows = ledger.list("intervention")
    if not rows:
        yield None
        return
    query_only = ledger.db.execute("PRAGMA query_only").fetchone()[0]
    if not query_only:
        # Export receipts retain the required schema, but need not retain this
        # derived index. Build it only in the writable scratch database.
        ledger.db.execute(
            "CREATE INDEX IF NOT EXISTS intervention_type "
            "ON record(campaign_id,json_extract(body,'$.type')) "
            "WHERE kind='intervention'"
        )
    ledger.db.execute("PRAGMA query_only=ON")
    ledger._intervention_verification_cache = VerificationCache(ledger, rows)
    try:
        yield ledger._intervention_verification_cache
    finally:
        del ledger._intervention_verification_cache
        ledger.db.execute(f"PRAGMA query_only={query_only}")
