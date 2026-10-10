"""The parser's HTTP service (stdlib only): ``GET /healthz`` and ``POST /parse``.

``POST /parse`` takes ``multipart/form-data`` with:

- ``file``: the document bytes;
- ``kind``: ``pdf | docx | pptx | xlsx | csv``;
- ``limits`` (optional): JSON of :class:`~app.schema.Limits`; it can only lower the
  service's limits.

It answers ``200`` with a :class:`~app.schema.ParseResult`, ``422`` with a
:class:`~app.schema.ParseFailure` (``limit_exceeded``, ``corrupt_file``, ``timeout``,
``unsupported``), or ``400``/``413`` for a malformed request. Parsing happens in a
sandboxed child (``app.sandbox``); at most ``PARSER_CONCURRENCY`` run at once.

Logs carry counts, sizes, durations and error codes only, never content or file names.
"""

import json
import logging
import os
import threading
import time
from email.message import Message
from email.parser import BytesParser
from email.policy import HTTP
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

from pydantic import ValidationError

from app import sandbox
from app.schema import SCHEMA_VERSION, FileKind, Limits, ParseFailure

_log = logging.getLogger("parser.server")

SERVICE_LIMITS: Final = Limits()
# Multipart overhead on top of the largest allowed file.
MAX_REQUEST_BYTES: Final = SERVICE_LIMITS.max_bytes + 1024 * 1024


def _concurrency() -> int:
    try:
        return max(1, int(os.environ.get("PARSER_CONCURRENCY", "2")))
    except ValueError:
        return 2


_slots = threading.BoundedSemaphore(_concurrency())


class BadRequestError(Exception):
    pass


def parse_multipart(content_type: str, body: bytes) -> dict[str, bytes]:
    """Form fields by name (the last one wins). Raises :class:`BadRequestError`."""
    if not content_type.startswith("multipart/form-data"):
        raise BadRequestError("content type")
    message = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    if not message.is_multipart():
        raise BadRequestError("multipart")
    fields: dict[str, bytes] = {}
    for part in message.iter_parts():
        assert isinstance(part, Message)  # noqa: S101
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str):
            continue
        payload = part.get_payload(decode=True)
        fields[name] = payload if isinstance(payload, bytes) else b""
    return fields


def handle_parse(content_type: str, body: bytes) -> tuple[int, bytes]:
    try:
        fields = parse_multipart(content_type, body)
        kind = FileKind(fields["kind"].decode("ascii").strip())
        data = fields["file"]
        requested = Limits.model_validate_json(fields["limits"]) if fields.get("limits") else None
    except (BadRequestError, KeyError, ValueError, UnicodeDecodeError, ValidationError):
        return HTTPStatus.BAD_REQUEST, json.dumps({"error": "bad_request"}).encode()
    limits = SERVICE_LIMITS.capped(requested)
    started = time.monotonic()
    with _slots:
        result = sandbox.run(kind, data, limits)
    elapsed_ms = round((time.monotonic() - started) * 1000)
    if isinstance(result, ParseFailure):
        _log.info(
            "parser.failed",
            extra={"kind": kind.value, "bytes": len(data), "ms": elapsed_ms, "code": result.code},
        )
        return HTTPStatus.UNPROCESSABLE_ENTITY, result.model_dump_json().encode()
    _log.info(
        "parser.parsed",
        extra={
            "kind": kind.value,
            "bytes": len(data),
            "ms": elapsed_ms,
            "text_items": len(result.text),
            "tables": len(result.tables),
            "sheets": len(result.sheets),
            "needs_ocr": len(result.needs_ocr),
        },
    )
    return HTTPStatus.OK, result.model_dump_json().encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "parser"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        """No request lines in the log (they carry nothing useful and could carry names)."""

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send(
                HTTPStatus.OK,
                json.dumps({"status": "ok", "schema_version": SCHEMA_VERSION}).encode(),
            )
        else:
            self._send(HTTPStatus.NOT_FOUND, b'{"error":"not_found"}')

    def do_POST(self) -> None:
        if self.path != "/parse":
            self._send(HTTPStatus.NOT_FOUND, b'{"error":"not_found"}')
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send(HTTPStatus.LENGTH_REQUIRED, b'{"error":"length_required"}')
            return
        if length > MAX_REQUEST_BYTES or length < 0:
            self.close_connection = True
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b'{"error":"limit_exceeded"}')
            return
        body = self.rfile.read(length)
        status, payload = handle_parse(self.headers.get("Content-Type", ""), body)
        self._send(status, payload)


class _JsonFormatter(logging.Formatter):
    _FIELDS: Final = ("kind", "bytes", "ms", "code", "text_items", "tables", "sheets", "needs_ocr")

    def format(self, record: logging.LogRecord) -> str:
        entry = {"level": record.levelname.lower(), "event": record.getMessage()}
        entry |= {k: getattr(record, k) for k in self._FIELDS if hasattr(record, k)}
        return json.dumps(entry)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:  # noqa: S104 (inside the internal network)
    configure_logging()
    sandbox.context()  # start the forkserver (and preload the parsers) before serving
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    _log.info("parser.listening")
    server.serve_forever()
