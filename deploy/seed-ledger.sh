#!/usr/bin/env bash
# Seed deploy/state for the containers:
#   seed-ledger.sh [ledger_src]     copy a recorded ledger (committed under runs/) into state/ledger, which the API
#                                   container writes to (people's notes and sign-offs are new records); refuses to
#                                   overwrite an existing ledger. Then seeds the fixtures as below.
#   seed-ledger.sh fixtures [src]   copy the web fixtures (default ../web/public/fixtures) into state/fixtures, the
#                                   volume Caddy serves /fixtures/ from, and split the intervention bundles there
#                                   (the web image is built without fixtures, so its prebuild skips the split).
#                                   Loop exports go in state/fixtures/loop/<name>.loop.json (/w/?recording=<name>).
set -euo pipefail
cd "$(dirname "$0")"

seed_fixtures() {
  local src=${1:-../web/public/fixtures}
  [[ -d $src ]] || { echo "no fixtures at $src" >&2; exit 2; }
  mkdir -p state/fixtures/loop
  cp -a "$src"/. state/fixtures/
  local dir web
  dir=$(cd state/fixtures && pwd)
  web=$(cd ../web && pwd)
  if command -v bun >/dev/null 2>&1; then
    bun "$web/scripts/split-intervention.ts" --fixtures "$dir"
  else   # no bun on the host: the build image's bun, with the splitter's sources mounted read-only
    docker run --rm -v "$dir:/fixtures" -v "$web/scripts:/web/scripts:ro" -v "$web/lib:/web/lib:ro" -w /web \
      oven/bun:1.2.23 bun scripts/split-intervention.ts --fixtures /fixtures
  fi
  echo "seeded state/fixtures from $src"
}

if [[ ${1:-} == fixtures ]]; then
  seed_fixtures "${2:-}"
  exit 0
fi

src=${1:-../runs/fw-chain8}
[[ -f $src/ledger.sqlite ]] || { echo "no ledger at $src" >&2; exit 2; }
[[ -e state/ledger/ledger.sqlite ]] && { echo "state/ledger already has a ledger; move it away first" >&2; exit 3; }
mkdir -p state/ledger state/api
cp -a "$src"/. state/ledger/
echo "seeded state/ledger from $src"
seed_fixtures
chown -R 10001:10001 state 2>/dev/null || echo "note: run as root (or chown -R 10001:10001 deploy/state) so the container can write"
