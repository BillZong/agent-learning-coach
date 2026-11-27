UV ?= uv
UVX ?= uvx

.DEFAULT_GOAL := help
.PHONY: help install install-dev install-notebook update lock test test-verbose test-cov lint fmt fmt-check \
        typecheck check ci eval notebook shell build env clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install project dependencies and the project itself (editable)
	$(UV) sync

install-dev: ## Install dependencies including the dev toolchain
	$(UV) sync --group dev

install-notebook: ## Install dependencies including the notebook toolchain
	$(UV) sync --group notebook

update: ## Update dependencies to the latest allowed versions
	$(UV) sync --upgrade

lock: ## Regenerate uv.lock
	$(UV) lock

test: ## Run the test suite
	$(UV) run --group dev pytest

test-verbose: ## Run the test suite with verbose output
	$(UV) run --group dev pytest -v

test-cov: ## Run the test suite with coverage and the coverage gate
	$(UV) run --group dev pytest --cov=agent_learning_coach --cov-report=term-missing --cov-fail-under=90

lint: ## Lint the code (ruff from the dev dependency group)
	$(UV) run --group dev ruff check .

fmt: ## Format the code (ruff from the dev dependency group)
	$(UV) run --group dev ruff format .

fmt-check: ## Check formatting without writing changes
	$(UV) run --group dev ruff format --check .

typecheck: ## Type-check the code (mypy from the dev dependency group)
	$(UV) run --group dev mypy src tests examples

eval: ## Run the evaluation suite (behavior acceptance; non-zero on failure)
	$(UV) run --group dev python examples/reliability_report.py

check: fmt-check lint typecheck test eval ## Run formatting check, lint, type check, tests and the evaluation suite

ci: fmt-check lint typecheck test-cov eval build ## Run exactly what CI runs (before pushing)


notebook: ## Start JupyterLab
	$(UV) run --group notebook jupyter lab

shell: ## Start an interactive Python shell with the project installed
	$(UV) run python

build: ## Build the wheel and sdist
	$(UV) build

env: ## Create .env from .env.example if it does not exist
	@if [ ! -f .env ]; then cp .env.example .env && echo "created .env from .env.example"; \
	else echo ".env already exists, left unchanged"; fi

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache build dist
	find . -path ./.venv -prune -o -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -path ./.venv -prune -o -type d -name '*.egg-info' -prune -exec rm -rf {} +
