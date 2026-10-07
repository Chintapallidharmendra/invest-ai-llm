"""Unit tests: salt derivation, profile loading, guard parsing, settings (AC #2, #4)."""

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml  # type: ignore[import-untyped]
from pydantic import SecretStr, ValidationError

from app.llm import GuardVerdict, MissingCacheSalt, parse_guard_output
from app.llm.errors import LLMConfigError
from app.llm.profiles import load_profiles
from app.llm.salt import CacheSalter
from app.llm.settings import DEFAULT_PROFILES_PATH, LLMSettings
from tests.conftest import LLM_SALT_SECRET
from tests.llm.conftest import USER_A, USER_B, expected_salt


def test_salt_is_hmac_base64url_per_user() -> None:
    salter = CacheSalter(SecretStr(LLM_SALT_SECRET))
    salt_a = salter.salt(USER_A)
    assert salt_a == expected_salt(USER_A)
    assert len(salt_a) == 43
    assert set(salt_a) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    assert salter.salt(UUID(USER_A)) == salt_a
    assert salter.salt(USER_B) != salt_a
    assert CacheSalter(SecretStr("another-secret-of-at-least-32-chars!")).salt(USER_A) != salt_a


@pytest.mark.parametrize("subject", [None, "", "   "])
def test_salt_requires_subject(subject: str | None) -> None:
    with pytest.raises(MissingCacheSalt):
        CacheSalter(SecretStr(LLM_SALT_SECRET)).salt(subject)


def test_default_profiles() -> None:
    profiles = load_profiles(DEFAULT_PROFILES_PATH)
    chat, guard = profiles["chat"], profiles["guard"]
    assert (chat.served_model, guard.served_model) == ("chat", "guard")
    assert chat.base_url == "http://vllm-chat:8000/v1"
    assert chat.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert guard.extra_body == {}
    assert chat.supports_priority
    assert not guard.supports_priority
    assert chat.supports_cache_salt
    assert guard.supports_cache_salt
    assert (chat.timeouts.connect_s, chat.timeouts.first_token_s, chat.timeouts.total_s) == (
        2,
        10,
        60,
    )
    assert (guard.timeouts.connect_s, guard.timeouts.total_s) == (2, 3)


def test_base_url_overrides() -> None:
    profiles = load_profiles(DEFAULT_PROFILES_PATH, {"guard": "http://127.0.0.1:9/v1"})
    assert profiles["guard"].base_url == "http://127.0.0.1:9/v1"
    assert profiles["chat"].base_url == "http://vllm-chat:8000/v1"
    with pytest.raises(LLMConfigError, match="unknown profiles"):
        load_profiles(DEFAULT_PROFILES_PATH, {"embed": "http://x/v1"})


def _write(tmp_path: Path, data: Any) -> Path:
    path = tmp_path / "profiles.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def _default() -> dict[str, Any]:
    return dict(yaml.safe_load(DEFAULT_PROFILES_PATH.read_text()))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["profiles"].pop("guard"), "missing"),
        (
            lambda d: d["profiles"]["chat"]["extra_body"].update(cache_salt="x"),
            "gateway-controlled",
        ),
        (lambda d: d["profiles"]["chat"]["extra_body"].update(priority=0), "gateway-controlled"),
        (lambda d: d["profiles"]["chat"].update(typo_field=1), "typo_field"),
        (lambda d: d["profiles"].update(embed=d["profiles"]["chat"]), "embed"),
        (lambda d: d["profiles"]["guard"].pop("supports_cache_salt"), "supports_cache_salt"),
    ],
)
def test_invalid_profiles_rejected(tmp_path: Path, mutate: Any, message: str) -> None:
    data = _default()
    mutate(data)
    with pytest.raises(LLMConfigError, match=message):
        load_profiles(_write(tmp_path, data))


def test_missing_profiles_file(tmp_path: Path) -> None:
    with pytest.raises(LLMConfigError):
        load_profiles(tmp_path / "absent.yaml")


@pytest.mark.parametrize(
    ("text", "verdict"),
    [
        ("Safety: Safe\nCategories: None", GuardVerdict("safe")),
        (
            "Safety: Unsafe\nCategories: Violent, Non-violent Illegal Acts",
            GuardVerdict("unsafe", frozenset({"Violent", "Non-violent Illegal Acts"})),
        ),
        (
            "Safety: Controversial\nCategories: Politically Sensitive Topics\nRefusal: No",
            GuardVerdict("controversial", frozenset({"Politically Sensitive Topics"}), False),
        ),
        (
            "safety: unsafe\ncategories: Jailbreak\nRefusal: Yes",
            GuardVerdict("unsafe", frozenset({"Jailbreak"}), True),
        ),
        ("I think this is fine.", GuardVerdict("controversial", frozenset({"parse_error"}))),
        (
            "Safety: Probably\nCategories: None",
            GuardVerdict("controversial", frozenset({"parse_error"})),
        ),
        ("", GuardVerdict("controversial", frozenset({"parse_error"}))),
        (None, GuardVerdict("controversial", frozenset({"parse_error"}))),
    ],
)
def test_parse_guard_output(text: str | None, verdict: GuardVerdict) -> None:
    parsed = parse_guard_output(text)
    assert parsed == verdict
    assert parsed.is_safe == (verdict.safety == "safe")


def test_settings_secret_required_and_long(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_LLM_CACHE_SALT_SECRET", raising=False)
    with pytest.raises(ValidationError):
        LLMSettings()
    monkeypatch.setenv("APP_LLM_CACHE_SALT_SECRET", "too-short")
    with pytest.raises(ValidationError, match="at least 32"):
        LLMSettings()
    monkeypatch.setenv("APP_LLM_CACHE_SALT_SECRET", LLM_SALT_SECRET)
    monkeypatch.setenv("APP_LLM_BASE_URLS", '{"chat": "http://127.0.0.1:1/v1"}')
    monkeypatch.setenv("APP_LLM_PREFIX_CACHING_DISABLED", "true")
    settings = LLMSettings()
    assert settings.base_urls == {"chat": "http://127.0.0.1:1/v1"}
    assert settings.prefix_caching_disabled
    assert LLM_SALT_SECRET not in repr(settings)


def test_settings_secret_from_compose_secret_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APP_LLM_CACHE_SALT_SECRET", raising=False)
    (tmp_path / "cache_salt_secret").write_text(LLM_SALT_SECRET)
    settings = LLMSettings(_secrets_dir=tmp_path)
    assert settings.cache_salt_secret.get_secret_value() == LLM_SALT_SECRET
