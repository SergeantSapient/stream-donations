#!/bin/sh
# Build a portable Linux x86_64 folder. Electron, Python, and the collector
# sit next to the launcher. The ledger is not copied. Nothing is installed
# to the system and this script does not ask for root.
#
# Windows and macOS are not produced here. Those builds should assemble the
# same three pieces and keep using the home data directory from paths.py.
set -eu
if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your user, not as root." >&2
  exit 1
fi
if [ "$(uname -s)" != "Linux" ] || [ "$(uname -m)" != "x86_64" ]; then
  echo "This package is Linux x86_64 only." >&2
  exit 1
fi
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
ELECTRON_SRC="$ROOT/bin/app/node_modules/electron/dist"
if [ ! -x "$ELECTRON_SRC/electron" ]; then
  echo "Electron is not built. From the project directory, run scripts/build.sh." >&2
  exit 1
fi
DIST="$ROOT/dist/StreamDonations-linux-x64"
PYTHON_CACHE="$ROOT/build/python-standalone"
mkdir -p "$ROOT/build" "$ROOT/dist"

if [ ! -x "$PYTHON_CACHE/bin/python3" ]; then
  echo "Downloading a private CPython 3.12 into build/ (not installed system-wide)."
  asset=$(python3 - <<'PY'
import json
import urllib.request

url = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
with urllib.request.urlopen(url, timeout=60) as response:
    data = json.load(response)
suffix = "-x86_64-unknown-linux-gnu-install_only.tar.gz"
for asset in data.get("assets", []):
    name = asset.get("name", "")
    if name.startswith("cpython-3.12.") and name.endswith(suffix):
        print(asset["browser_download_url"])
        break
else:
    raise SystemExit("Could not find a CPython 3.12 linux install_only build.")
PY
)
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "$asset" -o "$tmp/python.tar.gz"
  tar -xzf "$tmp/python.tar.gz" -C "$tmp"
  rm -rf "$PYTHON_CACHE"
  mv "$tmp/python" "$PYTHON_CACHE"
  rm -rf "$tmp"
  trap - EXIT
fi

rm -rf "$DIST"
mkdir -p "$DIST/app" "$DIST/collector" "$DIST/electron"
cp -a "$ELECTRON_SRC/." "$DIST/electron/"
cp "$ROOT/source/electron/package.json" "$DIST/app/package.json"
cp "$ROOT/source/electron/main.js" "$DIST/app/main.js"
cp "$ROOT/source/electron/preload.js" "$DIST/app/preload.js"
cp "$ROOT/source/electron/icon.png" "$DIST/app/icon.png"
cp -a "$ROOT/source/collector/streamdonations" "$DIST/collector/streamdonations"
find "$DIST/collector" -depth -name '__pycache__' -exec rm -rf {} + || true
find "$DIST/collector" -name '*.pyc' -delete || true
cp -a "$PYTHON_CACHE" "$DIST/python"
cp "$ROOT/scripts/stream-donations" "$DIST/StreamDonations"
chmod 755 "$DIST/StreamDonations"

# The directory above is enough to run. An AppImage is the same tree wrapped
# so it is one file. If the tool cannot be fetched, the folder is still there.
APPDIR="$ROOT/dist/StreamDonations.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR"
cp -a "$DIST/." "$APPDIR/"
cp "$APPDIR/StreamDonations" "$APPDIR/AppRun"
chmod 755 "$APPDIR/AppRun"
cp "$ROOT/source/electron/icon.png" "$APPDIR/stream-donations.png"
cat > "$APPDIR/stream-donations.desktop" <<'EOF'
[Desktop Entry]
Name=Stream Donations
Exec=StreamDonations
Icon=stream-donations
StartupWMClass=stream-donations
Type=Application
Categories=Utility;
EOF

tool="$ROOT/build/appimagetool"
if [ ! -x "$tool" ]; then
  curl -fsSL -o "$tool" "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" \
    && chmod 755 "$tool" \
    || true
fi
# Set STREAM_DONATIONS_APPIMAGE to write somewhere else when the usual file is open.
image="${STREAM_DONATIONS_APPIMAGE:-$ROOT/dist/StreamDonations-x86_64.AppImage}"
if [ -x "$tool" ]; then
  ARCH=x86_64 "$tool" --appimage-extract-and-run "$APPDIR" "$image" \
    || ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$tool" "$APPDIR" "$image" \
    || echo "AppImage tool could not wrap the folder. Run dist/StreamDonations-linux-x64/StreamDonations instead." >&2
else
  echo "AppImage tool was not downloaded. Run dist/StreamDonations-linux-x64/StreamDonations instead." >&2
fi
echo "Portable folder: $DIST"
echo "AppImage: $image"
