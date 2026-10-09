#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$WHATSAPP_ROOT"

for tool in go uv; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'Missing WSL prerequisite: %s. See LOCAL_SETUP.md. Nothing was started.\n' "$tool" >&2
        exit 1
    fi
done
if [ ! -x "$UV_PYTHON" ]; then
    printf 'Install Python 3.11 or newer at /usr/bin/python3 in WSL. See LOCAL_SETUP.md.\n' >&2
    exit 1
fi
"$UV_PYTHON" - "$(go env GOVERSION)" <<'PY'
import re
import sys
from pathlib import Path
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required in WSL; no server was started.")
required = re.search(r"(?m)^go (\d+)\.(\d+)(?:\.(\d+))?$", Path("whatsapp-bridge/go.mod").read_text())
installed = re.fullmatch(r"go(\d+)\.(\d+)(?:\.(\d+))?", sys.argv[1])
if not required or not installed or tuple(int(n or 0) for n in installed.groups()) < tuple(int(n or 0) for n in required.groups()):
    raise SystemExit("Install a stable Go version at least as new as the go directive in whatsapp-bridge/go.mod. No automatic toolchain download or server start.")
PY

uv sync --locked --project "$WHATSAPP_ROOT/whatsapp-mcp-server" --python "$UV_PYTHON"
mkdir -p "$WHATSAPP_ROOT/bin"
(
    cd whatsapp-bridge
    go build -mod=readonly -p 2 -o "$WHATSAPP_ROOT/bin/whatsapp-bridge" .
)
printf '\nDependencies installed and bridge built. No server, device pairing, Codex registration or autostart was started.\n'
