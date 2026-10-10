"""Download tokens (FR-024): 24 h, for one user, only ``sha256(token)`` stored.

A token is usable only by the user it was issued to, with a session of theirs, and only
while they can still see the output (owner-only RLS includes current membership).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import generate_token, hash_token
from app.outputs.models import DownloadToken, Output
from app.outputs.settings import get_outputs_settings
from app.spaces.access import AccessContext

DOWNLOAD_PATH = "/api/v1/downloads/"


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str  # raw: returned once, never stored or logged
    expires_at: datetime

    @property
    def url(self) -> str:
        return f"{DOWNLOAD_PATH}{self.token}"


async def issue(
    tx: AsyncSession, ctx: AccessContext, output: Output, *, now: datetime
) -> IssuedToken:
    token = generate_token()
    expires_at = now + _ttl()
    await tx.execute(
        insert(DownloadToken).values(
            output_id=output.id,
            space_id=output.space_id,
            user_id=ctx.user_id,
            token_hash=hash_token(token),
            expires_at=expires_at,
            created_at=now,
        )
    )
    return IssuedToken(token, expires_at)


async def redeem(
    tx: AsyncSession, ctx: AccessContext, token: str, *, now: datetime
) -> Output | None:
    """The output behind a valid token of the caller (counting the use), else ``None``."""
    row = (
        await tx.execute(
            select(DownloadToken.id, Output)
            .join(Output, Output.id == DownloadToken.output_id)
            .where(
                DownloadToken.token_hash == hash_token(token),
                DownloadToken.user_id == ctx.user_id,
                DownloadToken.expires_at > now,
                Output.owner_user_id == ctx.user_id,
                Output.deleted_at.is_(None),
                Output.expires_at > now,
            )
        )
    ).first()
    if row is None:
        return None
    token_id, output = row
    if not ctx.can_access(output.space_id):
        return None
    await tx.execute(
        update(DownloadToken)
        .where(DownloadToken.id == token_id)
        .values(used_count=DownloadToken.used_count + 1)
        .execution_options(synchronize_session=False)
    )
    return output


def _ttl() -> timedelta:
    return timedelta(seconds=get_outputs_settings().link_ttl_s)
