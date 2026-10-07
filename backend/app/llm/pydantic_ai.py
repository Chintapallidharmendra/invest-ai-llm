"""PydanticAI model factory (ADR-009, ADR-038).

``assistant`` (Story 3.3) builds agents through this factory and never constructs
OpenAI clients itself. The model uses the gateway's ``AsyncOpenAI`` client for the
profile (base URL, connect timeout, no SDK retries), and the returned settings carry the
per-user ``cache_salt``, the priority and the profile's ``extra_body`` (thinking off).

Pass the settings on every run: ``agent.run(prompt, model=model, model_settings=settings)``.
"""

from uuid import UUID

from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from app.llm.gateway import Gateway, get_gateway
from app.llm.profiles import LogicalModel
from app.llm.types import RequestPriority


def pydantic_ai_model(
    salt_subject: str | UUID | None,
    priority: RequestPriority,
    *,
    profile: LogicalModel = "chat",
    gateway: Gateway | None = None,
) -> tuple[OpenAIChatModel, OpenAIChatModelSettings]:
    """Return ``(model, model_settings)`` for one user's agent run.

    Raises :class:`app.llm.errors.MissingCacheSalt` without a ``salt_subject``.
    """
    gw = gateway or get_gateway()
    config = gw.profile(profile)
    settings = OpenAIChatModelSettings(
        max_tokens=config.max_tokens,
        timeout=config.timeouts.total_s,
        extra_body=gw.extra_body(profile, salt_subject, priority),
    )
    if config.temperature is not None:
        settings["temperature"] = config.temperature
    model = OpenAIChatModel(
        config.served_model,
        provider=OpenAIProvider(openai_client=gw.openai_client(profile)),
        settings=settings,
    )
    return model, settings
