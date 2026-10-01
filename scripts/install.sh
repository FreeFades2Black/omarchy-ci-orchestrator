#!/usr/bin/env bash
# ==============================================================================
# Autonomous CI/CD Orchestrator - 1-Touch Bootstrap Script (Arch Linux / Omarchy)
# ==============================================================================
set -euo pipefail

echo "[*] Bootstrapping Autonomous CI Orchestrator on Arch Linux..."

# 1. System packages
echo "[*] Installing required system packages via pacman..."
if command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed --noconfirm python python-pip sqlite libnotify curl jq github-cli
fi

# 2. Base directory setup
AGENTS_DIR="${HOME}/agents"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
mkdir -p "${AGENTS_DIR}"/{cache,scripts,systemd,docs,repo_agent,scraper_agent,shared}
mkdir -p "${SYSTEMD_USER_DIR}"

# 3. Synchronize repository files to ~/agents
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "[*] Deploying pipeline files from ${SCRIPT_DIR} to ${AGENTS_DIR}..."

cp -r "${SCRIPT_DIR}/repo_agent/"* "${AGENTS_DIR}/repo_agent/"
cp -r "${SCRIPT_DIR}/scraper_agent/"* "${AGENTS_DIR}/scraper_agent/"
cp -r "${SCRIPT_DIR}/shared/"* "${AGENTS_DIR}/shared/"
cp -r "${SCRIPT_DIR}/scripts/"* "${AGENTS_DIR}/scripts/"
cp -r "${SCRIPT_DIR}/systemd/"* "${AGENTS_DIR}/systemd/"

# 4. Make scripts executable
chmod +x "${AGENTS_DIR}/scripts/"*.sh

# 5. Lock down permissions
chmod 700 "${AGENTS_DIR}"
chmod 700 "${AGENTS_DIR}/cache"

# 6. Initialize environment file if missing
ENV_FILE="${AGENTS_DIR}/repo_agent/.env"
if [ ! -f "${ENV_FILE}" ]; then
    if [ -f "${AGENTS_DIR}/repo_agent/.env.example" ]; then
        cp "${AGENTS_DIR}/repo_agent/.env.example" "${ENV_FILE}"
    else
        touch "${ENV_FILE}"
        echo 'GITHUB_TOKEN=""' > "${ENV_FILE}"
    fi
    chmod 600 "${ENV_FILE}"
    echo "[+] Initialized ${ENV_FILE} with 600 permissions."
fi

# 7. Virtualenv setup & dependency installation
echo "[*] Initializing Python virtual environment in ${AGENTS_DIR}/venv..."
python3 -m venv "${AGENTS_DIR}/venv"
"${AGENTS_DIR}/venv/bin/pip" install --upgrade pip
"${AGENTS_DIR}/venv/bin/pip" install -r "${AGENTS_DIR}/repo_agent/requirements.txt"
"${AGENTS_DIR}/venv/bin/pip" install -r "${AGENTS_DIR}/scraper_agent/requirements.txt"

# 8. Copy service units to systemd user configuration
echo "[*] Registering systemd user service units..."
cp "${AGENTS_DIR}/systemd/"* "${SYSTEMD_USER_DIR}/"
systemctl --user daemon-reload

# 9. Enable persistent lingering for headless execution
if command -v loginctl >/dev/null 2>&1; then
    echo "[*] Enabling systemd user lingering for ${USER}..."
    loginctl enable-linger "${USER}" || true
fi

# 10. Activate units
echo "[*] Enabling and starting services and maintenance timers..."
systemctl --user enable --now scraper-agent.service repo-agent.service intel-cache-vacuum.timer

echo ""
echo "=================================================================="
echo "✅ Autonomous CI Orchestrator successfully deployed on Omarchy."
echo "=================================================================="
echo "Status check:  systemctl --user status scraper-agent.service repo-agent.service"
echo "Health API:    curl -s http://127.0.0.1:8484/health | jq ."
echo "Logs:          journalctl --user-unit=repo-agent.service -f"
echo "=================================================================="
