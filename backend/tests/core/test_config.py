import pytest

from app.core.config import BaseAppSettings


def test_reads_app_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+asyncpg://u:pw@db/x")
    settings = BaseAppSettings()
    assert settings.log_level == "DEBUG"
    assert settings.database_url.get_secret_value() == "postgresql+asyncpg://u:pw@db/x"
    assert "pw" not in repr(settings)


def test_ignores_unprefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_LOG_LEVEL", raising=False)
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    assert BaseAppSettings().log_level == "INFO"
