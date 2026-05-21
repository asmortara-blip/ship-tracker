# Ship Tracker — developer shortcuts.
#
# These targets mirror what CI runs (.github/workflows/ci.yml) so that
# a green local `make ci` is a strong predictor of a green CI run.

.PHONY: help install test test-fast cov lint ci clean

help:
	@echo "Targets:"
	@echo "  install    Install runtime + test dependencies"
	@echo "  test       Run the full pytest suite (matches CI)"
	@echo "  test-fast  Run pytest without coverage (fastest signal)"
	@echo "  cov        Run pytest with coverage + HTML report in htmlcov/"
	@echo "  lint       Run ruff if available (no-op otherwise)"
	@echo "  ci         Run the same command CI runs"
	@echo "  clean      Remove caches and coverage artifacts"

install:
	python -m pip install --upgrade pip
	pip install -r requirements.txt
	pip install pytest pytest-timeout pytest-cov

test:
	pytest --cov=. --cov-report=term

test-fast:
	pytest

cov:
	pytest --cov=. --cov-report=term --cov-report=html
	@echo "HTML report: htmlcov/index.html"

lint:
	@command -v ruff >/dev/null 2>&1 && ruff check . || echo "ruff not installed; skipping"

ci:
	pytest --cov=. --cov-report=xml --cov-report=term

clean:
	rm -rf .pytest_cache .ruff_cache htmlcov coverage.xml .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
