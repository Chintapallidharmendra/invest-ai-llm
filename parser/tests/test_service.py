"""AC #1 (HTTP service) and AC #4 (sandbox, limits, typed errors) end to end."""

import io
import json
import logging
import threading
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from typing import Any

import pytest

from app import sandbox, server
from app.schema import SCHEMA_VERSION, ErrorCode, FileKind, Limits, ParseFailure, ParseResult
from tests.conftest import corpus_file, pdf_bytes


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def multipart(fields: dict[str, bytes]) -> tuple[str, bytes]:
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for name, value in fields.items():
        out.write(f"--{boundary}\r\n".encode())
        filename = '; filename="upload"' if name == "file" else ""
        out.write(f'Content-Disposition: form-data; name="{name}"{filename}\r\n\r\n'.encode())
        out.write(value + b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", out.getvalue()


def post(
    base_url: str, fields: dict[str, bytes], *, content_type: str | None = None
) -> tuple[int, Any]:
    ctype, body = multipart(fields)
    request = urllib.request.Request(
        f"{base_url}/parse", data=body, headers={"Content-Type": content_type or ctype}
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def get(base_url: str, path: str) -> tuple[int, Any]:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=10) as response:
        return response.status, json.loads(response.read())


def test_healthz(base_url: str) -> None:
    assert get(base_url, "/healthz") == (200, {"status": "ok", "schema_version": SCHEMA_VERSION})


def test_parse_csv_over_http(base_url: str) -> None:
    status, body = post(base_url, {"file": b"a,b\n1,2\n", "kind": b"csv"})
    assert status == 200
    result = ParseResult.model_validate(body)
    assert result.csv is not None
    assert result.csv.rows == [["1", "2"]]


def test_parse_corpus_xlsx_over_http(base_url: str) -> None:
    status, body = post(
        base_url, {"file": corpus_file("model_vardhan_chemicals.xlsx"), "kind": b"xlsx"}
    )
    assert status == 200
    assert body["schema_version"] == SCHEMA_VERSION
    assert [s["name"] for s in body["sheets"]][:2] == ["P&L", "Segments"]


def test_page_limit_is_a_typed_error(base_url: str) -> None:
    status, body = post(base_url, {"file": pdf_bytes(301), "kind": b"pdf"})
    assert status == 422
    assert ParseFailure.model_validate(body).code is ErrorCode.LIMIT_EXCEEDED


def test_request_can_lower_but_not_raise_limits(base_url: str) -> None:
    lowered = Limits(max_rows_per_sheet=1).model_dump_json().encode()
    status, body = post(base_url, {"file": b"a\n1\n2\n", "kind": b"csv", "limits": lowered})
    assert (status, body["code"]) == (422, "limit_exceeded")
    raised = Limits(max_pages=10_000).model_dump_json().encode()
    status, body = post(base_url, {"file": pdf_bytes(301), "kind": b"pdf", "limits": raised})
    assert (status, body["code"]) == (422, "limit_exceeded")


@pytest.mark.parametrize("kind", [b"pdf", b"docx", b"pptx", b"xlsx", b"csv"])
def test_garbage_never_crashes_the_service(base_url: str, kind: bytes) -> None:
    status, body = post(base_url, {"file": b"\x00\xff" * 64 + b"%PDF", "kind": kind})
    assert status == 422
    assert body["code"] in {"corrupt_file", "unsupported"}
    assert get(base_url, "/healthz")[0] == 200


def test_empty_file(base_url: str) -> None:
    status, body = post(base_url, {"file": b"", "kind": b"docx"})
    assert (status, body["code"]) == (422, "corrupt_file")


def test_timeout_while_service_still_answers(base_url: str) -> None:
    """A parse that runs past the wall limit is killed; /healthz answers meanwhile."""
    limits = Limits(wall_s=1.0).model_dump_json().encode()
    data = corpus_file("cim_project_chakra_nilgiri_logistics.pdf")  # 150 pages: far over 1 s
    outcome: dict[str, Any] = {}

    def slow() -> None:
        outcome["response"] = post(base_url, {"file": data, "kind": b"pdf", "limits": limits})

    thread = threading.Thread(target=slow)
    thread.start()
    assert get(base_url, "/healthz")[0] == 200
    thread.join(60)
    status, body = outcome["response"]
    assert (status, body["code"]) == (422, "timeout")


def test_cpu_limit_kills_a_busy_child() -> None:
    result = sandbox.run(
        FileKind.PDF, corpus_file("cim_project_chakra_nilgiri_logistics.pdf"), Limits(cpu_s=1)
    )
    assert isinstance(result, ParseFailure)
    assert result.code is ErrorCode.TIMEOUT


@pytest.mark.parametrize(
    ("fields", "content_type"),
    [
        ({"file": b"x"}, None),  # no kind
        ({"kind": b"csv"}, None),  # no file
        ({"file": b"x", "kind": b"exe"}, None),
        ({"file": b"x", "kind": b"csv", "limits": b"{not json"}, None),
        ({"file": b"x", "kind": b"csv"}, "application/json"),
    ],
)
def test_malformed_requests_are_400(
    base_url: str, fields: dict[str, bytes], content_type: str | None
) -> None:
    status, body = post(base_url, fields, content_type=content_type)
    assert (status, body) == (400, {"error": "bad_request"})


def test_oversized_request_is_413(base_url: str) -> None:
    request = urllib.request.Request(
        f"{base_url}/parse",
        data=b"x",
        headers={
            "Content-Type": "multipart/form-data; boundary=x",
            "Content-Length": str(server.MAX_REQUEST_BYTES + 1),
        },
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=10)
    assert excinfo.value.code == 413


def test_unknown_paths_are_404(base_url: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(f"{base_url}/admin", timeout=10)
    assert excinfo.value.code == 404


def test_logs_carry_no_content(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="parser")
    ctype, body = multipart({"file": b"secret,value\nCANARY-123,9\n", "kind": b"csv"})
    status, _ = server.handle_parse(ctype, body)
    assert status == 200
    formatter = server._JsonFormatter()
    lines = [formatter.format(r) for r in caplog.records]
    assert lines
    assert all("CANARY" not in line and "secret" not in line for line in lines)
    assert json.loads(lines[-1])["kind"] == "csv"
