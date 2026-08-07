default: lint test

install:
    uv sync --locked

lint: lint-ruff fmt-check typecheck

lint-ruff:
    uv run ruff check .

fmt:
    uv run ruff format .

fmt-check:
    uv run ruff format --check .

typecheck: lint-mypy lint-basedpyright lint-ty lint-pyrefly

lint-mypy:
    uv run mypy
    uv run mypy --python-version 3.11
    uv run mypy tests/type_checking/typesafety.py

lint-basedpyright:
    uv run --with basedpyright basedpyright

lint-ty:
    uv run --with ty ty check src/corrode/ tests/type_checking/typesafety.py

lint-pyrefly:
    uv run --with pyrefly pyrefly check src/corrode/ tests/type_checking/typesafety.py

test:
    uv run pytest

test-cov:
    uv run pytest --cov=corrode --cov-report=term-missing --cov-fail-under=95

build:
    uv build

# live preview of the current working tree (single version)
docs:
    uv run --group docs mkdocs serve

docs-build:
    uv run --group docs mkdocs build --strict

# preview the published multi-version site from the local gh-pages branch
docs-versions:
    uv run --group docs mike serve
