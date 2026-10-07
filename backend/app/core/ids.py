"""Identifiers (ADR-005). DB rows default to ``uuidv7()``; Python-side IDs use this."""

import uuid

from uuid_utils.compat import uuid7


def new_uuid7() -> uuid.UUID:
    return uuid7()
