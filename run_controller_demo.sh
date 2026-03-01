#!/bin/bash
# Run the simple controller demo
# Usage: ./run_controller_demo.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

python3 controllers/demo.py "$@"
