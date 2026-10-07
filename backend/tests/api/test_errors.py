import uuid

from fastapi.testclient import TestClient

from tests.core.conftest import LogCapture

PROBLEM = "application/problem+json"


def test_problem_exception_body(client: TestClient) -> None:
    response = client.get("/test/problem")
    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM
    body = response.json()
    assert body == {
        "type": "urn:invest-ai-llm:problem:deal_locked",
        "title": "Deal is locked",
        "status": 409,
        "detail": "Try later",
        "instance": "/test/problem",
        "code": "deal_locked",
        "correlation_id": response.headers["x-correlation-id"],
        "retry_after_s": 5,
    }


def test_validation_error_lists_field_paths_without_values(client: TestClient) -> None:
    response = client.post("/test/deals", json={"name": "Project Falcon", "size_crore": "lots"})
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["errors"] == [{"loc": ["body", "size_crore"], "type": "int_parsing"}]
    assert "lots" not in response.text
    assert "Falcon" not in response.text


def test_unhandled_error_hides_exception_text(client: TestClient, logs: LogCapture) -> None:
    response = client.get("/test/boom")
    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM
    body = response.json()
    assert body["code"] == "internal_error"
    assert uuid.UUID(body["correlation_id"])
    assert response.headers["x-correlation-id"] == body["correlation_id"]
    assert "secret text" not in response.text

    assert "secret text" not in logs.text
    (failure,) = [e for e in logs.events if e["event"] == "http.unhandled_exception"]
    assert failure["exc_type"] == "ValueError"
    assert failure["exc_location"].startswith("tests.api.conftest:boom:")
    assert failure["correlation_id"] == body["correlation_id"]


def test_unknown_route_is_not_found_problem(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist?q=secret")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert body["instance"] == "/api/v1/does-not-exist"
    assert body["detail"] is None


def test_method_not_allowed_keeps_allow_header(client: TestClient) -> None:
    response = client.post("/healthz")
    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"
    assert "GET" in response.headers["allow"]
