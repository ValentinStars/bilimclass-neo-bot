#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/scripts/common.sh"

if service_installed; then
  systemctl --user --no-pager status "$SERVICE_NAME"
elif is_running_pid; then
  echo "НЭО работает (PID $(cat "$PID_FILE"))."
  echo "Лог: $LOG_FILE"
else
  echo "НЭО остановлен."
fi
