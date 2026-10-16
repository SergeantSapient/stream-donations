#!/usr/bin/env python3
"""Build a portable Windows x64 SuperBerry zip.

Electron, an embeddable CPython, and the collector sit side by side, the same
three pieces as scripts/package-linux.sh:

    SuperBerry-windows-x64/
      SuperBerry.cmd        starts electron\\electron.exe app
      electron/             Electron win32-x64 (version from package.json)
      app/                  main.js, preload.js, package.json, icon.png
      collector/            streamdonations, tzdata, cacert.pem
      python/python.exe     python.org embeddable CPython

The ledger is not copied. Nothing is installed. Downloads are cached in build/.
Runs on any OS with Python 3.9+, so CI and Linux can both produce the zip.
"""

import argparse
import tarfile
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
# Windows has no system time zone database; zoneinfo reads this package instead.
TZDATA_URL = "https://files.pythonhosted.org/packages/source/t/tzdata/tzdata-2025.2.tar.gz"
# certifi 2026.7.22, the Mozilla CA bundle. A new Windows install does not
# have those roots in the local store, and embeddable CPython will not fetch them.
CERTIFI_URL = (
    "https://files.pythonhosted.org/packages/a3/c2/"
    "24167ea9858356b47a87a50d39908bfdb72ceeefe0041586e704e5376b3a/"
    "certifi-2026.7.22.tar.gz"
)
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


def add_tzdata(collector, tzdata_tar):
    with tarfile.open(tzdata_tar) as archive:
        prefix = archive.getnames()[0].split("/", 1)[0] + "/src/"
        members = [m for m in archive.getmembers() if m.name.startswith(prefix + "tzdata/") and m.isfile()]
        for member in members:
            target = collector / member.name[len(prefix):]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())


def add_ca_bundle(collector, certifi_tar):
    with tarfile.open(certifi_tar) as archive:
        member = next(name for name in archive.getnames() if name.endswith("/certifi/cacert.pem"))
        data = archive.extractfile(member).read()
    if b"BEGIN CERTIFICATE" not in data:
        raise SystemExit("Certificate bundle was not a PEM file.")
    (collector / "cacert.pem").write_bytes(data)


def assemble(dist, electron_zip, python_zip, tzdata_tar, certifi_tar):
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
    add_tzdata(dist / "collector", tzdata_tar)
    add_ca_bundle(dist / "collector", certifi_tar)
    (dist / "SuperBerry.cmd").write_bytes(LAUNCHER.encode("ascii"))


def check(dist):
    needed = [
        "SuperBerry.cmd",
        "electron/electron.exe",
        "app/main.js",
        "collector/streamdonations/__main__.py",
        "python/python.exe",
        "collector/tzdata/zoneinfo/America/Los_Angeles",
        "collector/cacert.pem",
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
    assemble(
        dist,
        fetch(ELECTRON_URL.format(electron_version()), cache),
        fetch(PYTHON_URL, cache),
        fetch(TZDATA_URL, cache),
        fetch(CERTIFI_URL, cache),
    )
    check(dist)
    archive = shutil.make_archive(str(dist), "zip", root_dir=out, base_dir=dist.name)
    print(f"Portable folder: {dist}")
    print(f"Zip: {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
