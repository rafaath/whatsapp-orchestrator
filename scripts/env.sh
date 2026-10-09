#!/usr/bin/env bash

WHATSAPP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export WHATSAPP_ROOT
export PATH="$WHATSAPP_ROOT/.tools/go/bin:$PATH"
export GOTOOLCHAIN=local CGO_ENABLED=0 GOMAXPROCS=2
export GOPATH="$WHATSAPP_ROOT/.cache/go"
export GOMODCACHE="$WHATSAPP_ROOT/.cache/go-mod"
export GOCACHE="$WHATSAPP_ROOT/.cache/go-build"
export GOTMPDIR="$WHATSAPP_ROOT/.cache/tmp"
export UV_CACHE_DIR="$WHATSAPP_ROOT/.cache/uv"
export UV_PROJECT_ENVIRONMENT="$WHATSAPP_ROOT/.venv"
export UV_PYTHON_INSTALL_DIR="$WHATSAPP_ROOT/.tools/python"
export UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/bin/python3
export TMPDIR="$WHATSAPP_ROOT/.cache/tmp" PYTHONDONTWRITEBYTECODE=1
export WHATSAPP_STORE_DIR="$WHATSAPP_ROOT/runtime"
umask 077
mkdir -p "$GOTMPDIR" "$WHATSAPP_STORE_DIR"
