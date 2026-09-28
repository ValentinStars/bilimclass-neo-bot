#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/.run"
PID_FILE="$RUN_DIR/neo.pid"
LOG_FILE="$RUN_DIR/neo.log"
SERVICE_NAME="bilim-neo.service"

service_installed() {
  [[ -f "$HOME/.config/systemd/user/$SERVICE_NAME" ]] && command -v systemctl >/dev/null 2>&1
}

is_running_pid() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  local cmdline
  cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline")"
  [[ "$cmdline" == *"$ROOT_DIR/.venv/bin/python"* && "$cmdline" == *"bilim_neo"* ]]
}

require_env() {
  if [[ ! -f "$ROOT_DIR/.env" ]]; then
    if [[ -t 0 ]]; then
      "$ROOT_DIR/configure.sh"
    else
      echo "Нет .env. Запустите ./configure.sh в терминале или создайте .env по образцу." >&2
      exit 1
    fi
  fi
  if [[ "$(stat -c '%a' "$ROOT_DIR/.env")" != "600" ]]; then
    chmod 600 "$ROOT_DIR/.env"
  fi
}
