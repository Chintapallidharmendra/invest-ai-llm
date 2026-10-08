"""Metadata-only, hash-chained audit (ADR-032; FR-031, NFR-017, NFR-023).

Write events only through :func:`app.audit.writer.record`. Declare a module's events in
``app/<module>/audit_events.py`` as :class:`app.audit.types.AuditEvent` subclasses;
``python -m app.audit.check_models`` (CI) rejects free-text fields. Tests assert
payloads with :func:`app.audit.testing.assert_no_content`.
"""
