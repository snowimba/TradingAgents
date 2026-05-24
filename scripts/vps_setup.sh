#!/usr/bin/env bash
# TradingAgents Web UI — VPS one-command setup
# Usage: bash scripts/vps_setup.sh
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { printf "${GREEN}[OK]${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}[WARN]${NC} %s\n" "$*"; }
err()  { printf "${RED}[ERR]${NC} %s\n" "$*"; exit 1; }

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "========================================"
echo " TradingAgents VPS Setup"
echo " Project: $PROJECT_DIR"
echo "========================================"

# --- Check Python ---
PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || true)
        major=$(echo "$ver" | grep -o '[0-9]\+' | head -1)
        minor=$(echo "$ver" | grep -o '[0-9]\+' | tail -1)
        if [ "${major:-0}" -ge 3 ] && [ "${minor:-0}" -ge 10 ]; then
            PYTHON="$candidate"; break
        fi
    fi
done
[ -n "$PYTHON" ] || err "Python >= 3.10 not found. Install it first: apt install python3.12 (Debian/Ubuntu) or yum install python3.12 (RHEL/Fedora)"
log "Python: $PYTHON ($($PYTHON --version))"

# --- Install uv ---
if command -v uv &>/dev/null; then
    log "uv already installed: $(uv --version)"
else
    warn "uv not found, installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    log "uv installed: $(uv --version)"
fi

# --- .env template ---
if [ -f .env ]; then
    log ".env already exists — skipping template"
else
    cat > .env <<'ENVEOF'
# TradingAgents configuration
# Fill in your API keys and model preferences below.

# --- LLM Provider ---
# Options: openai, anthropic, google, deepseek, xai, ollama, openrouter, qwen, glm, minimax
TRADINGAGENTS_LLM_PROVIDER=openai

# --- Models (one for quick tasks, one for deep reasoning) ---
TRADINGAGENTS_DEEP_THINK_LLM=gpt-5.4
TRADINGAGENTS_QUICK_THINK_LLM=gpt-5.4-mini

# --- API Keys (uncomment and fill) ---
# OPENAI_API_KEY=sk-your-key
# ANTHROPIC_API_KEY=sk-ant-your-key
# GOOGLE_API_KEY=your-key
# DEEPSEEK_API_KEY=sk-your-key
# XAI_API_KEY=your-key
# DASHSCOPE_API_KEY=your-key
# ZHIPU_API_KEY=your-key
# MINIMAX_API_KEY=your-key
# OPENROUTER_API_KEY=your-key

# --- Optional: custom OpenAI-compatible backend ---
# When set, a "custom (OpenAI-compatible)" option appears in the Web UI
# TRADINGAGENTS_LLM_BACKEND_URL=http://127.0.0.1:8317/v1

# --- Optional overrides ---
# TRADINGAGENTS_OUTPUT_LANGUAGE=English
# TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
# TRADINGAGENTS_MAX_RISK_ROUNDS=1
# TRADINGAGENTS_WEB_PORT=8888
ENVEOF
    log "Created .env template — edit it to add your API keys"
fi

# --- Install dependencies ---
log "Installing project dependencies..."
uv sync
log "Dependencies installed"

# --- Done ---
echo ""
echo "========================================"
echo " Setup complete!"
echo "========================================"
echo ""
echo " Start the web server:"
echo "   uv run uvicorn web.app:app --host 0.0.0.0 --port 8888"
echo ""
echo " Or use the entry point:"
echo "   uv run tradingagents-web"
echo ""
echo " To run as a background service, use:"
echo "   bash scripts/vps_setup.sh --install-service"
echo ""

# --- Optional: install systemd service ---
if [ "${1:-}" = "--install-service" ]; then
    SERVICE_NAME="tradingagents-web"
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
    USER="${SUDO_USER:-$USER}"

    if [ "$(id -u)" -ne 0 ]; then
        warn "systemd service install requires root. Re-run with sudo:"
        echo "   sudo bash scripts/vps_setup.sh --install-service"
        exit 0
    fi

    cat > "$SERVICE_FILE" <<SVCEOF
[Unit]
Description=TradingAgents Web UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$HOME/.local/bin/uv run uvicorn web.app:app --host 0.0.0.0 --port 8888
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl start "$SERVICE_NAME"
    log "systemd service installed and started: $SERVICE_NAME"
    echo ""
    echo " Manage with:"
    echo "   systemctl status $SERVICE_NAME"
    echo "   systemctl restart $SERVICE_NAME"
    echo "   journalctl -u $SERVICE_NAME -f"
fi
