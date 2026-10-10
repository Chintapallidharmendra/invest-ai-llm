"""Create the first admin from the command line (Story 2.3)::

    python -m app.admin.bootstrap --username U --email E

Works only while no admin exists (of any status). Prints the one-time set-password link
to stdout and nothing else; nothing is logged. Concurrent runs are serialised by a
transaction-scoped advisory lock, so at most one creates an admin.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from typing import Final

from pydantic import ValidationError
from sqlalchemy import exists, select, text

from app.admin.schemas import CreateUserRequest
from app.admin.service_users import create_user_in
from app.auth.models import Role, User
from app.core.db import dispose_engine, transaction
from app.core.errors import ProblemException

# Arbitrary, fixed: the advisory lock taken while checking for and creating the admin.
_LOCK_KEY: Final = 2_003_001
EXIT_ADMIN_EXISTS: Final = 3


class AdminExistsError(Exception):
    pass


async def bootstrap(username: str, email: str) -> str:
    """Create the first admin and return their link; :class:`AdminExistsError` otherwise."""
    async with transaction() as tx:
        await tx.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
        if (await tx.execute(select(exists().where(User.role == Role.ADMIN)))).scalar():
            raise AdminExistsError
        _, link = await create_user_in(
            tx, None, username=username, email=email, role=Role.ADMIN, bootstrap=True
        )
    return link.url


async def run(username: str, email: str) -> int:
    try:
        url = await bootstrap(username, email)
    except AdminExistsError:
        print("An admin already exists; nothing was created.", file=sys.stderr)
        return EXIT_ADMIN_EXISTS
    except ProblemException as exc:
        print(f"Not created: {exc.code}", file=sys.stderr)
        return 1
    finally:
        await dispose_engine()
    print(url)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the first admin user.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args(argv)
    try:
        request = CreateUserRequest(username=args.username, email=args.email, role=Role.ADMIN)
    except ValidationError as exc:
        fields = sorted({str(err["loc"][0]) for err in exc.errors() if err["loc"]})
        print(f"Invalid {', '.join(fields)}", file=sys.stderr)
        return 2
    return asyncio.run(run(request.username, request.email))


if __name__ == "__main__":
    sys.exit(main())
