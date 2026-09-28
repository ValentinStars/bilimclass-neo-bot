#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/scripts/common.sh"

if service_installed; then
  exec journalctl --user -u "$SERVICE_NAME" -n 100 -f
fi
mkdir -p "$RUN_DIR"
touch "$LOG_FILE"
exec tail -n 100 -f "$LOG_FILE"
