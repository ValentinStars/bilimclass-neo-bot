#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

python_bin="${PYTHON:-python3}"
if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  "$python_bin" -m venv "$ROOT_DIR/.venv"
fi
"$ROOT_DIR/.venv/bin/python" -m pip install -e "$ROOT_DIR"
echo "Готово: зависимости установлены в $ROOT_DIR/.venv"
