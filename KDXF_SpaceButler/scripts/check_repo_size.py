"""Fail when ordinary Git tracks files too large for GitHub.

The check examines index entries instead of every file on disk, so ignored
runtime artifacts such as local GGUF models do not produce false positives.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

WARNING_BYTES = 50 * 1024 * 1024
ERROR_BYTES = 100 * 1024 * 1024
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    )
    return [
        REPOSITORY_ROOT / raw.decode("utf-8", errors="surrogateescape")
        for raw in completed.stdout.split(b"\0")
        if raw
    ]


def main() -> int:
    warnings: list[tuple[Path, int]] = []
    failures: list[tuple[Path, int]] = []
    for path in tracked_files():
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > ERROR_BYTES:
            failures.append((path, size))
        elif size > WARNING_BYTES:
            warnings.append((path, size))

    for path, size in warnings:
        print(f"WARNING: {path.relative_to(REPOSITORY_ROOT)} is {size / 1024 / 1024:.1f} MiB")
    for path, size in failures:
        print(f"ERROR: {path.relative_to(REPOSITORY_ROOT)} is {size / 1024 / 1024:.1f} MiB")

    if failures:
        print("Repository size check failed: tracked files must be <= 100 MiB.")
        return 1
    print("Repository size check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
