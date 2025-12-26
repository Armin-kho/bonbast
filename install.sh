#!/usr/bin/env bash
set -euo pipefail

echo "== Bonbast Telegram Bot installer/updater =="

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Please run as root (use sudo)."
  exit 1
fi

INSTALL_DIR_DEFAULT="/root/bonbast-bot"
read -r -p "Install directory [${INSTALL_DIR_DEFAULT}]: " INSTALL_DIR
INSTALL_DIR="${INSTALL_DIR:-$INSTALL_DIR_DEFAULT}"

APP_DIR="${INSTALL_DIR}/app"
mkdir -p "${APP_DIR}"

# You can override this if you fork:
#   RAW_BASE="https://raw.githubusercontent.com/<YOU>/<REPO>/<BRANCH>" bash install.sh
RAW_BASE_DEFAULT="https://raw.githubusercontent.com/Armin-kho/bonbast/main"
RAW_BASE="${RAW_BASE:-$RAW_BASE_DEFAULT}"

echo "Using RAW_BASE: ${RAW_BASE}"
echo "Installing system dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y wget ca-certificates curl sqlite3 \
  python3 python3-pip python3-venv || true

# On some Ubuntu/Debian versions python3-venv is versioned
# Try to ensure it's present:
if ! python3 -m venv /tmp/_venv_test >/dev/null 2>&1; then
  PYV="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  apt-get install -y "python${PYV}-venv" || true
  rm -rf /tmp/_venv_test || true
fi

FILES=(requirements.txt storage.py main.py bonbast_client.py README.md)

echo "Downloading app files..."
for f in "${FILES[@]}"; do
  echo "  - ${f}"
  wget -q "${RAW_BASE}/${f}" -O "${APP_DIR}/${f}"
done

cd "${APP_DIR}"

echo "Creating/Updating venv..."
if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

ENV_FILE="${APP_DIR}/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo ".env not found. Let's create it."

  read -r -p "Telegram BOT_TOKEN: " BOT_TOKEN
  read -r -p "Admin user IDs (comma or space separated): " ADMIN_IDS
  read -r -p "DB path [${APP_DIR}/data.db]: " DB_PATH
  DB_PATH="${DB_PATH:-${APP_DIR}/data.db}"

  cat > "${ENV_FILE}" <<EOF
BOT_TOKEN=${BOT_TOKEN}
ADMIN_IDS=${ADMIN_IDS}
DB_PATH=${DB_PATH}
LOG_LEVEL=INFO
EOF

  echo "Created .env"
else
  echo ".env already exists."
  read -r -p "Keep existing .env? [Y/n]: " KEEP
  KEEP="${KEEP:-Y}"
  if [[ "${KEEP}" =~ ^[Nn]$ ]]; then
    rm -f "${ENV_FILE}"
    echo "Removed .env. Re-run installer to recreate it."
    exit 0
  fi
fi

SERVICE_FILE="/etc/systemd/system/bonbast-bot.service"
if [[ ! -f "${SERVICE_FILE}" ]]; then
  echo "Creating systemd service..."
  cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Bonbast Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/main.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable bonbast-bot
fi

echo "Restarting service..."
systemctl restart bonbast-bot

echo "Done."
echo "Status:  systemctl status bonbast-bot --no-pager"
echo "Logs:    journalctl -u bonbast-bot -f"
