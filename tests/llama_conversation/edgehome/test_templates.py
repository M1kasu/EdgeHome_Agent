"""Tests for scene-template recognition without implicit actions."""

from __future__ import annotations

from custom_components.llama_conversation.edgehome.planner.templates import SceneName, SceneTemplateLibrary


def test_scene_library_matches_explicit_scene_keyword() -> None:
    template = SceneTemplateLibrary().match("我要开始观影模式")

    assert template is not None
    assert template.name == SceneName.MOVIE
    assert "明确列出的设备" in template.planning_guidance


def test_scene_library_does_not_guess_scene_for_plain_device_command() -> None:
    assert SceneTemplateLibrary().match("打开客厅灯") is None
