#!/usr/bin/env bash
# Build/refresh everything: python venv deps, frontend bundle, then restart the service if installed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
cd "$ROOT/frontend"
npm install --silent
npm run build
if systemctl --user is-enabled rookery.service >/dev/null 2>&1; then
  systemctl --user restart rookery.service
  echo "rookery.service restarted"
fi
