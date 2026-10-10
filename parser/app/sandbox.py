"""Run each parse in a child process under resource limits (ADR-027, NFR-025).

Children are forked from a ``forkserver`` that has already imported the light parser
modules, so a request costs a fork, not an interpreter start (Docling itself is imported
in the child, for PDF/DOCX/PPTX only). Each child:

- sets ``RLIMIT_CPU`` (``Limits.cpu_s``) and ``RLIMIT_AS`` (``Limits.address_space_bytes``),
  plus ``RLIMIT_CORE = 0``, before it touches the file;
- parses, and sends back the result as JSON over a pipe.

The parent waits at most ``Limits.wall_s``, then kills the child. A child killed for CPU
time or wall time is ``timeout``; one that runs out of memory is ``limit_exceeded``; any
other death is ``corrupt_file``. Whatever the file does, the service process survives.
"""

import contextlib
import json
import logging
import multiprocessing as mp
import resource
import signal
import time
from multiprocessing.connection import Connection
from typing import Final

from app.schema import ErrorCode, FileKind, Limits, ParseError, ParseFailure, ParseResult

_log = logging.getLogger("parser.sandbox")

# Light modules only: forking after Docling (torch) has been imported is not fork-safe
# everywhere (macOS crashes), so each child imports Docling itself when it needs it.
PRELOAD: Final = ["app.parse"]
_context: mp.context.ForkServerContext | None = None


def context() -> mp.context.ForkServerContext:
    global _context  # noqa: PLW0603 (one forkserver per process)
    if _context is None:
        ctx = mp.get_context("forkserver")
        ctx.set_forkserver_preload(PRELOAD)
        _context = ctx
    return _context


def _set_limits(limits: Limits) -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_s, limits.cpu_s + 5))
    try:
        resource.setrlimit(
            resource.RLIMIT_AS, (limits.address_space_bytes, limits.address_space_bytes)
        )
    except (ValueError, OSError):
        # macOS doesn't support RLIMIT_AS; the container's mem_limit still applies.
        _log.warning("parser.rlimit_as_unsupported")


def _child(conn: Connection, kind: str, data: bytes, limits_json: str) -> None:
    limits = Limits.model_validate_json(limits_json)
    _set_limits(limits)
    from app.parse import parse  # noqa: PLC0415 (preloaded by the forkserver)

    try:
        result: ParseResult | ParseFailure = parse(FileKind(kind), data, limits)
    except ParseError as exc:
        result = exc.failure()
    except MemoryError:
        result = ParseFailure(code=ErrorCode.LIMIT_EXCEEDED, detail="memory")
    except Exception:
        result = ParseFailure(code=ErrorCode.CORRUPT_FILE)
    try:
        conn.send_bytes(result.model_dump_json().encode())
    except MemoryError:
        conn.send_bytes(
            ParseFailure(code=ErrorCode.LIMIT_EXCEEDED, detail="memory").model_dump_json().encode()
        )
    finally:
        conn.close()


def _death(exitcode: int | None, *, timed_out: bool) -> ParseFailure:
    if timed_out or exitcode in {-signal.SIGXCPU, -signal.SIGKILL}:
        return ParseFailure(code=ErrorCode.TIMEOUT)
    if exitcode == -signal.SIGSEGV:
        return ParseFailure(code=ErrorCode.CORRUPT_FILE, detail="crash")
    return ParseFailure(code=ErrorCode.CORRUPT_FILE)


def run(kind: FileKind, data: bytes, limits: Limits) -> ParseResult | ParseFailure:
    """Parse ``data`` in a sandboxed child; never raises for anything the file does."""
    ctx = context()
    receiver, sender = ctx.Pipe(duplex=False)
    child = ctx.Process(
        target=_child, args=(sender, kind.value, data, limits.model_dump_json()), daemon=True
    )
    started = time.monotonic()
    child.start()
    sender.close()
    payload: bytes | None = None
    timed_out = False
    try:
        deadline = started + limits.wall_s
        # Read while waiting: a large result must not block the child on a full pipe.
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            if receiver.poll(min(remaining, 0.5)):
                with contextlib.suppress(EOFError):
                    payload = receiver.recv_bytes()
                break
            if not child.is_alive() and not receiver.poll():
                break
    finally:
        receiver.close()
        if child.is_alive():
            child.kill()
        child.join(5)
    if payload is not None:
        return _decode(payload)
    return _death(child.exitcode, timed_out=timed_out)


def _decode(payload: bytes) -> ParseResult | ParseFailure:
    data = json.loads(payload)
    if data.get("ok") is True:
        return ParseResult.model_validate(data)
    return ParseFailure.model_validate(data)
