#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
cd "$ROOT"
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
"$PYTHON" -m pytest -p pytest_asyncio.plugin -q \
  tests/test_fast_path.py tests/test_movie_scene.py tests/test_leave_home_scene.py \
  tests/test_multi_turn_sleep.py tests/test_failure_recovery.py \
  tests/test_knowledge_retriever.py tests/test_energy_analyzer.py \
  tests/test_protected_entities.py tests/test_prompt_injection_safety.py tests/test_invalid_json_recovery.py
