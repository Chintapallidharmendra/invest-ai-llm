"""Envelope encryption at rest (ADR-028): KEK -> space keys -> object keys.

The only module that imports ``cryptography`` (import-linter). Content fields go through
:func:`app.crypto.fields.encrypt_field` / ``decrypt_field``, files through
:func:`app.crypto.files.encrypt_stream` / ``decrypt_stream``; deleting a key row
(:mod:`app.crypto.shred`) makes its data unrecoverable.
"""

from app.crypto.kek import CryptoError, IntegrityError, KekError
from app.crypto.keys import KeyDestroyed, KeyRef

__all__ = ["CryptoError", "IntegrityError", "KekError", "KeyDestroyed", "KeyRef"]
