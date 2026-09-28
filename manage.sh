#!/usr/bin/env bash
set -uo pipefail
root_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$root_dir"

if [[ ! -t 0 ]]; then
  echo "Интерактивное меню требует терминал. Для автоматизации используйте ./start.sh и другие команды." >&2
  exit 1
fi

while true; do
  cat <<'MENU'

НЭО · управление
  1) Настроить токен, базу и интервал
  2) Запустить
  3) Остановить
  4) Перезапустить
  5) Статус
  6) Логи
  7) Установить systemd-сервис
  8) Поставить аватарку в Telegram
  9) Перенести код на сервер
  0) Выход
MENU
  read -r -p "Выберите действие: " action
  case "$action" in
    1) ./configure.sh ;;
    2) ./start.sh ;;
    3) ./stop.sh ;;
    4) ./restart.sh ;;
    5) ./status.sh ;;
    6) ./logs.sh ;;
    7) ./scripts/install-service.sh ;;
    8)
      if [[ ! -x .venv/bin/python ]]; then ./scripts/setup.sh; fi
      .venv/bin/python scripts/apply_brand.py
      ;;
    9)
      read -r -p "SSH адрес (user@server): " target
      read -r -p "Папка на сервере [~/bilimclass-neo-bot]: " destination
      ./scripts/deploy.sh "$target" "${destination:-~/bilimclass-neo-bot}"
      ;;
    0) exit 0 ;;
    *) echo "Выберите номер из меню." ;;
  esac
done
