#!/usr/bin/env bash
set -euo pipefail

echo "== Bonbast Telegram Bot installer =="

need_cmd() { command -v "$1" >/dev/null 2>&1; }

if ! need_cmd python3; then
  echo "python3 not found."
  if need_cmd apt-get; then
    echo "Installing python3 + venv via apt..."
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip
  else
    echo "Please install python3 and python3-venv and re-run."
    exit 1
  fi
fi

if ! need_cmd unzip; then
  if need_cmd apt-get; then
    sudo apt-get update
    sudo apt-get install -y unzip
  else
    echo "Please install unzip."
    exit 1
  fi
fi

if ! need_cmd curl; then
  if need_cmd apt-get; then
    sudo apt-get update
    sudo apt-get install -y curl
  else
    echo "Please install curl."
    exit 1
  fi
fi

read -r -p "Install directory [$HOME/bonbast-bot]: " INSTALL_DIR
INSTALL_DIR="${INSTALL_DIR:-$HOME/bonbast-bot}"

read -r -p "GitHub repo zip URL (example: https://github.com/USER/REPO/archive/refs/heads/main.zip): " ZIP_URL
if [[ -z "$ZIP_URL" ]]; then
  echo "ZIP URL is required."
  exit 1
fi

mkdir -p "$INSTALL_DIR"
tmpzip="$(mktemp).zip"
echo "Downloading repo zip..."
curl -L "$ZIP_URL" -o "$tmpzip"

tmpdir="$(mktemp -d)"
unzip -q "$tmpzip" -d "$tmpdir"
rm -f "$tmpzip"

# The zip extracts into a single folder
SRC_DIR="$(find "$tmpdir" -maxdepth 1 -type d -name "*-main" -o -name "*-master" | head -n 1)"
if [[ -z "$SRC_DIR" ]]; then
  SRC_DIR="$(find "$tmpdir" -maxdepth 2 -type d | head -n 1)"
fi

echo "Copying files..."
rm -rf "$INSTALL_DIR/app"
mkdir -p "$INSTALL_DIR/app"
cp -r "$SRC_DIR"/* "$INSTALL_DIR/app/"

cd "$INSTALL_DIR/app"

echo "Creating venv..."
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
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
WorkingDirectory=$INSTALL_DIR/app
EnvironmentFile=$INSTALL_DIR/app/.env
ExecStart=$INSTALL_DIR/app/.venv/bin/python $INSTALL_DIR/app/main.py
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
  echo "  cd $INSTALL_DIR/app"
  echo "  source .venv/bin/activate"
  echo "  export \$(cat .env | xargs) && python main.py"
fi

echo "Done."
