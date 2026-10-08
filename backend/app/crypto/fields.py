"""Field encryption (ADR-028): one value of one object, AES-256-GCM.

Format: ``version(1) | nonce(12) | ciphertext | tag(16)``, stored in ``*_enc`` bytea
columns. AAD = ``space_id || object_id || field`` (behind a domain label), so a
ciphertext moved to another object, space or column fails with :class:`IntegrityError`.
A fresh random nonce per call means equal plaintexts encrypt differently.
"""

import re
from typing import Final

from app.crypto.kek import IntegrityError, open_sealed, seal
from app.crypto.keys import Executor, KeyRef, object_key

_FIELD: Final = re.compile(r"[a-z][a-z0-9_]{0,62}")
_FIELD_AAD: Final = b"invest-ai/field/v1\x00"

__all__ = ["IntegrityError", "decrypt_field", "decrypt_text", "encrypt_field", "field_aad"]


def field_aad(key_ref: KeyRef, field: str) -> bytes:
    if not _FIELD.fullmatch(field):
        raise ValueError("field must match [a-z][a-z0-9_]{0,62}")
    return _FIELD_AAD + key_ref.space_id.bytes + key_ref.object_id.bytes + field.encode()


async def encrypt_field(
    key_ref: KeyRef, field: str, plaintext: bytes | str, *, executor: Executor | None = None
) -> bytes:
    data = plaintext.encode() if isinstance(plaintext, str) else plaintext
    return seal(await object_key(key_ref, executor), data, field_aad(key_ref, field))


async def decrypt_field(
    key_ref: KeyRef, field: str, ciphertext: bytes, *, executor: Executor | None = None
) -> bytes:
    """Raises :class:`IntegrityError` on tamper or AAD mismatch, ``KeyDestroyed`` if shredded."""
    return open_sealed(
        await object_key(key_ref, executor), bytes(ciphertext), field_aad(key_ref, field)
    )


async def decrypt_text(
    key_ref: KeyRef, field: str, ciphertext: bytes, *, executor: Executor | None = None
) -> str:
    return (await decrypt_field(key_ref, field, ciphertext, executor=executor)).decode()
