.PHONY: lint format mypy check

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

format:
	uv run ruff check --fix src/
	uv run ruff format src/ tests/

typecheck:
	uv run mypy src

test:
	uv run pytest tests/ -v

check: lint typecheck test
