#!/usr/bin/env python3
"""Build a portable Windows x64 SuperBerry zip.

Electron, an embeddable CPython, and the collector sit side by side, the same
three pieces as scripts/package-linux.sh:

    SuperBerry-windows-x64/
      SuperBerry.cmd        starts electron\\electron.exe app
      electron/             Electron win32-x64 (version from package.json)
      app/                  main.js, preload.js, package.json, icon.png
      collector/            streamdonations
      python/python.exe     python.org embeddable CPython

The ledger is not copied. Nothing is installed. Downloads are cached in build/.
Runs on any OS with Python 3.9+, so CI and Linux can both produce the zip.
"""

import argparse
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.12.10"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
ELECTRON_URL = "https://github.com/electron/electron/releases/download/v{0}/electron-v{0}-win32-x64.zip"
APP_FILES = ("package.json", "main.js", "preload.js", "icon.png")
LAUNCHER = '@echo off\r\nstart "" "%~dp0electron\\electron.exe" "%~dp0app" %*\r\n'


def electron_version():
    package = json.loads((ROOT / "source" / "electron" / "package.json").read_text(encoding="utf-8"))
    return package["dependencies"]["electron"].lstrip("^~=v")


def fetch(url, cache):
    target = cache / url.rsplit("/", 1)[1]
    if not target.is_file():
        print(f"Downloading {url}")
        partial = target.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=300) as response, open(partial, "wb") as out:
            shutil.copyfileobj(response, out)
        partial.replace(target)
    return target


def enable_collector_path(python_dir):
    """The embeddable Python ignores PYTHONPATH; its ._pth file is the path."""
    pth = next(python_dir.glob("python3*._pth"))
    lines = [line for line in pth.read_text(encoding="utf-8").splitlines() if line.strip()]
    if "..\\collector" not in lines:
        lines.insert(lines.index(".") + 1 if "." in lines else len(lines), "..\\collector")
    pth.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def assemble(dist, electron_zip, python_zip):
    if dist.exists():
        shutil.rmtree(dist)
    (dist / "app").mkdir(parents=True)
    for name in APP_FILES:
        shutil.copy2(ROOT / "source" / "electron" / name, dist / "app" / name)
    shutil.copytree(
        ROOT / "source" / "collector" / "streamdonations",
        dist / "collector" / "streamdonations",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    with zipfile.ZipFile(electron_zip) as archive:
        archive.extractall(dist / "electron")
    with zipfile.ZipFile(python_zip) as archive:
        archive.extractall(dist / "python")
    enable_collector_path(dist / "python")
    (dist / "SuperBerry.cmd").write_bytes(LAUNCHER.encode("ascii"))


def check(dist):
    needed = [
        "SuperBerry.cmd",
        "electron/electron.exe",
        "app/main.js",
        "collector/streamdonations/__main__.py",
        "python/python.exe",
    ]
    missing = [name for name in needed if not (dist / name).is_file()]
    if missing:
        raise SystemExit("Package is missing: " + ", ".join(missing))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "dist"), help="output folder (default dist/)")
    args = parser.parse_args(argv)
    cache = ROOT / "build" / "windows"
    cache.mkdir(parents=True, exist_ok=True)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dist = out / "SuperBerry-windows-x64"
    assemble(dist, fetch(ELECTRON_URL.format(electron_version()), cache), fetch(PYTHON_URL, cache))
    check(dist)
    archive = shutil.make_archive(str(dist), "zip", root_dir=out, base_dir=dist.name)
    print(f"Portable folder: {dist}")
    print(f"Zip: {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
