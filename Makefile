.PHONY: help install test lint format typecheck check clean

help:
	@echo "Tename dev targets:"
	@echo "  make install    - sync dev dependencies with uv"
	@echo "  make test       - run pytest"
	@echo "  make lint       - run ruff check"
	@echo "  make format     - run ruff format (and fix lint issues)"
	@echo "  make typecheck  - run pyright"
	@echo "  make check      - lint + typecheck + test"
	@echo "  make clean      - remove caches, build artifacts, and .venv"

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
