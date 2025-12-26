#!/usr/bin/env bash
set -euo pipefail

REPO_OWNER="Armin-kho"
REPO_NAME="bonbast"
REPO_BRANCH="main"
RAW_BASE="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${REPO_BRANCH}"

APP_DIR_DEFAULT="/root/bonbast-bot"
SERVICE_NAME="bonbast-bot"
APP_SUBDIR="app"

need_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root (sudo -i)."
    exit 1
  fi
}

apt_install() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y python3 python3-venv python3-pip curl ca-certificates
}

download_file() {
  local url="$1"
  local out="$2"
  curl -fsSL "$url" -o "$out"
}

prompt() {
  local var="$1"
  local text="$2"
  local def="${3:-}"
  local val=""
  read -r -p "${text}${def:+ [${def}]}: " val
  if [[ -z "$val" ]]; then
    val="$def"
  fi
  printf -v "$var" '%s' "$val"
}

main() {
  need_root
  echo "== Bonbast Telegram Bot installer/updater =="

  local install_dir=""
  prompt install_dir "Install directory" "$APP_DIR_DEFAULT"

  mkdir -p "${install_dir}/${APP_SUBDIR}"
  cd "${install_dir}/${APP_SUBDIR}"

  echo "Installing OS deps..."
  apt_install

  echo "Downloading app files from ${REPO_OWNER}/${REPO_NAME}@${REPO_BRANCH} ..."
  download_file "${RAW_BASE}/requirements.txt" "requirements.txt"
  download_file "${RAW_BASE}/main.py" "main.py"
  download_file "${RAW_BASE}/storage.py" "storage.py"
  download_file "${RAW_BASE}/bonbast_client.py" "bonbast_client.py"

  echo "Creating/Updating venv..."
  if [[ ! -d ".venv" ]]; then
    python3 -m venv .venv
  fi
  . ".venv/bin/activate"
  python -m pip install -U pip
  python -m pip install -r requirements.txt

  # .env
  local env_file="${install_dir}/${APP_SUBDIR}/.env"
  if [[ -f "$env_file" ]]; then
    read -r -p ".env already exists. Keep existing .env? [Y/n]: " keep
    keep="${keep:-Y}"
    if [[ "$keep" =~ ^[Nn]$ ]]; then
      rm -f "$env_file"
    fi
  fi

  if [[ ! -f "$env_file" ]]; then
    echo "Creating .env ..."
    local bot_token=""
    local admin_ids=""
    prompt bot_token "BOT_TOKEN (from BotFather)" ""
    prompt admin_ids "ADMIN_IDS (your numeric Telegram user id, comma-separated if multiple)" ""
    cat > "$env_file" <<EOF
BOT_TOKEN=${bot_token}
ADMIN_IDS=${admin_ids}
LOG_LEVEL=INFO
# DB_PATH=${install_dir}/${APP_SUBDIR}/data.db
EOF
  fi

  # systemd unit
  local unit="/etc/systemd/system/${SERVICE_NAME}.service"
  cat > "$unit" <<EOF
[Unit]
Description=Bonbast Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${install_dir}/${APP_SUBDIR}
EnvironmentFile=${install_dir}/${APP_SUBDIR}/.env
ExecStart=${install_dir}/${APP_SUBDIR}/.venv/bin/python ${install_dir}/${APP_SUBDIR}/main.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}" >/dev/null 2>&1 || true
  systemctl restart "${SERVICE_NAME}"

  echo "Done."
  echo "Status: systemctl status ${SERVICE_NAME} --no-pager"
  echo "Logs:   journalctl -u ${SERVICE_NAME} -f"
}

main "$@"
