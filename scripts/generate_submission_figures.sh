#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
cd "$ROOT"
"$PYTHON" scripts/generate_submission_figures.py
printf '%s\n' '比赛图表和最小复核数据已更新至 competition_submission/assets。'
