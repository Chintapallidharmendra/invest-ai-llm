import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_package
from app.api.app import create_app
from app.api.router import discover_routers

BACKEND_DIR = Path(__file__).resolve().parents[2]

ROUTES_SOURCE = """
from fastapi import APIRouter

router = APIRouter(prefix="/testmod", tags=["testmod"])


@router.get("/ping")
async def ping() -> dict[str, str]:
    return {"pong": "yes"}
"""


@pytest.fixture
def testmod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A temporary ``app._testmod`` package with a ``routes.py``."""
    package = tmp_path / "_testmod"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "routes.py").write_text(ROUTES_SOURCE)
    (tmp_path / "_norouter").mkdir()
    (tmp_path / "_norouter" / "__init__.py").write_text("")
    (tmp_path / "_norouter" / "routes.py").write_text("router = 'not a router'\n")
    monkeypatch.setattr(app_package, "__path__", [*app_package.__path__, str(tmp_path)])
    for name in ("app._testmod", "app._testmod.routes", "app._norouter", "app._norouter.routes"):
        monkeypatch.delitem(sys.modules, name, raising=False)


def test_routes_module_is_auto_mounted(testmod: None) -> None:
    names = [name for name, _ in discover_routers()]
    assert "app._testmod.routes" in names
    assert "app._norouter.routes" not in names

    with TestClient(create_app(configure_logs=False)) as client:
        response = client.get("/api/v1/testmod/ping")
    assert response.status_code == 200
    assert response.json() == {"pong": "yes"}


def test_export_openapi_writes_json() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app.api.export_openapi"],
        cwd=BACKEND_DIR,
        check=True,
        capture_output=True,
        text=True,
    )
    document = json.loads(result.stdout)
    assert document["openapi"].startswith("3.")
    assert "/healthz" in document["paths"]
    assert "/readyz" in document["paths"]
    assert "/metrics" not in document["paths"]
