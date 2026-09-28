#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd "$(dirname "$0")" && pwd)"
"$root_dir/stop.sh"
"$root_dir/start.sh"
