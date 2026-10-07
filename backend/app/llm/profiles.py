"""Profile loader (ADR-009, NFR-015)."""

from pathlib import Path
from typing import Any, Final, Literal, get_args

import yaml  # type: ignore[import-untyped]  # PyYAML ships via uvicorn[standard]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.llm.errors import LLMConfigError

LogicalModel = Literal["chat", "guard"]
REQUIRED_PROFILES: Final = frozenset(get_args(LogicalModel))
# Set by the gateway on every request; a profile must not override them.
GATEWAY_FIELDS: Final = frozenset(
    {"cache_salt", "priority", "model", "messages", "stream", "stream_options"}
)


class Timeouts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    connect_s: float = Field(2.0, gt=0)
    first_token_s: float | None = Field(None, gt=0)  # streams only
    total_s: float = Field(gt=0)


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    served_model: str = Field(min_length=1)
    max_tokens: int = Field(gt=0)
    temperature: float | None = None
    extra_body: dict[str, Any] = Field(default_factory=dict)
    supports_cache_salt: bool
    supports_priority: bool
    timeouts: Timeouts

    @field_validator("extra_body")
    @classmethod
    def _no_gateway_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        clash = GATEWAY_FIELDS & set(value)
        if clash:
            raise ValueError(f"extra_body must not set gateway-controlled fields: {sorted(clash)}")
        return value


class _ProfilesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: dict[LogicalModel, Profile]


def load_profiles(
    path: Path, base_urls: dict[str, str] | None = None
) -> dict[LogicalModel, Profile]:
    """Load and validate ``profiles.yaml``; apply base-URL overrides."""
    try:
        raw = yaml.safe_load(path.read_text())
        profiles = _ProfilesFile.model_validate(raw).profiles
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise LLMConfigError(f"invalid LLM profiles file {path}: {exc}") from exc

    missing = REQUIRED_PROFILES - set(profiles)
    if missing:
        raise LLMConfigError(f"LLM profiles missing: {sorted(missing)}")
    overrides = base_urls or {}
    unknown = set(overrides) - set(profiles)
    if unknown:
        raise LLMConfigError(f"APP_LLM_BASE_URLS names unknown profiles: {sorted(unknown)}")
    return {
        name: profile.model_copy(update={"base_url": overrides[name]})
        if name in overrides
        else profile
        for name, profile in profiles.items()
    }
