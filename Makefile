.PHONY: bootstrap up migrate lint test eval benchmark simulate smoke
bootstrap:
	python3 scripts/bootstrap.py
	uv sync --locked
up:
	docker compose up --build
migrate:
	uv run --locked alembic upgrade head
lint:
	uv run --locked ruff format --check .
	uv run --locked ruff check .
	uv run --locked mypy app
test:
	uv run --locked pytest --cov=app
eval:
	uv run --locked python -m evals.run
benchmark:
	uv run --locked python -m benchmarks.admission
simulate:
	uv run --locked pytest tests/failure_injection tests/concurrency -v
smoke:
	python3 scripts/smoke.py
