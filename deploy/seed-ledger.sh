#!/usr/bin/env bash
# Copy a recorded ledger (committed under runs/) into deploy/state/ledger, which the API container writes to
# (people's notes and sign-offs are new records). Refuses to overwrite an existing ledger.
set -euo pipefail
cd "$(dirname "$0")"
src=${1:-../runs/fw-chain8}
[[ -f $src/ledger.sqlite ]] || { echo "no ledger at $src" >&2; exit 2; }
[[ -e state/ledger/ledger.sqlite ]] && { echo "state/ledger already has a ledger; move it away first" >&2; exit 3; }
mkdir -p state/ledger state/api
cp -a "$src"/. state/ledger/
chown -R 10001:10001 state 2>/dev/null || echo "note: run as root (or chown -R 10001:10001 deploy/state) so the container can write"
echo "seeded state/ledger from $src"
