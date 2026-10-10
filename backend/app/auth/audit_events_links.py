"""Audit events of set-password links (ADR-032): metadata only, never the token."""

from typing import ClassVar

from app.audit.types import AuditEvent
from app.auth.models import TokenPurpose


class LinkRedeemed(AuditEvent):
    event_type: ClassVar[str] = "auth.link_redeemed"

    purpose: TokenPurpose
    sessions_revoked: int
