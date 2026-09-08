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

# Clears dist first, so `smoke` cannot pick up a wheel from an older name or
# version and pass on the wrong artifact.
build:
    rm -rf dist
    uv build

# Installs the built wheel on its own and imports it, which is what catches a
# package missing from pyproject: the source tree would have imported fine.
smoke:
    uv run --isolated --no-project --with dist/*.whl python -c "import deeptrust; from deeptrust.agents import DeepTrust; print(deeptrust.__version__)"

# Releases go through the release workflow on a tag, so that publishing uses
# PyPI trusted publishing and never a token on someone's laptop. This target is
# for a manual upload when that is unavailable.
publish: check build
    uv publish

clean:
    rm -rf dist .pytest_cache .mypy_cache .ruff_cache
    find . -name __pycache__ -type d -prune -exec rm -rf {} +
