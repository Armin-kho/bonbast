#!/usr/bin/env bash
set -euo pipefail

# ====== CONFIG (edit in GitHub if you fork) ======
REPO_SLUG="Armin-kho/bonbast"
BRANCH="main"
SERVICE_NAME="bonbast-bot"
# ================================================

echo "== Bonbast Telegram Bot installer/updater =="

need_cmd() { command -v "$1" >/dev/null 2>&1; }

apt_install() {
  sudo apt-get update
  for p in "$@"; do
    sudo apt-get install -y "$p" || true
  done
}

if ! need_cmd python3; then
  if need_cmd apt-get; then
    apt_install python3 python3-pip
  else
    echo "python3 not found and no apt-get available. Install Python 3 manually."
    exit 1
  fi
fi

if ! need_cmd curl; then
  if need_cmd apt-get; then
    apt_install curl
  else
    echo "curl not found. Install curl manually."
    exit 1
  fi
fi

PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"

# ensure venv works (Ubuntu/Debian split ensurepip into pythonX.Y-venv)
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  if need_cmd apt-get; then
    apt_install python3-venv "python${PYVER}-venv" python3-pip
  fi
fi

read -r -p "Install directory [$HOME/bonbast-bot]: " INSTALL_DIR
INSTALL_DIR="${INSTALL_DIR:-$HOME/bonbast-bot}"

APP_DIR="$INSTALL_DIR/app"
mkdir -p "$APP_DIR"

RAW_BASE="https://raw.githubusercontent.com/${REPO_SLUG}/${BRANCH}"

echo "Downloading app files from ${REPO_SLUG}@${BRANCH} ..."
download() {
  local file="$1"
  echo "  - $file"
  curl -fsSL "${RAW_BASE}/${file}" -o "${APP_DIR}/${file}"
}

download requirements.txt
download storage.py
download main.py
download bonbast_client.py || true
download README.md || true

cd "$APP_DIR"

if [[ ! -d ".venv" ]]; then
  echo "Creating venv..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

if [[ -f ".env" ]]; then
  echo ".env already exists."
  read -r -p "Keep existing .env? [Y/n]: " keep
  keep="${keep:-Y}"
else
  keep="n"
fi

if [[ ! "$keep" =~ ^[Yy]$ ]]; then
  read -r -p "Telegram BOT_TOKEN: " BOT_TOKEN
  read -r -p "OWNER_IDS (comma separated numeric user IDs): " OWNER_IDS
  cat > .env <<EOF
BOT_TOKEN=${BOT_TOKEN}
OWNER_IDS=${OWNER_IDS}
DB_PATH=${INSTALL_DIR}/bot.db
EOF
fi

# systemd service
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ ! -f "$SERVICE_FILE" ]]; then
  echo "Installing systemd service..."
  sudo bash -c "cat > '$SERVICE_FILE' <<EOF
[Unit]
Description=Bonbast Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/main.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF"
  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE_NAME"
fi

echo "Restarting service..."
sudo systemctl restart "$SERVICE_NAME"

echo "Done."
echo "Status: sudo systemctl status $SERVICE_NAME --no-pager"
echo "Logs:   sudo journalctl -u $SERVICE_NAME -f"
