#!/bin/sh
# Build the Electron app with the tools on this machine:
#   python3 or pyenv, pip, and a distrobox (podman) for Node.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"

PY=python3
if command -v pyenv >/dev/null 2>&1; then
  if pyenv which python >/dev/null 2>&1; then
    PY=$(pyenv which python)
  fi
fi

echo "Unit tests with $PY"
PYTHONPATH="$ROOT/source/collector" "$PY" -m unittest discover -s "$ROOT/source/collector/tests" -v

echo "Install the collector with pip"
# --system-site-packages lets the build see the host setuptools module.
# --no-build-isolation then avoids downloading a second copy.
if "$PY" -m venv --system-site-packages "$ROOT/.venv"; then
  "$ROOT/.venv/bin/pip" install --no-build-isolation "$ROOT/source/collector"
else
  echo "python -m venv is unavailable; installing with pip --prefix instead."
  "$PY" -m pip install --no-build-isolation --prefix "$ROOT/.pyprefix" "$ROOT/source/collector"
fi

echo "Build the Node image used by the distrobox"
podman build -t localhost/stream-donations-node -f "$ROOT/Containerfile" "$ROOT"

if ! podman container exists stream-donations; then
  echo "Create the stream-donations distrobox"
  distrobox create --yes --no-entry \
    --image localhost/stream-donations-node \
    --name stream-donations
fi

echo "Assemble bin/ and install Electron inside the distrobox"
distrobox enter --name stream-donations --no-tty -- bash --noprofile --norc -c "
  set -eu
  cd '$ROOT'
  sh scripts/assemble.sh
  cd '$ROOT/bin/app'
  npm install --no-fund --no-audit --cache '$ROOT/build/npm-cache'
  node --check main.js
  node --check preload.js
  node --check '$ROOT/source/collector/streamdonations/web/app.js'
"

ELECTRON="$ROOT/bin/app/node_modules/electron/dist/electron"
if [ ! -x "$ELECTRON" ]; then
  echo "Electron binary was not produced." >&2
  exit 1
fi
chmod 755 "$ROOT/bin/stream-donations" "$ELECTRON" || true
echo "Electron version:"
"$ELECTRON" --version
echo "Built $ROOT/bin/stream-donations"
