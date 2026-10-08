#!/usr/bin/env python3
"""Record a shipped change and keep the collector and Electron versions together.

Usage:
  python3 scripts/version.py check
  python3 scripts/version.py peripheral|minor|considerable|major "What changed"

Wishlist work uses considerable unless the request says it is major.
peripheral writes a changelog note and leaves the number as it is.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source/collector"))

from streamdonations.versioning import KINDS, apply_change, published_version  # noqa: E402


def _mirror_checkout(version: str) -> None:
    """The checkout launcher reads bin/. The image is built from source/."""
    init = ROOT / "bin/collector/streamdonations/__init__.py"
    package = ROOT / "bin/app/package.json"
    if init.is_file():
        text = init.read_text(encoding="utf-8")
        init.write_text(
            re.sub(
                r'__version__\s*=\s*"\d+\.\d+\.\d+"',
                f'__version__ = "{version}"',
                text,
                count=1,
            ),
            encoding="utf-8",
        )
    if package.is_file():
        text = package.read_text(encoding="utf-8")
        package.write_text(
            re.sub(
                r'("version"\s*:\s*")\d+\.\d+\.\d+(")',
                rf"\g<1>{version}\2",
                text,
                count=1,
            ),
            encoding="utf-8",
        )


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "check":
        print(published_version(ROOT))
        return 0
    if len(argv) != 3 or argv[1] not in KINDS:
        print(
            'usage: python3 scripts/version.py check\n'
            '       python3 scripts/version.py peripheral|minor|considerable|major "What changed"',
            file=sys.stderr,
        )
        return 2
    version = apply_change(ROOT, argv[1], argv[2], date.today().isoformat())
    _mirror_checkout(version)
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
