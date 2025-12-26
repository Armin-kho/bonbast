#!/usr/bin/env bash
set -euo pipefail

# ====== CONFIG (edit these in GitHub if you fork) ======
REPO_SLUG="Armin-kho/bonbast"
BRANCH="main"
# =======================================================

echo "== Bonbast Telegram Bot installer =="

need_cmd() { command -v "$1" >/dev/null 2>&1; }

apt_install() {
  sudo apt-get update
  # Install each package separately so a missing one doesn't kill the whole install
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

# Ensure venv/ensurepip exists (Ubuntu/Debian split python into packages)
PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  if need_cmd apt-get; then
    apt_install python3-venv "python${PYVER}-venv" python3-pip
  fi
fi

# Final venv sanity check
TMPV="$(mktemp -d)"
if ! python3 -m venv "$TMPV/.venv" >/dev/null 2>&1; then
  echo "ERROR: python venv still failing (ensurepip missing)."
  echo "Try: sudo apt install -y python3-venv python${PYVER}-venv python3-pip"
  rm -rf "$TMPV"
  exit 1
fi
rm -rf "$TMPV"

read -r -p "Install directory [$HOME/bonbast-bot]: " INSTALL_DIR
INSTALL_DIR="${INSTALL_DIR:-$HOME/bonbast-bot}"

APP_DIR="$INSTALL_DIR/app"
mkdir -p "$APP_DIR"

RAW_BASE="https://raw.githubusercontent.com/${REPO_SLUG}/${BRANCH}"

echo "Downloading app files from ${REPO_SLUG}@${BRANCH} ..."
download() {
  local file="$1"
  curl -fsSL "${RAW_BASE}/${file}" -o "${APP_DIR}/${file}"
}

download requirements.txt
download bonbast_client.py
download models.py
download storage.py
download main.py
download README.md || true

cd "$APP_DIR"

echo "Creating venv..."
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

read -r -p "Telegram BOT_TOKEN: " BOT_TOKEN
read -r -p "OWNER_IDS (comma separated numeric user IDs): " OWNER_IDS

cat > .env <<EOF
BOT_TOKEN=${BOT_TOKEN}
OWNER_IDS=${OWNER_IDS}
DB_PATH=${INSTALL_DIR}/bot.db
EOF

echo "Install as systemd service? [Y/n]: "
read -r yn
yn="${yn:-Y}"

if [[ "$yn" =~ ^[Yy]$ ]]; then
  SERVICE_NAME="bonbast-bot"
  SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

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
  sudo systemctl restart "$SERVICE_NAME"
  echo "Service installed & started: $SERVICE_NAME"
  echo "Logs: sudo journalctl -u $SERVICE_NAME -f"
else
  echo "Run manually:"
  echo "  cd $APP_DIR"
  echo "  source .venv/bin/activate"
  echo "  export \$(cat .env | xargs) && python main.py"
fi

echo "Done."
