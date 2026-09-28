#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/scripts/common.sh"
require_env

if service_installed; then
  systemctl --user start "$SERVICE_NAME"
  systemctl --user --no-pager status "$SERVICE_NAME"
  exit
fi

if is_running_pid; then
  echo "НЭО уже работает (PID $(cat "$PID_FILE"))."
  exit
fi

if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  "$ROOT_DIR/scripts/setup.sh"
fi

mkdir -p "$RUN_DIR"
chmod 700 "$RUN_DIR"
umask 077
cd "$ROOT_DIR"
setsid "$ROOT_DIR/.venv/bin/python" -m bilim_neo >> "$LOG_FILE" 2>&1 < /dev/null &
echo "$!" > "$PID_FILE"
sleep 2
if is_running_pid; then
  echo "НЭО запущен (PID $(cat "$PID_FILE")). Лог: $LOG_FILE"
else
  echo "НЭО не запустился. Последние строки лога:" >&2
  tail -n 30 "$LOG_FILE" >&2
  exit 1
fi
