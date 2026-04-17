#!/usr/bin/env bash
# Start the local Tename dev stack (Postgres in docker compose).
# Waits for Postgres to pass its healthcheck, then prints the connection string.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a; source .env; set +a
fi

POSTGRES_USER="${POSTGRES_USER:-tename}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-tename}"
POSTGRES_DB="${POSTGRES_DB:-tename_dev}"
POSTGRES_TEST_DB="${POSTGRES_TEST_DB:-tename_test}"
POSTGRES_PORT="${POSTGRES_PORT:-5433}"

echo "Starting Tename Postgres on port ${POSTGRES_PORT}..."
docker compose up -d postgres

echo -n "Waiting for Postgres to be ready"
for _ in $(seq 1 30); do
  status="$(docker inspect -f '{{.State.Health.Status}}' tename-postgres 2>/dev/null || echo starting)"
  if [[ "$status" == "healthy" ]]; then
    echo " ✓"
    break
  fi
  echo -n "."
  sleep 1
done

status="$(docker inspect -f '{{.State.Health.Status}}' tename-postgres 2>/dev/null || echo unknown)"
if [[ "$status" != "healthy" ]]; then
  echo
  echo "Postgres failed to become healthy (status: ${status}). Check: docker compose logs postgres" >&2
  exit 1
fi

# Ensure the integration-test database exists. `postgres` is the
# maintenance DB we connect to for the CREATE DATABASE check.
if ! docker exec tename-postgres \
    psql -U "${POSTGRES_USER}" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '${POSTGRES_TEST_DB}'" \
    | grep -q 1; then
  echo "Creating test database ${POSTGRES_TEST_DB}..."
  docker exec tename-postgres \
    psql -U "${POSTGRES_USER}" -d postgres \
    -c "CREATE DATABASE ${POSTGRES_TEST_DB} OWNER ${POSTGRES_USER};" >/dev/null
fi

cat <<EOF

Tename dev stack is up.

  Dev connection string:
    postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${POSTGRES_PORT}/${POSTGRES_DB}

  Test connection string:
    postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${POSTGRES_PORT}/${POSTGRES_TEST_DB}

  Psql shell:
    docker compose --profile tools run --rm dev-tools

  Stop:
    make dev-stop

EOF
