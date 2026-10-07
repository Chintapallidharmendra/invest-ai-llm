"""Typed LLM errors (ADR-009, ADR-024).

Callers fail closed: guardrails turn any :class:`LLMError` into a refusal. Messages
never contain prompt or response text, so they are safe to log.
"""


class LLMError(Exception):
    """Base for failures talking to a model server."""

    code = "llm_error"


class LLMUnavailable(LLMError):  # noqa: N818 (name fixed by Story 1.5)
    """The server could not be reached (after one retry) or returned 5xx/429."""

    code = "llm_unavailable"


class LLMStreamInterrupted(LLMUnavailable):
    """A stream broke off before it finished. Discard any partial text."""

    code = "llm_stream_interrupted"


class LLMTimeout(LLMError):  # noqa: N818 (name fixed by Story 1.5)
    """Connect, first-token or total deadline exceeded."""

    code = "llm_timeout"


class LLMRequestError(LLMError):
    """The server rejected the request (4xx): a gateway or profile bug, not content."""

    code = "llm_request_rejected"


class LLMSchemaError(LLMError):
    """Structured output did not parse or did not match the schema."""

    code = "llm_schema_error"


class MissingCacheSalt(ValueError):  # noqa: N818 (name fixed by ADR-031)
    """A model call was made without ``salt_subject``. A programming error, so it is not
    an :class:`LLMError` and is never turned into a refusal silently."""


class LLMConfigError(RuntimeError):
    """Invalid profiles or an unsafe configuration; the gateway refuses to start."""
