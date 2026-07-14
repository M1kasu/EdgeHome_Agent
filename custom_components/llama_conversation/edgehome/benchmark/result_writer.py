"""原始结果写入 JSONL/CSV，并保存无密钥的配置快照。"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .schemas import BenchmarkMetric


class BenchmarkResultWriter:
    """写入 append-only JSONL 及方便图表使用的 CSV。"""

    def __init__(self, result_directory: Path) -> None:
        self._directory = result_directory
        self._directory.mkdir(parents=True, exist_ok=True)

    def write(self, name: str, metrics: Iterable[BenchmarkMetric], config_snapshot: dict[str, Any]) -> tuple[Path, Path, Path]:
        """将同一轮真实记录一次性写入，且拒绝配置中的疑似密钥字段。"""
        rows = [metric.model_dump(mode="json") for metric in metrics]
        jsonl = self._directory / f"{name}.jsonl"
        csv_path = self._directory / f"{name}.csv"
        config = self._directory / f"{name}.config.json"
        with jsonl.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        fields = list(BenchmarkMetric.model_fields)
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        config.write_text(json.dumps(_redacted(config_snapshot), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return jsonl, csv_path, config


def _redacted(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if any(part in key.lower() for part in ("token", "secret", "password", "api_key")) else _redacted(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted(item) for item in value]
    return value
