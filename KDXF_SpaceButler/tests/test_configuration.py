from __future__ import annotations

import os

from spacebutler import configuration
from spacebutler.voice import VoiceConfig


def test_dotenv_is_independent_of_cwd_and_does_not_interpolate_secrets(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("SPACEBUTLER_ASR_API_KEY='literal-${UNDEFINED_NAME}-$value'\n", encoding="utf-8-sig")
    monkeypatch.setattr(configuration, "ENV_FILE", env_path)
    monkeypatch.delenv("SPACEBUTLER_ASR_API_KEY", raising=False)
    elsewhere = tmp_path / "different-working-directory"
    elsewhere.mkdir()
    (elsewhere / ".env").write_text("SPACEBUTLER_ASR_API_KEY=wrong-app-key\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    assert VoiceConfig.from_env().api_key == "literal-${UNDEFINED_NAME}-$value"
    assert "SPACEBUTLER_ASR_API_KEY" not in os.environ


def test_empty_process_value_can_disable_a_file_credential(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("SPACEBUTLER_ASR_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setenv("SPACEBUTLER_ASR_API_KEY", "")
    assert VoiceConfig.from_env(env_path).api_key == ""


def test_launcher_loads_only_project_environment_and_preserves_process_values(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ESPHOME_HOST=192.0.2.10\nSPACEBUTLER_LLM_TIMEOUT=240\nHA_TOKEN=test-token\n"
        "UNRELATED_SETTING=should-not-be-loaded\n", encoding="utf-8",
    )
    monkeypatch.setenv("ESPHOME_HOST", "192.0.2.20")
    for name in ("SPACEBUTLER_LLM_TIMEOUT", "HA_TOKEN", "UNRELATED_SETTING"):
        monkeypatch.delenv(name, raising=False)
    configuration.load_project_environment(env_path)
    assert os.environ["ESPHOME_HOST"] == "192.0.2.20"
    assert os.environ["SPACEBUTLER_LLM_TIMEOUT"] == "240"
    assert os.environ["HA_TOKEN"] == "test-token"
    assert "UNRELATED_SETTING" not in os.environ


def test_missing_env_file_still_accepts_process_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACEBUTLER_ASR_API_KEY", "environment-key")
    assert VoiceConfig.from_env(tmp_path / ".env").api_key == "environment-key"
