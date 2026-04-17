#!/usr/bin/env bash
# Stop the local Tename dev stack.
# With --volumes (or -v), also deletes the Postgres data volume after a confirmation prompt.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DELETE_VOLUMES=0
for arg in "$@"; do
  case "$arg" in
    -v|--volumes)
      DELETE_VOLUMES=1
      ;;
    -h|--help)
      echo "Usage: $(basename "$0") [--volumes]"
      echo "  --volumes  Also remove Docker volumes (deletes all local DB data; prompts for confirmation)."
      exit 0
      ;;
    *)
      echo "Unknown arg: $arg" >&2
      exit 2
      ;;
  esac
done

if [[ $DELETE_VOLUMES -eq 1 ]]; then
  echo "This will DELETE the tename_pgdata volume and all local Postgres data."
  read -r -p "Type 'yes' to continue: " confirm
  if [[ "$confirm" != "yes" ]]; then
    echo "Aborted."
    exit 1
  fi
  docker compose down -v
  echo "Stack stopped; volumes removed."
else
  docker compose down
  echo "Stack stopped; volumes preserved."
fi
