"""将本地演示截图和已审计数据生成无旁白 GIF 演示草稿。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "competition_submission" / "assets"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    family = "msyhbd.ttc" if bold else "msyh.ttc"
    return ImageFont.truetype(Path("C:/Windows/Fonts") / family, size)


def _canvas(title: str, lines: list[str]) -> Image.Image:
    image = Image.new("RGB", (1280, 720), "#f7fbff")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1280, 95), fill="#0c4a6e")
    draw.text((60, 27), title, font=_font(34, bold=True), fill="white")
    y = 160
    for line in lines:
        draw.text((80, y), line, font=_font(30), fill="#172554")
        y += 72
    draw.text((80, 650), "EdgeHome Agent｜本地演示证据草稿｜无旁白", font=_font(20), fill="#475569")
    return image


def _screenshot_frame(name: str, title: str) -> Image.Image:
    screenshot = Image.open(ASSETS / "screenshots" / name).convert("RGB")
    screenshot.thumbnail((1120, 590))
    frame = _canvas(title, [])
    left = (1280 - screenshot.width) // 2
    top = 110 + (570 - screenshot.height) // 2
    frame.paste(screenshot, (left, top))
    return frame


def _metrics() -> list[str]:
    with (ASSETS / "data" / "dual_path_results.csv").open(encoding="utf-8", newline="") as handle:
        rows = {row["label"]: row for row in csv.DictReader(handle)}
    direct = float(rows["A 全量模型简单任务"]["average_latency_ms"])
    fast = float(rows["B 双路径简单任务"]["average_latency_ms"])
    outcomes = json.loads((ASSETS / "data" / "complex_schema_q4_ctx2048_full.outcomes.json").read_text(encoding="utf-8"))
    schema_valid = sum(item["schema_valid"] is True for item in outcomes)
    stability = json.loads((ASSETS / "data" / "stability_1h_summary.json").read_text(encoding="utf-8"))
    return [
        f"简单任务快速路径：{fast:.3f} ms",
        f"同任务直接模型：{direct:.3f} ms",
        f"复杂规划 Schema：{schema_valid} / {len(outcomes)} 合法",
        "一小时稳定性："
        f"{stability['samples']} 次采样，模型/检索/能耗失败 "
        f"{stability['model_unhealthy_count']}/{stability['knowledge_failure_count']}/{stability['energy_failure_count']}",
        "范围：Q4_K_M、ctx2048、CPU；不等同设备执行成功率",
    ]


def main() -> None:
    videos = ASSETS / "videos"
    videos.mkdir(parents=True, exist_ok=True)
    frames = [
        _canvas("智家端脑 EdgeHome Agent", ["面向全屋智能场景的端侧自主服务智能体", "真实页面截图 + 可复核实验数据", "本草稿无旁白；正式视频请按 07_演示视频脚本录制"]),
        _screenshot_frame("ha_entity_state.jpg", "真实 Home Assistant：虚拟实体状态"),
        _screenshot_frame("ha_assist_parser.jpg", "真实 Home Assistant：中文测试指令输入"),
        _canvas("关键实测结果", _metrics()),
    ]
    frames[0].save(videos / "edgehome_demo_evidence.gif", save_all=True, append_images=frames[1:], duration=[2200, 4200, 4200, 5200], loop=0)
    (videos / "README.md").write_text(
        "# 演示视频资产\n\n`edgehome_demo_evidence.gif` 由本机实际页面截图与 `assets/data/dual_path_results.csv` 生成，无旁白、总时长约 15.8 秒。它是演示证据草稿，不替代 `07_演示视频脚本.md` 中的 5 分钟正式录制。正式 MP4 需在测试账户下按脚本录制并加入旁白。\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
