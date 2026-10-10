"""Story 2.2: discovery also imports ``app/<module>/audit_events_*.py``."""

import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

import app
from app.audit import registry
from app.audit.check_models import main as check_models_main


@pytest.fixture
def extra_events_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A throwaway ``app.zz_story_test`` package with two event files."""
    package = tmp_path / "zz_story_test"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "audit_events.py").write_text(
        textwrap.dedent(
            """
            from typing import ClassVar
            from app.audit.types import AuditEvent

            class Base(AuditEvent):
                event_type: ClassVar[str] = "zz_story_test.base"
                n: int
            """
        )
    )
    (package / "audit_events_links.py").write_text(
        textwrap.dedent(
            """
            from typing import ClassVar
            from app.audit.types import AuditEvent

            class Linked(AuditEvent):
                event_type: ClassVar[str] = "zz_story_test.linked"
                note: str  # free text: the CI check must catch it here too
            """
        )
    )
    (package / "not_events.py").write_text("raise RuntimeError('must not be imported')\n")
    monkeypatch.setattr(app, "__path__", [*app.__path__, str(tmp_path)])
    yield "app.zz_story_test"
    for name in ("zz_story_test.base", "zz_story_test.linked"):
        registry.unregister(name)
    for module in [m for m in sys.modules if m.startswith("app.zz_story_test")]:
        del sys.modules[module]


def test_discover_imports_topic_event_files(extra_events_module: str) -> None:
    imported = registry.discover()
    assert f"{extra_events_module}.audit_events" in imported
    assert f"{extra_events_module}.audit_events_links" in imported
    assert f"{extra_events_module}.not_events" not in imported
    assert {"zz_story_test.base", "zz_story_test.linked"} <= set(registry.registered())


def test_discover_finds_the_real_modules() -> None:
    imported = registry.discover()
    expected = sorted(
        f"app.{path.parent.name}.{path.stem}"
        for path in Path(app.__file__).parent.glob("*/audit_events*.py")
    )
    assert sorted(imported) == expected
    assert "app.auth.audit_events" in imported


def test_check_models_sees_topic_event_files(
    extra_events_module: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check_models_main() == 1
    out = capsys.readouterr().out
    assert "zz_story_test.linked" in out
    assert "zz_story_test.base" not in out
