#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/scripts/common.sh"

if service_installed; then
  systemctl --user stop "$SERVICE_NAME"
  echo "Сервис НЭО остановлен."
  exit
fi

if ! is_running_pid; then
  echo "НЭО не запущен."
  exit
fi

pid="$(cat "$PID_FILE")"
kill -TERM "$pid"
for _ in {1..30}; do
  if ! kill -0 "$pid" 2>/dev/null; then
    break
  fi
  sleep 1
done
if kill -0 "$pid" 2>/dev/null; then
  echo "Процесс ещё завершает работу (PID $pid). Проверьте ./status.sh" >&2
  exit 1
fi
rm -f "$PID_FILE"
echo "НЭО остановлен."
