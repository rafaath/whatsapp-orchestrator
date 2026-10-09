#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
if [ ! -x "$WHATSAPP_ROOT/bin/whatsapp-bridge" ]; then
    printf 'Bridge has not been built. Follow LOCAL_SETUP.md.\n' >&2
    exit 1
fi
cd "$WHATSAPP_ROOT/whatsapp-bridge"
exec "$WHATSAPP_ROOT/bin/whatsapp-bridge"
