# invest-ai-llm root Makefile (Story 1.1).
# Later stories plug in via the delegated files below (or mk/*.mk) instead of editing
# this file. Override tool paths if needed, e.g. `make lint UV=/path/to/uv PNPM="npx -y pnpm@12.9.1"`.

UV   ?= uv
PNPM ?= pnpm

BACKEND  := backend
FRONTEND := frontend

CORPUS_MK  := evals/corpus/Makefile.inc
COMPOSE    := deploy/compose.yml
TYPEGEN_SH := $(FRONTEND)/scripts/gen-api-types.sh

define not_available
	@echo "make $@: not available yet ($(1) arrives in $(2))."
endef

.DEFAULT_GOAL := help
.PHONY: help lint typecheck test typegen corpus up down

help:
	@echo "Targets: lint typecheck test typegen corpus up down"

lint:
	cd $(BACKEND) && $(UV) run ruff check .
	cd $(BACKEND) && $(UV) run ruff format --check .
	cd $(BACKEND) && $(UV) run lint-imports
	cd $(FRONTEND) && $(PNPM) exec eslint .

typecheck:
	cd $(BACKEND) && $(UV) run mypy --strict app
ifneq ($(wildcard $(FRONTEND)/src),)
	cd $(FRONTEND) && $(PNPM) exec tsc -p . --noEmit
else
	@echo "make typecheck: frontend skipped (no $(FRONTEND)/src yet; arrives in Story 1.10)."
endif

# pytest exits 5 when no tests are collected; that is fine on the empty skeleton.
test:
	cd $(BACKEND) && { $(UV) run pytest; rc=$$?; [ $$rc -eq 0 ] || [ $$rc -eq 5 ]; }
ifneq ($(wildcard $(FRONTEND)/src),)
	cd $(FRONTEND) && $(PNPM) exec vitest run --passWithNoTests
else
	@echo "make test: frontend skipped (no $(FRONTEND)/src yet; arrives in Story 1.10)."
endif

typegen:
ifneq ($(wildcard $(TYPEGEN_SH)),)
	sh $(TYPEGEN_SH)
else
	$(call not_available,$(TYPEGEN_SH),Story 1.10)
endif

corpus:
ifneq ($(wildcard $(CORPUS_MK)),)
	$(MAKE) -f $(CORPUS_MK) corpus
else
	$(call not_available,$(CORPUS_MK),Story 1.7)
endif

up:
ifneq ($(wildcard $(COMPOSE)),)
	docker compose -f $(COMPOSE) up -d
else
	$(call not_available,$(COMPOSE),Story 1.3)
endif

down:
ifneq ($(wildcard $(COMPOSE)),)
	docker compose -f $(COMPOSE) down
else
	$(call not_available,$(COMPOSE),Story 1.3)
endif

# Extra targets contributed by later stories (one file per story).
-include $(wildcard mk/*.mk)
