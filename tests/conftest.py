"""Repo-root pytest conftest.

Auto-loads `.env` from the repo root before any test imports run, so that
locally-held secrets (ANTHROPIC_API_KEY, TENAME_TEST_DATABASE_URL, ...)
don't need to be re-exported in every shell.

If no `.env` is present, nothing happens — gated integration tests skip
cleanly as before. Existing shell env vars always take precedence (we do
NOT override).
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _REPO_ROOT / ".env"

if _ENV_FILE.is_file():
    load_dotenv(_ENV_FILE, override=False)
