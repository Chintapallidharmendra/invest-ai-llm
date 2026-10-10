"""The caller's access context (ADR-023). **Only this module defines it**; every
repository function takes one, and passes ``ctx.rls_settings()`` to
``core.db.transaction(context=...)`` so the database applies the same boundary::

    async with transaction(context=ctx.rls_settings()) as tx:
        ...
"""

import uuid
from dataclasses import dataclass

from app.auth.models import Role


@dataclass(frozen=True, slots=True)
class AccessContext:
    user_id: uuid.UUID
    role: Role
    space_ids: frozenset[uuid.UUID]  # open spaces the user belongs to, read this request
    grant_id: uuid.UUID | None = None  # break-glass grant (ADR-036)

    def rls_settings(self) -> dict[str, str]:
        settings = {"app.user_id": str(self.user_id)}
        if self.grant_id is not None:
            settings["app.grant_id"] = str(self.grant_id)
        return settings

    def can_access(self, space_id: uuid.UUID) -> bool:
        return space_id in self.space_ids
