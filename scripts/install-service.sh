#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
require_env
"$ROOT_DIR/scripts/setup.sh"
if is_running_pid; then
  "$ROOT_DIR/stop.sh"
fi

service_dir="$HOME/.config/systemd/user"
mkdir -p "$service_dir"
service_file="$service_dir/$SERVICE_NAME"
cat > "$service_file" <<UNIT
[Unit]
Description=BilimClass NEO Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT_DIR
EnvironmentFile=$ROOT_DIR/.env
ExecStart=$ROOT_DIR/.venv/bin/python -m bilim_neo
Restart=on-failure
RestartSec=10
UMask=0077

[Install]
WantedBy=default.target
UNIT
chmod 600 "$service_file"
systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE_NAME"
echo "Сервис установлен. Управление: ./start.sh, ./stop.sh, ./restart.sh, ./status.sh, ./logs.sh"
echo "Чтобы сервис работал после выхода из SSH, выполните: sudo loginctl enable-linger $USER"
