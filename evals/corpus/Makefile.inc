# Synthetic corpus targets (Story 1.7). The root Makefile runs `make corpus` through
# this file from the repository root. Override uv with `make corpus UV=/path/to/uv`.
UV ?= uv
CORPUS_DIR := evals/corpus
CORPUS_RUN := cd $(CORPUS_DIR) && $(UV) run --project ../../backend

.PHONY: corpus corpus-verify corpus-test

# Build evals/corpus/build/ (git-ignored), rewrite manifest.yaml, then self-check.
corpus:
	$(CORPUS_RUN) python -m generate
	$(CORPUS_RUN) python check_corpus.py

# CI: rebuild and fail if the committed manifest.yaml would change (non-determinism or
# an uncommitted generator change), then self-check.
corpus-verify:
	$(CORPUS_RUN) python -m generate --check
	$(CORPUS_RUN) python check_corpus.py

corpus-test:
	$(CORPUS_RUN) pytest -q tests
