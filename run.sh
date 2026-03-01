#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/env"

# Create venv if it doesn't exist
if [ ! -d "$VENV_DIR/bin" ]; then
    echo "[*] Creation du venv 'env'..."
    python3 -m venv "$VENV_DIR"
    echo "[*] Installation des dependances..."
    "$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
    echo "[+] Environnement pret."
else
    echo "[+] Venv 'env' detecte."
fi

# Allow root to use the current user's X display
xhost +si:localuser:root 2>/dev/null || true

# Run as root via XWayland (force X11) so the window manager decorates the window
echo "[*] Lancement de write_blocker en sudo..."
sudo DISPLAY="$DISPLAY" XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}" QT_QPA_PLATFORM=xcb \
    "$VENV_DIR/bin/python" "$SCRIPT_DIR/write_blocker.py"
