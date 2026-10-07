"""Targets the runner can evaluate: the deployed app (API mode) or a model endpoint."""

from typing import Protocol

from evals.runner.schema import Case
from evals.runner.transcript import Transcript


class FeatureUnavailable(Exception):  # noqa: N818 (a skip signal, not an error)
    """The app does not (yet) implement an endpoint this case needs (404/501)."""


class Target(Protocol):
    mode: str

    def describe(self) -> dict[str, str]: ...

    def run_case(self, case: Case) -> Transcript: ...

    def close(self) -> None: ...
