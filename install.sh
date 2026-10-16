#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="$HOME/.local/bin"

mkdir -p "$TARGET_DIR"
ln -sf "$SCRIPT_DIR/rtsp_server.py" "$TARGET_DIR/rtsp-server"
chmod +x "$SCRIPT_DIR/rtsp_server.py"

echo "✅ Berhasil memasang rtsp-server ke $TARGET_DIR/rtsp-server"
echo "Ketik 'rtsp-server --help' untuk melihat panduan penggunaan."
