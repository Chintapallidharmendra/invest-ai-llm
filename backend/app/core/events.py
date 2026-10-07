"""In-process async hook registry.

Modules subscribe to named events (e.g. ``user.deleted``) without a shared listener
list. Subscribers run in subscription order; an exception propagates to the emitter so
callers fail closed.
"""

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

Subscriber = Callable[..., Awaitable[None]]

_subscribers: defaultdict[str, list[Subscriber]] = defaultdict(list)


def subscribe(name: str, fn: Subscriber) -> Callable[[], None]:
    """Register ``fn`` for event ``name``. Returns a function that unsubscribes it."""
    _subscribers[name].append(fn)

    def unsubscribe() -> None:
        if fn in _subscribers[name]:
            _subscribers[name].remove(fn)

    return unsubscribe


async def emit(name: str, **payload: Any) -> None:
    for fn in list(_subscribers.get(name, ())):
        await fn(**payload)
