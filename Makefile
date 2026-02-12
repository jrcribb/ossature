.PHONY: lint format mypy check

lint:
	uv run ruff check src/
	uv run ruff format --check src/

format:
	uv run ruff check --fix src/
	uv run ruff format src/

typecheck:
	uv run mypy src/ntt

check: lint typecheck
