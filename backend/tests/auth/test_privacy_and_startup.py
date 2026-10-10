"""AC #6: no secrets in audit rows, logs or responses. AC #7: start-up wiring."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.audit import registry
from app.audit.check_models import violations
from app.audit.testing import assert_no_content
from app.auth.policy import BreachedListError, load_breached_list
from app.auth.settings import get_auth_settings
from app.core import readiness
from app.crypto import kek as kek_module
from app.crypto.settings import get_crypto_settings
from app.worker import main as worker_main
from tests.auth.conftest import (
    AuthEnv,
    audit_rows,
    create_user,
    csrf_headers,
    login,
    make_client,
    session_cookie,
)
from tests.core.conftest import LogCapture, logs

__all__ = ["logs"]

PLANTED_PASSWORD = "Planted-Secret-Passphrase-9931"  # noqa: S105  gitleaks:allow (test value)
PLANTED_USERNAME = "planted.user.7731"


@pytest.mark.db
async def test_no_secret_in_audit_logs_or_responses(auth_env: AuthEnv, logs: LogCapture) -> None:
    await create_user(PLANTED_USERNAME, password=PLANTED_PASSWORD)
    texts: list[str] = []
    async with make_client() as client:
        for username, password in [
            ("unknown." + PLANTED_USERNAME, PLANTED_PASSWORD),
            (PLANTED_USERNAME, PLANTED_PASSWORD + "-wrong"),
            (PLANTED_USERNAME, PLANTED_PASSWORD),
        ]:
            response = await login(client, username, password)
            texts.append(response.text)
        token = session_cookie(client)
        csrf = client.cookies.get("__Host-csrf")
        texts.append((await client.get("/api/v1/auth/me")).text)
        response = await client.post("/api/v1/auth/logout", headers=csrf_headers(client))
        texts.append(response.text)
    assert token
    assert csrf

    rows = await audit_rows()
    assert {r.event_type for r in rows} >= {
        "auth.login_failed",
        "auth.login_succeeded",
        "auth.logged_out",
    }
    for row in rows:
        assert_no_content(row.payload)
    secrets = [PLANTED_PASSWORD, token, csrf]
    for haystack in [*texts, logs.text, *(str(r.payload) for r in rows)]:
        for secret in secrets:
            assert secret not in haystack
    # The username may appear in /me (it's the caller's own), but never in logs or audit.
    for haystack in [logs.text, *(str(r.payload) for r in rows)]:
        assert PLANTED_USERNAME not in haystack


def test_every_auth_event_is_metadata_only() -> None:
    registry.discover()
    events = {k: v for k, v in registry.registered().items() if k.startswith("auth.")}
    # Later stories add auth events in audit_events_*.py files; these must be present.
    assert set(events) >= {
        "auth.login_succeeded",
        "auth.login_failed",
        "auth.account_locked",
        "auth.logged_out",
        "auth.access_denied",
    }
    for model in events.values():
        assert violations(model) == [], model


# --- AC #7 ------------------------------------------------------------------------------


def test_missing_breached_list_fails_app_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_AUTH_BREACHED_PASSWORDS_PATH", str(tmp_path / "missing.txt"))
    get_auth_settings.cache_clear()
    load_breached_list.cache_clear()
    try:
        with pytest.raises(BreachedListError), TestClient(create_app(configure_logs=False)):
            pass
    finally:
        get_auth_settings.cache_clear()
        load_breached_list.cache_clear()


def test_readyz_lists_kek_loaded_when_the_kek_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readiness, "_checks", {})
    monkeypatch.setenv("APP_CRYPTO_KEK_PATH", str(tmp_path / "no-kek"))
    get_crypto_settings.cache_clear()
    kek_module.reset_kek()
    try:
        with TestClient(create_app(configure_logs=False)) as client:
            response = client.get("/readyz")
            assert response.status_code == 503
            assert "kek_loaded" in response.json()["failing_checks"]
            assert {"llm.chat", "llm.guard", "kek_loaded"} <= set(readiness._checks)
        # Shutdown removes them again.
        assert not {"llm.chat", "llm.guard", "kek_loaded"} & set(readiness._checks)
    finally:
        get_crypto_settings.cache_clear()
        kek_module.reset_kek()


async def test_worker_registers_kek_and_llm_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(worker_main.kek, "register_checks", lambda: calls.append("kek"))
    monkeypatch.setattr(worker_main.llm_readiness, "register_checks", lambda: calls.append("llm"))

    monkeypatch.setattr(worker_main.service, "register_checks", lambda _: None)

    class _StopError(Exception):
        pass

    def _stop_here(*_: object, **__: object) -> None:
        raise _StopError

    # Stop right after the checks are registered, before anything starts.
    monkeypatch.setattr(worker_main, "Runner", _stop_here)
    with pytest.raises(_StopError):
        await worker_main.run_worker(install_signal_handlers=False)
    assert calls == ["kek", "llm"]


async def test_app_client_fixture_still_works(app_client: httpx.AsyncClient) -> None:
    # The shared fixture (no lifespan) is unaffected by the new middleware on GETs.
    assert (await app_client.get("/healthz")).status_code == 200
