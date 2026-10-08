"""Classify a shipped change and keep the two published version strings together.

The number is major.considerable.minor. A minor change raises the last number.
A considerable change raises the middle number and resets the last. A major
change raises the first number and resets the others. A peripheral fix does
not change the number.
"""

from __future__ import annotations

import re
from pathlib import Path

KINDS = ("peripheral", "minor", "considerable", "major")

_LABELS = {
    "peripheral": "Peripheral",
    "minor": "Minor",
    "considerable": "Considerable",
    "major": "Major",
}

_INIT_VERSION = re.compile(r'__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"')
_PACKAGE_VERSION = re.compile(r'("version"\s*:\s*")(\d+\.\d+\.\d+)(")')
_TODO_VERSION = re.compile(r"Current app version is `(\d+\.\d+\.\d+)`")
_HEADING = re.compile(r"^## (\d+\.\d+\.\d+)\b.*\n", re.M)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


def parse_version(text: str) -> tuple[int, int, int]:
    match = _INIT_VERSION.search(text)
    if match is None:
        raise ValueError('The collector version must look like __version__ = "1.0.0".')
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def format_version(parts: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in parts)


def next_version(parts: tuple[int, int, int], kind: str) -> tuple[int, int, int]:
    if kind not in KINDS:
        raise ValueError("Classification must be peripheral, minor, considerable, or major.")
    major, considerable, minor = parts
    if kind == "peripheral":
        return parts
    if kind == "minor":
        return major, considerable, minor + 1
    if kind == "considerable":
        return major, considerable + 1, 0
    return major + 1, 0, 0


def _replace_once(pattern: re.Pattern[str], text: str, repl: str, problem: str) -> str:
    new, count = pattern.subn(repl, text, count=1)
    if count != 1:
        raise ValueError(problem)
    return new


def _add_bullet(changelog: str, version: str, bullet: str) -> str:
    match = None
    for found in _HEADING.finditer(changelog):
        if found.group(1) == version:
            match = found
            break
    if match is None:
        raise ValueError(f"The changelog has no section for {version}.")
    return changelog[: match.end()] + "\n" + bullet + changelog[match.end() :]


def _open_section(changelog: str, version: str, today: str, bullet: str) -> str:
    block = f"## {version} — {today}\n\n{bullet}"
    match = _HEADING.search(changelog)
    if match is None:
        suffix = changelog if changelog.endswith("\n") else changelog + "\n"
        return suffix + "\n" + block
    return changelog[: match.start()] + block + "\n" + changelog[match.start() :]


def apply_change(root: Path, kind: str, summary: str, today: str) -> str:
    """Write the version files under root. Returns the version now in effect."""
    note = " ".join(summary.split())
    if not note:
        raise ValueError("A version note needs a summary.")
    if note[-1] not in ".!?":
        note += "."
    if _DATE.fullmatch(today) is None:
        raise ValueError("The date must be YYYY-MM-DD.")
    init_path = root / "source/collector/streamdonations/__init__.py"
    package_path = root / "source/electron/package.json"
    todo_path = root / "TODO.md"
    changelog_path = root / "CHANGELOG.md"
    current = parse_version(init_path.read_text(encoding="utf-8"))
    updated = next_version(current, kind)
    version = format_version(updated)
    bullet = f"- {today} — {_LABELS[kind]}. {note}\n"
    init_path.write_text(
        _replace_once(
            _INIT_VERSION,
            init_path.read_text(encoding="utf-8"),
            f'__version__ = "{version}"',
            "Could not find the collector version.",
        ),
        encoding="utf-8",
    )
    package_path.write_text(
        _replace_once(
            _PACKAGE_VERSION,
            package_path.read_text(encoding="utf-8"),
            rf"\g<1>{version}\g<3>",
            "Could not find the Electron version.",
        ),
        encoding="utf-8",
    )
    todo_path.write_text(
        _replace_once(
            _TODO_VERSION,
            todo_path.read_text(encoding="utf-8"),
            f"Current app version is `{version}`",
            "Could not find the version line in TODO.md.",
        ),
        encoding="utf-8",
    )
    changelog = changelog_path.read_text(encoding="utf-8")
    if kind == "peripheral":
        changelog = _add_bullet(changelog, version, bullet)
    else:
        changelog = _open_section(changelog, version, today, bullet)
    changelog_path.write_text(changelog, encoding="utf-8")
    return version


def published_version(root: Path) -> str:
    """The version shared by the collector, Electron, the changelog, and TODO.md."""
    init_text = (root / "source/collector/streamdonations/__init__.py").read_text(encoding="utf-8")
    package = (root / "source/electron/package.json").read_text(encoding="utf-8")
    todo = (root / "TODO.md").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    version = format_version(parse_version(init_text))
    package_match = _PACKAGE_VERSION.search(package)
    todo_match = _TODO_VERSION.search(todo)
    if package_match is None or package_match.group(2) != version:
        raise ValueError("Electron's version does not match the collector.")
    if todo_match is None or todo_match.group(1) != version:
        raise ValueError("TODO.md does not match the collector version.")
    if not any(match.group(1) == version for match in _HEADING.finditer(changelog)):
        raise ValueError(f"The changelog has no section for {version}.")
    return version
