#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Использование: ./scripts/deploy.sh user@server [remote_directory]" >&2
  exit 2
fi
target="$1"
remote_dir="${2:-~/bilimclass-neo-bot}"

rsync -az --delete \
  --exclude '/.git/' --exclude '/.venv/' --exclude '/.run/' \
  --exclude '/.env' --exclude '/data/' --exclude '/__pycache__/' \
  "$ROOT_DIR/" "$target:$remote_dir/"
echo "Код перенесён в $target:$remote_dir"
echo "На сервере: создайте .env из .env.example, затем запустите scripts/install-service.sh"
echo "Секреты и база данных намеренно не копируются. Для миграции данных см. README."
