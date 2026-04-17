.PHONY: help install test lint format typecheck check clean \
        dev dev-stop dev-reset migrate migrate-create

help:
	@echo "Tename dev targets:"
	@echo "  make install              - sync dev dependencies with uv"
	@echo "  make test                 - run pytest"
	@echo "  make lint                 - run ruff check"
	@echo "  make format               - run ruff format (and fix lint issues)"
	@echo "  make typecheck            - run pyright"
	@echo "  make check                - lint + typecheck + test"
	@echo "  make clean                - remove caches, build artifacts, and .venv"
	@echo ""
	@echo "Local dev environment:"
	@echo "  make dev                  - start Postgres via docker compose"
	@echo "  make dev-stop             - stop the dev stack (volumes preserved)"
	@echo "  make dev-reset            - stop + remove volumes + restart (destructive)"
	@echo "  make migrate              - apply alembic migrations to HEAD"
	@echo "  make migrate-create name=<slug>"
	@echo "                            - scaffold a new alembic revision"

install:
	uv sync --all-extras

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run pyright

check: lint typecheck test

clean:
	rm -rf .venv
	rm -rf .pytest_cache .ruff_cache .mypy_cache .pyright
	rm -rf build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete

dev:
	scripts/dev-setup.sh

dev-stop:
	docker compose stop

dev-reset:
	scripts/dev-teardown.sh --volumes
	scripts/dev-setup.sh

migrate:
	uv run alembic upgrade head

migrate-create:
	@if [ -z "$(name)" ]; then \
		echo "Usage: make migrate-create name=<slug>"; exit 2; \
	fi
	uv run alembic revision -m "$(name)"
