"""Install the tracked EdgeHome demo YAML into a local Home Assistant config directory."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_SOURCE = _ROOT / "deployment" / "demo" / "homeassistant"
_FRONTEND_SOURCE = _SOURCE / "www"
_CONFIG_INCLUDES = {
    "input_boolean": "edgehome_demo_input_boolean.yaml",
    "input_number": "edgehome_demo_input_number.yaml",
    "template": "edgehome_demo_templates.yaml",
    "climate": "edgehome_demo_climate.yaml",
    "rest_command": "edgehome_demo_rest_commands.yaml",
    "panel_custom": "edgehome_demo_panel_custom.yaml",
}
_SCRIPT_START = "# >>> EdgeHome demo script (managed) >>>"
_SCRIPT_END = "# <<< EdgeHome demo script (managed) <<<"


def install(config_dir: Path) -> None:
    """Copy demo fragments and frontend assets without touching `.storage`."""
    config_dir.mkdir(parents=True, exist_ok=True)
    for source in _SOURCE.glob("edgehome_demo_*.yaml"):
        if source.name == "edgehome_demo_scripts.yaml":
            continue
        shutil.copy2(source, config_dir / source.name)
    _install_frontend_assets(config_dir)

    configuration = config_dir / "configuration.yaml"
    if not configuration.exists():
        configuration.write_text("default_config:\n", encoding="utf-8")
    _ensure_includes(configuration)
    _install_script(config_dir / "scripts.yaml", _SOURCE / "edgehome_demo_scripts.yaml")


def _install_frontend_assets(config_dir: Path) -> None:
    """Install the self-contained workbench card under Home Assistant's local URL root."""
    if not _FRONTEND_SOURCE.exists():
        return
    destination = config_dir / "www"
    destination.mkdir(parents=True, exist_ok=True)
    for source in _FRONTEND_SOURCE.glob("edgehome_*.js"):
        # 只同步项目自带的工作台资源，不清理用户 www 目录中的其他页面或图片。
        shutil.copy2(source, destination / source.name)


def _ensure_includes(path: Path) -> None:
    content = _remove_legacy_includes(path.read_text(encoding="utf-8"))
    additions = []
    for key, filename in _CONFIG_INCLUDES.items():
        if f"!include {filename}" in content:
            continue
        # 保留用户已有的顶层集成配置，避免安装演示工作台时覆盖手工 Lovelace 或 REST 命令。
        # 仅在干净配置中写入受版本控制的 EdgeHome include。
        if re.search(rf"(?m)^{re.escape(key)}\s*:", content):
            continue
        additions.append(f"{key}: !include {filename}")
    if additions:
        suffix = "" if content.endswith("\n") else "\n"
        path.write_text(f"{content}{suffix}\n# EdgeHome 虚拟家居测试环境\n{'\n'.join(additions)}\n", encoding="utf-8")
    elif content != path.read_text(encoding="utf-8"):
        path.write_text(content, encoding="utf-8")


def _remove_legacy_includes(content: str) -> str:
    """Remove superseded managed snippets while preserving user-owned configuration."""
    content = re.sub(r"(?m)^mqtt:\s*!include\s+edgehome_demo_mqtt\.yaml\s*\r?\n?", "", content)
    # 工作台已迁移到官方 panel_custom；移除旧 YAML 仪表盘，避免重启后出现未注册自定义卡片。
    return re.sub(r"(?m)^lovelace:\s*!include\s+edgehome_demo_lovelace\.yaml\s*\r?\n?", "", content)


def _install_script(path: Path, source: Path) -> None:
    block = f"{_SCRIPT_START}\n{source.read_text(encoding='utf-8').strip()}\n{_SCRIPT_END}\n"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    if content.strip() in {"", "{}"}:
        path.write_text(block, encoding="utf-8")
        return
    if _SCRIPT_START in content and _SCRIPT_END in content:
        before, remainder = content.split(_SCRIPT_START, maxsplit=1)
        _, after = remainder.split(_SCRIPT_END, maxsplit=1)
        path.write_text(f"{before}{block}{after.lstrip()}", encoding="utf-8")
        return
    suffix = "" if content.endswith("\n") else "\n"
    path.write_text(f"{content}{suffix}\n{block}", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=_ROOT / "deployment" / "homeassistant",
        help="Home Assistant configuration directory (default: deployment/homeassistant)",
    )
    args = parser.parse_args()
    install(args.config_dir)
    print(f"EdgeHome demo YAML installed into {args.config_dir}")


if __name__ == "__main__":
    main()
