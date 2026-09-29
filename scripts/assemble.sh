#!/bin/sh
# Copy the Electron shell and the Python collector into bin/.
# build.sh runs this inside the stream-donations distrobox.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
BIN="$ROOT/bin"
rm -rf "$BIN/app" "$BIN/collector"
mkdir -p "$BIN/app" "$BIN/collector" "$ROOT/data" "$ROOT/build"
cp "$ROOT/source/electron/package.json" "$BIN/app/package.json"
cp "$ROOT/source/electron/main.js" "$BIN/app/main.js"
cp "$ROOT/source/electron/preload.js" "$BIN/app/preload.js"
cp -R "$ROOT/source/collector/streamdonations" "$BIN/collector/streamdonations"
find "$BIN/collector" -type d -name __pycache__ | while read -r dir; do
  rm -rf "$dir"
done
cp "$ROOT/scripts/stream-donations" "$BIN/stream-donations"
chmod 755 "$BIN/stream-donations"
