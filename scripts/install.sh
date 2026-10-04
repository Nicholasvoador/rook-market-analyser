#!/usr/bin/env bash
# Install Rook Market Analyser as a systemd *user* service + app-launcher entry, wherever this repo lives.
# Usage: scripts/install.sh            (build, install, enable, start)
#        scripts/install.sh --uninstall
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

if [ "${1:-}" = "--uninstall" ]; then
  systemctl --user disable --now rookery.service 2>/dev/null || true
  rm -f "$UNIT_DIR/rookery.service" "$APP_DIR/rookery.desktop"
  systemctl --user daemon-reload
  echo "Removed the service and launcher. Your data (~/.local/share/rookery) and settings (~/.config/rookery) were kept."
  exit 0
fi

"$ROOT/scripts/build.sh"
mkdir -p "$UNIT_DIR" "$APP_DIR"
sed "s#@ROOT@#$ROOT#g" "$ROOT/scripts/rookery.service" >"$UNIT_DIR/rookery.service"
sed "s#@ROOT@#$ROOT#g" "$ROOT/scripts/rookery.desktop" >"$APP_DIR/rookery.desktop"
chmod +x "$ROOT/scripts/rookery-open"
systemctl --user daemon-reload
systemctl --user enable --now rookery.service
echo "Running on http://127.0.0.1:8787  ·  open it with scripts/rookery-open or from your app launcher."
