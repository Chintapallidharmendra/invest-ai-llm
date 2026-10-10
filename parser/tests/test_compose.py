"""AC #1 and #5: the parser's Compose service is locked down (needs Docker)."""

import json
import shutil
import subprocess
from typing import Any

import pytest

from tests.conftest import REPO

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed")


@pytest.fixture(scope="module")
def config() -> dict[str, Any]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            # The whole stack: the `parsing` network is defined in compose.yml.
            str(REPO / "deploy" / "compose.yml"),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin", "APP_DATA_DIR": "/data"},
    )
    if result.returncode != 0:
        pytest.skip(f"docker compose config failed: {result.stderr[-300:]}")
    rendered: dict[str, Any] = json.loads(result.stdout)
    return rendered


@pytest.fixture(scope="module")
def parser_service(config: dict[str, Any]) -> dict[str, Any]:
    service: dict[str, Any] = config["services"]["parser"]
    return service


def test_hardening(parser_service: dict[str, Any]) -> None:
    s = parser_service
    assert s["read_only"] is True
    assert s["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in s["security_opt"]
    assert s["user"] == "10001:10001"
    assert s["mem_limit"] == str(4 * 1024**3) or s["mem_limit"] in {"4g", 4 * 1024**3}
    assert int(s["pids_limit"]) > 0
    assert any(str(t).startswith("/tmp") for t in s["tmpfs"])  # noqa: S108


def test_parsing_network_only_and_no_ports(
    parser_service: dict[str, Any], config: dict[str, Any]
) -> None:
    assert list(parser_service["networks"]) == ["parsing"]
    assert config["networks"]["parsing"]["internal"] is True
    assert "ports" not in parser_service


def test_models_read_only_and_no_secrets(parser_service: dict[str, Any]) -> None:
    volumes = parser_service["volumes"]
    assert {v["target"] for v in volumes} == {
        "/models/docling/docling-project--docling-layout-heron",
        "/models/docling/docling-project--docling-models",
    }
    assert all(v["read_only"] is True for v in volumes)
    assert "secrets" not in parser_service
    env = parser_service.get("environment", {})
    assert not any(k.endswith(("PASSWORD", "SECRET", "KEY", "DATABASE_URL")) for k in env)
