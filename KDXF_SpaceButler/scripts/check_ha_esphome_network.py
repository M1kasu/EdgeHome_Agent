"""Verify TCP reachability from inside Home Assistant to every ESPHome node."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spacebutler.esphome_registry import esphome_nodes  # noqa: E402
from spacebutler.configuration import load_project_environment  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="spacebutler-home-assistant")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()
    load_project_environment()
    if shutil.which("docker") is None:
        print(
            json.dumps(
                {
                    "container": args.container,
                    "nodes": [],
                    "error": "docker CLI is not available on this host; run this script on the HA Docker host",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    results: list[dict[str, object]] = []
    for role, host, port in esphome_nodes():
        probe = (
            "import socket; "
            f"s=socket.create_connection(({host!r},{port}),{args.timeout!r}); "
            "s.close()"
        )
        completed = subprocess.run(
            ["docker", "exec", args.container, "python", "-c", probe],
            check=False,
            capture_output=True,
            text=True,
        )
        results.append(
            {
                "role": role,
                "host": host,
                "port": port,
                "reachable_from_ha": completed.returncode == 0,
                "error": completed.stderr.strip() if completed.returncode else None,
            }
        )
    print(json.dumps({"container": args.container, "nodes": results}, ensure_ascii=False, indent=2))
    return 0 if all(bool(item["reachable_from_ha"]) for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
