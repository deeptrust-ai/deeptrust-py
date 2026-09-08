# Dev scripts. Everything runs through uv, so there is no virtualenv to
# activate and no lockfile drift between machines.

default:
    @just --list

# Create the environment and install everything, adapters included.
install:
    uv sync --all-extras --group dev

test:
    uv run pytest -q

# Watch mode, for when you are actually writing something.
watch:
    uv run pytest -q --looponfail

lint:
    uv run ruff check src tests
    uv run ruff format --check src tests

fmt:
    uv run ruff check --fix src tests
    uv run ruff format src tests

types:
    uv run mypy

# What CI runs. Run this before pushing.
check: lint types test

build:
    uv build

# Sanity check that the package imports from a clean install rather than from
# the source tree, which is how a missing entry in pyproject gets caught.
smoke:
    uv run --isolated --no-project --with dist/*.whl python -c "import deeptrust; from deeptrust.agents import DeepTrust; print(deeptrust.__version__)"

publish: check build
    uv publish

clean:
    rm -rf dist .pytest_cache .mypy_cache .ruff_cache
    find . -name __pycache__ -type d -prune -exec rm -rf {} +
