#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

export SMOKE_STATE_DIR
SMOKE_STATE_DIR=$(mktemp -d)
artifacts="${SMOKE_ARTIFACT_DIR:-container-smoke-artifacts}"
mkdir -p "$artifacts"
compose=(docker compose --project-name "xhuma-smoke-${GITHUB_RUN_ID:-$$}-${GITHUB_RUN_ATTEMPT:-1}" -f docker-compose.smoke.yml)

cleanup() {
  status=$?
  trap - EXIT
  "${compose[@]}" ps --all > "$artifacts/containers.txt" 2>&1 || true
  "${compose[@]}" logs --no-color > "$artifacts/containers.log" 2>&1 || true
  if [ "$status" -ne 0 ]; then
    cat "$artifacts/containers.log"
  fi
  "${compose[@]}" down --volumes --remove-orphans || status=1
  rm -rf "$SMOKE_STATE_DIR"
  exit "$status"
}
trap cleanup EXIT

uv run --locked python scripts/container_smoke/prepare.py
"${compose[@]}" build --pull xhuma driver
"${compose[@]}" up --detach --wait --wait-timeout 120
"${compose[@]}" run --rm --no-deps driver 2>&1 | tee "$artifacts/journey.log"

# Prove the async SQLAlchemy/asyncpg path actually committed audit events.
"${compose[@]}" exec -T postgres psql -U postgres -d xhuma -v ON_ERROR_STOP=1 <<'SQL' 2>&1 | tee "$artifacts/audit.log"
SELECT action, outcome, count(*) FROM audit_event GROUP BY action, outcome ORDER BY action, outcome;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM audit_event WHERE outcome <> 'ok') THEN
    RAISE EXCEPTION 'Journey persisted unsuccessful audit events';
  END IF;
  IF (SELECT count(DISTINCT action) FROM audit_event WHERE action IN (
    'pds_lookup', 'sds_trace', 'fhir_endpoint_trace',
    'sds_self_lookup', 'gpconnect_request', 'store_ccda', 'iti38_document_query', 'iti39_document_retrieve'
  )) <> 8 THEN
    RAISE EXCEPTION 'Missing committed journey audit events';
  END IF;
  IF (SELECT count(*) FROM audit_event WHERE action = 'iti38_document_query') <> 2
     OR (SELECT count(*) FROM audit_event WHERE action = 'iti39_document_retrieve') <> 2 THEN
    RAISE EXCEPTION 'Cold and warm journeys must both commit query/retrieval audits';
  END IF;
END $$;
SQL
