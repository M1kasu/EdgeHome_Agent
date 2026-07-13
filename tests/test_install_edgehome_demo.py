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


def test_installer_adds_workbench_assets(tmp_path: Path) -> None:
    """A clean Home Assistant config receives the tracked product workbench once."""
    _MODULE.install(tmp_path)
    _MODULE.install(tmp_path)

    configuration = (tmp_path / "configuration.yaml").read_text(encoding="utf-8")
    assert configuration.count("panel_custom: !include edgehome_demo_panel_custom.yaml") == 1
    assert configuration.count("rest_command: !include edgehome_demo_rest_commands.yaml") == 1
    panel = (tmp_path / "edgehome_demo_panel_custom.yaml").read_text(encoding="utf-8")
    assert "edgehome-execution-workbench" in panel
    assert "sidebar_title: EdgeHome Agent" in panel
    assert "sidebar_icon: mdi:shield-home-outline" in panel
    assert "module_url: /local/edgehome_execution_workbench.js" in panel
    assert "edgehome_set_curtain_fault:" in (tmp_path / "edgehome_demo_rest_commands.yaml").read_text(encoding="utf-8")
    assert 'mode: "none"' in (tmp_path / "edgehome_demo_templates.yaml").read_text(encoding="utf-8")
    workbench = (tmp_path / "www" / "edgehome_execution_workbench.js")
    assert workbench.is_file()
    frontend = workbench.read_text(encoding="utf-8")
    assert "conversation/process" in frontend
    assert "快捷场景" in frontend
    assert "工具与 MQTT 回读" in frontend


def test_installer_preserves_existing_lovelace_configuration(tmp_path: Path) -> None:
    """An existing user Lovelace section is never replaced by the demo installer."""
    configuration = tmp_path / "configuration.yaml"
    configuration.write_text("default_config:\n\nlovelace:\n  mode: storage\n", encoding="utf-8")

    _MODULE.install(tmp_path)

    content = configuration.read_text(encoding="utf-8")
    assert content.count("lovelace:") == 1
    assert "mode: storage" in content
    assert "rest_command: !include edgehome_demo_rest_commands.yaml" in content
