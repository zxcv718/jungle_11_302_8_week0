#!/usr/bin/env bash

# Robust one-shot deploy for Ubuntu/Debian hosts
# - Creates or repairs a Python venv in the project dir
# - Installs requirements with venv pip (avoids PEP 668)
# - Writes and starts a systemd service

set -Eeuo pipefail

APP_NAME="jstory"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
VENV_DIR="$APP_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"

echo "[1/7] Ensuring system deps..."
if command -v apt-get >/dev/null 2>&1; then
  if [[ $EUID -ne 0 ]]; then SUDO=sudo; else SUDO=; fi
  $SUDO apt-get update -y
  $SUDO apt-get install -y --no-install-recommends \
    ${PYTHON_BIN} ${PYTHON_BIN}-venv python3-pip \
    ca-certificates curl
fi

echo "[2/7] Creating or repairing venv..."
if [ ! -x "$VENV_DIR/bin/python" ]; then
  # If a broken venv folder exists, remove it first
  if [ -d "$VENV_DIR" ]; then
    echo "Existing venv is missing python; recreating..."
    rm -rf "$VENV_DIR"
  fi
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -V || { echo "Failed to create venv" >&2; exit 1; }

echo "[3/7] Installing Python requirements..."
# Make sure venv has pip (Debian-based images may need this)
"$VENV_DIR/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$VENV_DIR/bin/python" -m pip install --upgrade pip wheel
"$VENV_DIR/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

echo "[4/7] Preparing environment file..."
# Ensure HOST is 0.0.0.0 so the app is reachable over the network
if ! grep -q '^HOST=' "$APP_DIR/.env" 2>/dev/null; then
  echo 'HOST=0.0.0.0' >> "$APP_DIR/.env"
else
  sed -i.bak 's/^HOST=.*/HOST=0.0.0.0/' "$APP_DIR/.env"
fi
# Default PORT if not set
if ! grep -q '^PORT=' "$APP_DIR/.env" 2>/dev/null; then
  echo 'PORT=5050' >> "$APP_DIR/.env"
fi

echo "[5/7] Sanity check app import..."
if [ "${SKIP_SANITY:-0}" = "1" ]; then
  echo "Skipping sanity check (SKIP_SANITY=1)"
else
  (cd "$APP_DIR" && "$VENV_DIR/bin/python" - <<'PY') || { echo "Sanity check failed. Ensure project files are present under $APP_DIR" >&2; exit 1; }
from app import create_app
app = create_app('config.Config')
print('OK: app created')
PY
fi

echo "[6/7] Writing systemd service..."
SERVICE_USER="${SUDO_USER:-$(whoami)}"
SERVICE_CONTENT="[Unit]
Description=JStory Flask App (Socket.IO)
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${VENV_DIR}/bin/python ${APP_DIR}/main.py
Restart=always
RestartSec=3
User=${SERVICE_USER}
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
"
echo "$SERVICE_CONTENT" | sudo tee "$SERVICE_FILE" >/dev/null

echo "[7/7] Enabling and starting service..."
sudo systemctl daemon-reload
sudo systemctl enable "$APP_NAME"
sudo systemctl restart "$APP_NAME"

echo "Done. Check status and logs with:"
echo "  sudo systemctl status ${APP_NAME} --no-pager"
echo "  sudo journalctl -u ${APP_NAME} -f --no-pager"
echo
echo "If using a cloud VM, open TCP port set in .env (default 5050) in your security group."
