"""Spaces: private spaces and deal workspaces, and the RLS foundation (ADR-023).

Importing the package subscribes :mod:`app.spaces.hooks` (a private space for every new
user), so any process that emits ``auth.user_created`` must import ``app.spaces``; the
API does through router discovery.
"""

from app.spaces import hooks as _hooks  # noqa: F401 (subscribes on import)
