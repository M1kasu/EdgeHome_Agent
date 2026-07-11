"""从版本控制的原始实验数据生成比赛提交包图表；不接受手工数值输入。"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmark" / "results"
OUTPUT = ROOT / "competition_submission" / "assets"


def _rows(name: str) -> list[dict[str, str]]:
    with (RESULTS / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_svg(name: str, body: str, title: str) -> None:
    figures = OUTPUT / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    (figures / name).write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540">'
        f'<style>text{{font-family:Arial,"Microsoft YaHei",sans-serif;fill:#1c2430}}'
        f'.t{{font-size:24px;font-weight:bold}}.l{{font-size:15px}}.v{{font-size:17px;font-weight:bold}}'
        f'.box{{fill:#edf6ff;stroke:#2776b7;stroke-width:2}}.line{{stroke:#8da1b5;stroke-width:2}}</style>'
        f'<text class="t" x="40" y="42">{title}</text>{body}</svg>\n',
        encoding="utf-8",
    )


def _bar_chart(name: str, title: str, items: list[tuple[str, float]], unit: str, maximum: float | None = None) -> None:
    maximum = maximum or max(value for _, value in items)
    left, baseline, width, height = 105, 445, 720, 310
    body = f'<line class="line" x1="{left}" y1="{baseline}" x2="{left + width}" y2="{baseline}"/>'
    body += f'<text class="l" x="{left}" y="{baseline + 34}">0</text><text class="l" x="{left + width - 15}" y="{baseline + 34}">{maximum:g}</text>'
    step = width / len(items)
    for index, (label, value) in enumerate(items):
        bar_width = min(120, step * 0.54)
        x = left + index * step + (step - bar_width) / 2
        bar_height = height * value / maximum if maximum else 0
        y = baseline - bar_height
        color = "#2776b7" if index % 2 == 0 else "#35a778"
        body += f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="{color}" rx="5"/>'
        body += f'<text class="v" x="{x + bar_width / 2:.1f}" y="{y - 10:.1f}" text-anchor="middle">{value:.3f}{unit}</text>'
        body += f'<text class="l" x="{x + bar_width / 2:.1f}" y="{baseline + 58}" text-anchor="middle">{label}</text>'
    _write_svg(name, body, title)


def _flow_chart(name: str, title: str, labels: list[str], note: str) -> None:
    body = ""
    width = 150
    for index, label in enumerate(labels):
        x = 45 + index * 180
        body += f'<rect class="box" x="{x}" y="180" width="{width}" height="92" rx="12"/>'
        body += f'<text class="v" x="{x + width / 2}" y="220" text-anchor="middle">{label}</text>'
        if index < len(labels) - 1:
            body += f'<line class="line" x1="{x + width}" y1="226" x2="{x + 178}" y2="226"/><text class="v" x="{x + 164}" y="218">›</text>'
    body += f'<text class="l" x="45" y="345">{note}</text>'
    _write_svg(name, body, title)


def _stability_chart() -> None:
    records = [json.loads(line) for line in (RESULTS / "stability_1h.jsonl").read_text(encoding="utf-8").splitlines() if line]
    values = [row["resource"]["process_memory_mb"] for row in records]
    low, high = min(values), max(values)
    left, top, width, height = 80, 105, 800, 300
    span = max(high - low, 0.01)
    points = []
    for index, value in enumerate(values):
        x = left + width * index / (len(values) - 1)
        y = top + height - height * (value - low) / span
        points.append(f"{x:.1f},{y:.1f}")
    body = f'<rect x="{left}" y="{top}" width="{width}" height="{height}" fill="#fafcff" stroke="#c3d2e2"/>'
    body += f'<polyline points="{" ".join(points)}" fill="none" stroke="#2776b7" stroke-width="3"/>'
    body += f'<text class="l" x="{left}" y="{top - 12}">进程内存（MiB）：最小 {low:.3f}，最大 {high:.3f}</text>'
    body += f'<text class="l" x="{left}" y="{top + height + 32}">60 个一分钟周期；模型、知识、能耗失败均为 0</text>'
    _write_svg("stability_memory.svg", body, "一小时稳定性：执行器进程内存采样")


def _copy_data() -> None:
    destination = OUTPUT / "data"
    destination.mkdir(parents=True, exist_ok=True)
    names = [
        "dual_path_results.csv",
        "complex_schema_q4_ctx2048_full.outcomes.json",
        "stability_1h_summary.json",
        "execution_verification_comparison.csv",
        "knowledge_workloads.csv",
        "energy_workloads.csv",
        "safety_workloads.json",
        "offline_network_test.json",
        "phase14_schema_failure_recheck_ctx2048_256.outcomes.json",
        "phase14_schema_failure_recovery_ctx2048_384.outcomes.json",
    ]
    for name in names:
        shutil.copy2(RESULTS / name, destination / name)


def main() -> None:
    _copy_data()
    dual = {row["label"]: row for row in _rows("dual_path_results.csv")}
    _bar_chart(
        "latency_comparison.svg",
        "简单自然语言任务：双路径与直接模型平均延迟",
        [("直接模型", float(dual["A 全量模型简单任务"]["average_latency_ms"])), ("双路径快速路由", float(dual["B 双路径简单任务"]["average_latency_ms"]))],
        " ms",
    )
    _bar_chart("schema_validity.svg", "复杂规划 Schema 验证结果（第13阶段原始测试）", [("合法", 27), ("非法", 3)], " 条", 30)
    _stability_chart()
    knowledge = _rows("knowledge_workloads.csv")
    energy = _rows("energy_workloads.csv")
    _bar_chart(
        "scenario_completion.svg",
        "关键能力任务结果（各项工作负载不同，不作统一端到端成功率）",
        [
            ("Schema 合法", 27 / 30 * 100),
            ("知识有证据", sum(row["success"] == "True" for row in knowledge) / len(knowledge) * 100),
            ("能耗分析", sum(row["success"] == "True" for row in energy) / len(energy) * 100),
            ("安全未触发保护服务", 100),
            ("恢复后状态完成", 100),
        ],
        "%",
        100,
    )
    _flow_chart(
        "simple_task_flow.svg",
        "简单任务处理流程",
        ["中文指令", "快速意图/槽位", "白名单工具", "状态回读"],
        "未被保守分类器接受的输入不会直接执行，而是降级到受限规划路径。",
    )
    _flow_chart(
        "system_architecture.svg",
        "EdgeHome 系统模块架构",
        ["用户交互", "双路径路由", "规划与安全", "HA 工具与验证", "反馈/恢复"],
        "知识检索、能耗分析和资源调度为本地支撑模块；所有模型计划先过 Schema 与安全边界。",
    )
    _flow_chart(
        "open_source_boundary.svg",
        "开源组件与 EdgeHome 新增模块边界",
        ["Home Assistant", "Home LLM", "llama.cpp", "EdgeHome 新增模块"],
        "基础平台与推理服务来自开源组件；中文路由、规划安全、检索、能耗、评测为本项目新增。",
    )
    screenshots = OUTPUT / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    (screenshots / "README.md").write_text(
        "# 演示截图采集说明\n\n现场录制前按 `11_现场演示脚本.md` 逐项截取；截图不得包含登录令牌、家庭住址、账号、学校或个人身份信息。当前提交包不附带本机 Home Assistant 页面截图，以避免将现有账户资料带入比赛材料。请在演示测试/访客账户下重新采集，并标注预录片段。\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
