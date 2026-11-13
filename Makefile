UV ?= uv
UVX ?= uvx

.DEFAULT_GOAL := help
.PHONY: help install install-dev update lock test test-verbose lint fmt fmt-check typecheck check \
        notebook shell build env clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install project dependencies and the project itself (editable)
	$(UV) sync

install-dev: ## Install dependencies including the test extra
	$(UV) sync --extra test

update: ## Update dependencies to the latest allowed versions
	$(UV) sync --upgrade

lock: ## Regenerate uv.lock
	$(UV) lock

test: ## Run the test suite
	$(UV) run pytest

test-verbose: ## Run the test suite with verbose output
	$(UV) run pytest -v

lint: ## Lint the code (ruff via uvx, not a project dependency)
	$(UVX) ruff check src

fmt: ## Format the code (ruff via uvx, not a project dependency)
	$(UVX) ruff format src

fmt-check: ## Check formatting without writing changes
	$(UVX) ruff format --check src

typecheck: ## Type-check the code (mypy via uvx, not a project dependency)
	$(UVX) mypy src

check: fmt-check lint test ## Run formatting check, lint and tests

notebook: ## Start JupyterLab
	$(UV) run jupyter lab

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
