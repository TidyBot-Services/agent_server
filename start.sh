#!/bin/bash
# Start the agent server with environment from .env
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load .env if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
    echo "[agent_server] Loaded .env (YOLO_SERVER_URL=$YOLO_SERVER_URL)"
fi

# Activate conda env
eval "$(conda shell.bash hook)" 2>/dev/null
conda activate tidybot 2>/dev/null

cd "$SCRIPT_DIR"
exec python3 server.py "$@"
