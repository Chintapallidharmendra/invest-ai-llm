"""KEK rotation (ADR-028): re-wrap every space key under a new KEK in one transaction.

No data is re-encrypted: object keys are wrapped by space keys, which don't change. Runs
as ``app_migrator`` (``app_rw`` has no UPDATE on ``space_keys``)::

    python -m app.crypto.rotate --new-kek /path/to/new_kek [--new-version N]

then install the new KEK file, set ``APP_CRYPTO_KEK_VERSION`` to the new version and
restart api and worker. Until then they refuse to unwrap (version mismatch), failing
closed. The command prints counts only, never key material.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.crypto.cache import get_key_cache
from app.crypto.kek import CryptoError, Kek, KekError, get_kek
from app.crypto.keys import space_key_aad
from app.crypto.models import SpaceKey


async def rotate_kek(
    new_kek: Kek, *, current: Kek | None = None, migrator_url: str | None = None
) -> int:
    """Re-wrap all space keys from ``current`` (the loaded KEK) to ``new_kek``.

    All or nothing: one transaction, with ``space_keys`` locked against concurrent
    inserts. Returns the number of keys re-wrapped.
    """
    current = current or get_kek()
    if new_kek.version <= current.version:
        raise KekError("the new KEK version must be greater than the current one")
    url = migrator_url or get_settings().migrator_database_url.get_secret_value()
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("LOCK TABLE space_keys IN SHARE ROW EXCLUSIVE MODE"))
            rows = (
                await conn.execute(
                    select(SpaceKey.space_id, SpaceKey.wrapped_key, SpaceKey.kek_version)
                )
            ).all()
            for space_id, wrapped, version in rows:
                if version != current.version:
                    raise KekError(
                        f"a space key is wrapped by KEK version {version}, not {current.version}"
                    )
                aad = space_key_aad(space_id)
                await conn.execute(
                    update(SpaceKey)
                    .where(SpaceKey.space_id == space_id)
                    .values(
                        wrapped_key=new_kek.wrap(current.unwrap(bytes(wrapped), aad), aad),
                        kek_version=new_kek.version,
                    )
                )
    finally:
        await engine.dispose()
    get_key_cache().clear()
    return len(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.crypto.rotate", description=__doc__)
    parser.add_argument("--new-kek", type=Path, required=True, help="file holding the new KEK")
    parser.add_argument("--new-version", type=int, help="default: current version + 1")
    args = parser.parse_args(argv)
    try:
        current = get_kek()
        new_kek = Kek.from_file(args.new_kek, args.new_version or current.version + 1)
        count = asyncio.run(rotate_kek(new_kek, current=current))
    except CryptoError as exc:
        print(f"rotate: {exc}", file=sys.stderr)
        return 1
    print(f"rotate: re-wrapped {count} space key(s) to KEK version {new_kek.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
