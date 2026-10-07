import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.middleware import accept_or_generate
from tests.core.conftest import LogCapture


def test_valid_correlation_id_is_echoed(client: TestClient, logs: LogCapture) -> None:
    supplied = str(uuid.uuid4())
    response = client.get("/healthz", headers={"X-Correlation-ID": supplied})
    assert response.headers["x-correlation-id"] == supplied
    (access,) = [e for e in logs.events if e["event"] == "http.request"]
    assert access["correlation_id"] == supplied
    assert access["status_code"] == 200
    assert "latency_ms" in access


@pytest.mark.parametrize(
    "supplied",
    ["not-a-uuid", "'; DROP TABLE users; --", "{12345678-1234-5678-1234-567812345678}", ""],
)
def test_invalid_correlation_id_is_replaced(client: TestClient, supplied: str) -> None:
    response = client.get("/healthz", headers={"X-Correlation-ID": supplied})
    generated = uuid.UUID(response.headers["x-correlation-id"])
    assert generated.version == 7


def test_generated_when_missing(client: TestClient) -> None:
    response = client.get("/healthz")
    assert uuid.UUID(response.headers["x-correlation-id"]).version == 7


def test_accept_normalises_case() -> None:
    value = "12345678-ABCD-5678-1234-567812345678"
    assert accept_or_generate(value) == value.lower()
