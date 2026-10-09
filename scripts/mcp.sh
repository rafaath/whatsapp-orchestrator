#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
if [ ! -x "$WHATSAPP_ROOT/.venv/bin/python" ]; then
    printf 'Python dependencies are missing. Follow LOCAL_SETUP.md.\n' >&2
    exit 1
fi
cd "$WHATSAPP_ROOT/whatsapp-mcp-server"
exec "$WHATSAPP_ROOT/.venv/bin/python" -u main.py
