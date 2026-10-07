"""Readiness checks for the model servers (ADR-033).

``register_checks()`` adds ``llm.chat`` and ``llm.guard`` to the ``/readyz`` registry.
It is idempotent; call it once at process start-up (API and worker). The guard check
is a safety dependency: without it the system refuses work (ADR-024).
"""

from collections.abc import Callable
from typing import Final

from app.core import readiness
from app.llm.gateway import Gateway, get_gateway
from app.llm.profiles import LogicalModel

CHECK_NAMES: Final[dict[LogicalModel, str]] = {"chat": "llm.chat", "guard": "llm.guard"}


def register_checks(gateway: Callable[[], Gateway] = get_gateway) -> None:
    for logical, check_name in CHECK_NAMES.items():

        async def check(logical: LogicalModel = logical) -> bool:
            return await gateway().health(logical)

        readiness.unregister_check(check_name)
        readiness.register_check(check_name, check)


def unregister_checks() -> None:
    for check_name in CHECK_NAMES.values():
        readiness.unregister_check(check_name)
