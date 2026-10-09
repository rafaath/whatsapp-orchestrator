#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$WHATSAPP_ROOT"
exec "$WHATSAPP_ROOT/.venv/bin/python" -u -m control.supervisor
