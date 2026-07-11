"""Install the tracked EdgeHome demo YAML into a local Home Assistant config directory."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_SOURCE = _ROOT / "deployment" / "demo" / "homeassistant"
_CONFIG_INCLUDES = {
    "input_boolean": "edgehome_demo_input_boolean.yaml",
    "input_number": "edgehome_demo_input_number.yaml",
    "template": "edgehome_demo_templates.yaml",
    "climate": "edgehome_demo_climate.yaml",
}
_SCRIPT_START = "# >>> EdgeHome demo script (managed) >>>"
_SCRIPT_END = "# <<< EdgeHome demo script (managed) <<<"


def install(config_dir: Path) -> None:
    """Copy demo fragments and add idempotent includes without touching `.storage`."""
    config_dir.mkdir(parents=True, exist_ok=True)
    for source in _SOURCE.glob("edgehome_demo_*.yaml"):
        if source.name == "edgehome_demo_scripts.yaml":
            continue
        shutil.copy2(source, config_dir / source.name)

    configuration = config_dir / "configuration.yaml"
    if not configuration.exists():
        configuration.write_text("default_config:\n", encoding="utf-8")
    _ensure_includes(configuration)
    _install_script(config_dir / "scripts.yaml", _SOURCE / "edgehome_demo_scripts.yaml")


def _ensure_includes(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    additions = [
        f"{key}: !include {filename}"
        for key, filename in _CONFIG_INCLUDES.items()
        if f"!include {filename}" not in content
    ]
    if additions:
        suffix = "" if content.endswith("\n") else "\n"
        path.write_text(f"{content}{suffix}\n# EdgeHome 虚拟家居测试环境\n{'\n'.join(additions)}\n", encoding="utf-8")


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
