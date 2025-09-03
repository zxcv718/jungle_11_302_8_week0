#!/usr/bin/env bash
set -euo pipefail

# JStory Flask app one-shot deployment (HTTP, no HTTPS)
# - Uses .env in project root (includes FLASK_DEBUG, SECRET_KEY, JWT_SECRET_KEY, MONGO_URI, COOKIE_SECURE)
# - Binds to HOST=0.0.0.0 by default so it’s reachable externally
# - Installs python venv, dependencies, creates a systemd service, and starts it

APP_NAME="jstory"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
VENV_DIR="$APP_DIR/.venv"
PYTHON_BIN="python3"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"

echo "[1/7] Ensuring system deps..."
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -y
  sudo apt-get install -y python3-venv python3-pip
fi

echo "[2/7] Creating venv..."
if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

echo "[3/7] Installing Python requirements..."
pip install --upgrade pip
pip install -r "$APP_DIR/requirements.txt"

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
python - <<'PY'
from dotenv import load_dotenv
load_dotenv()
from app import create_app
app = create_app('config.Config')
print('OK: app created, secret len =', len(app.config.get('SECRET_KEY','')))
PY

echo "[6/7] Writing systemd service..."
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
User=$(whoami)
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
#!/usr/bin/env bash
set -Eeuo pipefail

# One-shot EC2 deploy script for Ubuntu 22.04 (t2.micro)
# - Installs system deps
# - Creates Python venv and installs requirements
# - Ensures .env with required values
# - Sanity tests app import
# - Creates & starts a systemd service

PROJECT_DIR=${PROJECT_DIR:-$(pwd)}
APP_ENTRY=${APP_ENTRY:-main.py}
SERVICE_NAME=${SERVICE_NAME:-jstory}
PY_BIN=${PY_BIN:-python3}

echo "[1/7] System update & base packages"
if [[ $EUID -ne 0 ]]; then SUDO=sudo; else SUDO=; fi
$SUDO apt-get update -y
$SUDO apt-get install -y --no-install-recommends \
  ${PY_BIN} ${PY_BIN}-venv python3-pip \
  build-essential libffi-dev pkg-config \
  ca-certificates curl git

echo "[2/7] Create virtualenv"
cd "$PROJECT_DIR"
if [[ ! -d .venv ]]; then
  ${PY_BIN} -m venv .venv
fi
source .venv/bin/activate
python -m pip install -U pip wheel

echo "[3/7] Install Python dependencies"
if [[ -f requirements.txt ]]; then
  pip install -r requirements.txt
else
  echo "requirements.txt not found in $PROJECT_DIR" >&2
  exit 1
fi

echo "[4/7] Prepare .env"
ENV_FILE=".env"
if [[ ! -f "$ENV_FILE" ]]; then
  : "${MONGO_URI:=}"
  if [[ -z "${MONGO_URI}" ]]; then
    echo "ERROR: MONGO_URI is required. Export MONGO_URI and re-run, or create a .env file manually." >&2
    echo "Example: export MONGO_URI='mongodb+srv://USER:PASS@cluster.mongodb.net/db?retryWrites=true&w=majority'" >&2
    exit 2
  fi
  : "${SECRET_KEY:=$(openssl rand -hex 16 2>/dev/null || echo dev-secret)}"
  : "${JWT_SECRET_KEY:=$(openssl rand -hex 16 2>/dev/null || echo dev-jwt-secret)}"
  cat > "$ENV_FILE" <<EOF
MONGO_URI=${MONGO_URI}
SECRET_KEY=${SECRET_KEY}
JWT_SECRET_KEY=${JWT_SECRET_KEY}
# Bind to 0.0.0.0 so external traffic can reach the app
HOST=0.0.0.0
PORT=5050
# Optional: set to 0 for dev (keeps cookies over HTTP)
COOKIE_SECURE=False
FLASK_DEBUG=0
EOF
  echo "Created .env"
else
  echo ".env exists; using existing configuration"
fi

echo "[5/7] Sanity test app import"
python - <<'PY'
import sys
from dotenv import load_dotenv
load_dotenv()
try:
    from app import create_app
    app = create_app('config.Config')
    print('APP_IMPORT_OK')
except Exception as e:
    print('APP_IMPORT_FAIL', e)
    sys.exit(3)
PY

echo "[6/7] Create systemd service"
SERVICE_FILE=/etc/systemd/system/${SERVICE_NAME}.service
$SUDO bash -c "cat > ${SERVICE_FILE}" <<EOF
[Unit]
Description=JStory Flask-SocketIO Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PROJECT_DIR}/.venv/bin/python ${PROJECT_DIR}/${APP_ENTRY}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

$SUDO systemctl daemon-reload
$SUDO systemctl enable ${SERVICE_NAME}

echo "[7/7] Start service"
$SUDO systemctl restart ${SERVICE_NAME}
$SUDO sleep 1 || true
$SUDO systemctl --no-pager --full status ${SERVICE_NAME} || true

echo "Done. If the service is active, app should be listening on PORT from .env (default 5050)."
echo "Remember to open the port in your EC2 security group or put a reverse proxy in front."
