"""Tests for installing the tracked EdgeHome demo presentation assets."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_INSTALLER_PATH = _ROOT / "scripts" / "install_edgehome_demo.py"
_SPEC = importlib.util.spec_from_file_location("edgehome_demo_installer", _INSTALLER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_installer_adds_workbench_and_fault_control_assets(tmp_path: Path) -> None:
    """A clean Home Assistant config receives the tracked dashboard assets once."""
    _MODULE.install(tmp_path)
    _MODULE.install(tmp_path)

    configuration = (tmp_path / "configuration.yaml").read_text(encoding="utf-8")
    assert configuration.count("lovelace: !include edgehome_demo_lovelace.yaml") == 1
    assert configuration.count("rest_command: !include edgehome_demo_rest_commands.yaml") == 1
    assert "edgehome-workbench:" in (tmp_path / "edgehome_demo_lovelace.yaml").read_text(encoding="utf-8")
    assert "sensor.edgehome_last_execution" in (tmp_path / "edgehome_demo_execution_dashboard.yaml").read_text(encoding="utf-8")
    assert "edgehome_set_curtain_fault:" in (tmp_path / "edgehome_demo_rest_commands.yaml").read_text(encoding="utf-8")


def test_installer_preserves_existing_lovelace_configuration(tmp_path: Path) -> None:
    """An existing user Lovelace section is never replaced by the demo installer."""
    configuration = tmp_path / "configuration.yaml"
    configuration.write_text("default_config:\n\nlovelace:\n  mode: storage\n", encoding="utf-8")

    _MODULE.install(tmp_path)

    content = configuration.read_text(encoding="utf-8")
    assert content.count("lovelace:") == 1
    assert "mode: storage" in content
    assert "rest_command: !include edgehome_demo_rest_commands.yaml" in content
