#!/usr/bin/env bash
set -euo pipefail

REPO_OWNER="Armin-kho"
REPO_NAME="bonbast"
REPO_BRANCH="main"
RAW_BASE="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${REPO_BRANCH}"

echo "== Bonbast Telegram Bot installer/updater =="

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo su -)"
  exit 1
fi

read -r -p "Install directory [/root/bonbast-bot]: " INSTALL_DIR
INSTALL_DIR="${INSTALL_DIR:-/root/bonbast-bot}"
APP_DIR="${INSTALL_DIR}/app"
ENV_FILE="${APP_DIR}/.env"
DB_DIR="${APP_DIR}/data"
DB_PATH_DEFAULT="${DB_DIR}/bonbast.db"

apt-get update -y
apt-get install -y python3 python3-venv python3-pip wget curl

mkdir -p "$APP_DIR" "$DB_DIR"

echo "Downloading app files from ${REPO_OWNER}/${REPO_NAME}@${REPO_BRANCH} ..."
cd "$APP_DIR"
wget -q "${RAW_BASE}/requirements.txt" -O requirements.txt
wget -q "${RAW_BASE}/storage.py" -O storage.py
wget -q "${RAW_BASE}/bonbast_client.py" -O bonbast_client.py
wget -q "${RAW_BASE}/main.py" -O main.py

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

if [[ -f "$ENV_FILE" ]]; then
  echo ".env already exists."
else
  touch "$ENV_FILE"
fi

# Ensure required keys exist
get_env() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -n1 | cut -d= -f2-; }

BOT_TOKEN="$(get_env BOT_TOKEN || true)"
ADMIN_IDS="$(get_env ADMIN_IDS || true)"
DB_PATH="$(get_env DB_PATH || true)"
LOG_LEVEL="$(get_env LOG_LEVEL || true)"

if [[ -z "${BOT_TOKEN}" ]]; then
  read -r -p "BOT_TOKEN: " BOT_TOKEN
fi

if [[ -z "${ADMIN_IDS}" ]]; then
  echo "ADMIN_IDS is required (your Telegram numeric user id)."
  echo "You can get it by messaging @userinfobot in Telegram."
  read -r -p "ADMIN_IDS (comma-separated): " ADMIN_IDS
fi

if [[ -z "${DB_PATH}" ]]; then
  DB_PATH="$DB_PATH_DEFAULT"
fi

if [[ -z "${LOG_LEVEL}" ]]; then
  LOG_LEVEL="INFO"
fi

# Rewrite .env with required keys (preserve other lines)
tmp_env="$(mktemp)"
grep -vE "^(BOT_TOKEN|ADMIN_IDS|DB_PATH|LOG_LEVEL)=" "$ENV_FILE" > "$tmp_env" || true
{
  echo "BOT_TOKEN=${BOT_TOKEN}"
  echo "ADMIN_IDS=${ADMIN_IDS}"
  echo "DB_PATH=${DB_PATH}"
  echo "LOG_LEVEL=${LOG_LEVEL}"
  cat "$tmp_env"
} > "$ENV_FILE"
rm -f "$tmp_env"

SERVICE_FILE="/etc/systemd/system/bonbast-bot.service"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Bonbast Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/main.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable bonbast-bot.service
systemctl restart bonbast-bot.service

echo "Done."
echo "Status: systemctl status bonbast-bot --no-pager"
echo "Logs:   journalctl -u bonbast-bot -f"
