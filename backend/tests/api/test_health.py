import pytest
from fastapi.testclient import TestClient

from app.core import readiness


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness, "_checks", {})


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_ok_when_all_checks_pass(client: TestClient) -> None:
    async def ok() -> None:
        return None

    readiness.register_check("database", ok)
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readyz_503_lists_only_failing_names(client: TestClient) -> None:
    async def ok() -> None:
        return None

    async def broken() -> None:
        raise ConnectionError("vllm at 10.0.0.5 refused: secret")

    readiness.register_check("database", ok)
    readiness.register_check("vllm_chat", broken)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "not_ready"
    assert body["failing_checks"] == ["vllm_chat"]
    assert "database" not in response.text
    assert "secret" not in response.text


def test_metrics_exposes_prometheus_text(client: TestClient) -> None:
    client.get("/healthz")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text
