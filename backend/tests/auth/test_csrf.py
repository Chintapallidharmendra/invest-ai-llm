"""AC #4: the CSRF middleware (unit level, on a tiny app) and the cookie helpers."""

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from app.auth.csrf import (
    CSRF_COOKIE,
    CsrfMiddleware,
    expire_login_cookies,
    origin_allowed,
    set_login_cookies,
    token_matches,
)
from app.auth.settings import get_auth_settings
from tests.auth.conftest import SITE, make_client

TOKEN = "csrf-token-value-0123456789abcdef0123456789a"  # noqa: S105 (test value)


async def _ok(request: Request) -> Response:
    return PlainTextResponse("ok")


_METHODS = ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]


@pytest.fixture
def origins(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_AUTH_SITE_ORIGINS", f'["{SITE}", "https://alt.example/"]')
    get_auth_settings.cache_clear()
    yield
    get_auth_settings.cache_clear()


@pytest.fixture
async def client(origins: None) -> AsyncIterator[httpx.AsyncClient]:
    app = Starlette(
        routes=[
            Route("/api/v1/things", _ok, methods=_METHODS),
            Route("/api/v1/auth/login", _ok, methods=["POST"]),
            Route("/api/v1/auth/set-password", _ok, methods=["POST"]),
            Route("/healthz", _ok, methods=["POST", "GET"]),
        ]
    )
    app.add_middleware(CsrfMiddleware)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=SITE
    ) as test_client:
        test_client.cookies.set(CSRF_COOKIE, TOKEN)
        yield test_client


def _good(**extra: str) -> dict[str, str]:
    return {"Origin": SITE, "X-CSRF-Token": TOKEN, **extra}


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_state_changing_requests_need_token_and_origin(
    client: httpx.AsyncClient, method: str
) -> None:
    assert (await client.request(method, "/api/v1/things", headers=_good())).status_code == 200
    cases = {
        "no token": {"Origin": SITE},
        "wrong token": _good(**{"X-CSRF-Token": TOKEN[:-1] + "X"}),
        "empty token": _good(**{"X-CSRF-Token": ""}),
        "cross-site origin": _good(Origin="https://evil.example"),
        "null origin": _good(Origin="null"),
        "no origin": {"X-CSRF-Token": TOKEN},
        "cross-site fetch": {"X-CSRF-Token": TOKEN, "Sec-Fetch-Site": "cross-site"},
        "same-site fetch": {"X-CSRF-Token": TOKEN, "Sec-Fetch-Site": "same-site"},
    }
    for name, headers in cases.items():
        response = await client.request(method, "/api/v1/things", headers=headers)
        assert response.status_code == 403, name
        assert response.json()["code"] == "csrf_failed", name
        assert response.headers["content-type"] == "application/problem+json"


async def test_same_origin_fetch_metadata_passes_without_origin(
    client: httpx.AsyncClient,
) -> None:
    headers = {"X-CSRF-Token": TOKEN, "Sec-Fetch-Site": "same-origin"}
    assert (await client.post("/api/v1/things", headers=headers)).status_code == 200


async def test_configured_origin_with_trailing_slash(client: httpx.AsyncClient) -> None:
    headers = _good(Origin="https://alt.example")
    assert (await client.post("/api/v1/things", headers=headers)).status_code == 200


async def test_missing_cookie_fails(client: httpx.AsyncClient) -> None:
    client.cookies.clear()
    assert (await client.post("/api/v1/things", headers=_good())).status_code == 403


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_safe_methods_are_unaffected(client: httpx.AsyncClient, method: str) -> None:
    client.cookies.clear()
    response = await client.request(method, "/api/v1/things", headers={"Origin": "https://evil"})
    assert response.status_code == 200


async def test_paths_outside_the_api_are_unaffected(client: httpx.AsyncClient) -> None:
    assert (await client.post("/healthz")).status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/auth/login", "/api/v1/auth/set-password"])
async def test_pre_session_paths_skip_the_token_but_not_the_origin(
    client: httpx.AsyncClient, path: str
) -> None:
    client.cookies.clear()
    assert (await client.post(path, headers={"Origin": SITE})).status_code == 200
    response = await client.post(path, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert (await client.post(path)).status_code == 403


async def test_real_login_endpoint_checks_origin(auth_settings_env: pytest.MonkeyPatch) -> None:
    async with make_client(headers={"Origin": "https://evil.example"}) as client:
        body = {"username": "a", "password": "b"}
        response = await client.post("/api/v1/auth/login", json=body)
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"
    assert response.headers["x-correlation-id"]  # runs inside the correlation middleware


def test_token_matches() -> None:
    assert token_matches("abc", "abc")
    assert not token_matches("abc", "abd")
    assert not token_matches("", "")
    assert not token_matches(None, "abc")
    assert not token_matches("abc", None)
    assert not token_matches("ab", "abc")


def test_origin_allowed() -> None:
    assert origin_allowed(Headers({"origin": SITE}), [SITE])
    assert origin_allowed(Headers({"origin": SITE + "/"}), [SITE])
    assert not origin_allowed(Headers({"origin": "https://testserver.evil"}), [SITE])
    assert not origin_allowed(Headers({}), [SITE])
    assert origin_allowed(Headers({"sec-fetch-site": "same-origin"}), [])
    assert not origin_allowed(Headers({"origin": "null"}), ["null"])


def test_cookie_attributes() -> None:
    response = Response()
    set_login_cookies(response, session_token="s", csrf_token="c")  # noqa: S106
    expire_login_cookies(response)
    assert response.headers.getlist("set-cookie") == [
        "__Host-session=s; HttpOnly; Secure; SameSite=Strict; Path=/",
        "__Host-csrf=c; Secure; SameSite=Strict; Path=/",
        "__Host-session=; Max-Age=0; HttpOnly; Secure; SameSite=Strict; Path=/",
        "__Host-csrf=; Max-Age=0; Secure; SameSite=Strict; Path=/",
    ]
