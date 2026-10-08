"""Where the ledger and secrets live.

The application directory is something you can copy or replace. Tokens,
client secrets, and the database stay in the home directory so they do not
travel with that copy. Linux uses $XDG_DATA_HOME/stream-donations, or
~/.local/share/stream-donations. macOS uses ~/Library/Application Support/
stream-donations. Windows uses %APPDATA%\\stream-donations. A later Windows
or Mac build should keep calling user_data_dir() instead of inventing a
folder next to the binary.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path


class DataPathError(ValueError):
    """The requested database directory is not allowed."""


def user_data_dir(
    *,
    home: Path | None = None,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    home_path = _home(home)
    system = platform or sys.platform
    env = _env(environ)
    if system == "win32":
        base = env.get("APPDATA") or str(home_path / "AppData" / "Roaming")
        return _under_home(Path(base), home_path, home_path / "AppData" / "Roaming") / "stream-donations"
    if system == "darwin":
        return home_path / "Library" / "Application Support" / "stream-donations"
    base = env.get("XDG_DATA_HOME") or str(home_path / ".local" / "share")
    return _under_home(Path(base), home_path, home_path / ".local" / "share") / "stream-donations"


def resolve_data_dir(
    explicit: str = "",
    *,
    home: Path | None = None,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    legacy: list[Path] | None = None,
) -> Path:
    """Directory for donations.db. Never a folder inside the application tree."""

    home_path = _home(home)
    env = _env(environ)
    requested = explicit.strip() or env.get("STREAM_DONATIONS_DATA", "").strip()
    if requested:
        candidate = Path(requested).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        candidate = candidate.resolve()
        if not _is_under(candidate, home_path):
            raise DataPathError(
                "SuperBerry only stores its database inside the home directory. "
                f"Refusing {candidate}."
            )
        if not _inside_application_tree(candidate):
            target = user_data_dir(home=home_path, platform=platform, environ=env)
            if candidate != target:
                return candidate
        else:
            print(
                "Ignoring a data directory inside the application folder. "
                "The ledger stays in the home directory.",
                file=sys.stderr,
            )
    target = user_data_dir(home=home_path, platform=platform, environ=env)
    migrate_legacy_database(target, legacy_database_candidates() if legacy is None else legacy)
    return target


def legacy_database_candidates() -> list[Path]:
    """A checkout used to keep donations.db in <project>/data. Move that once."""

    here = Path(__file__).resolve()
    for parent in here.parents:
        marker = parent / "source" / "collector" / "streamdonations" / "youtube.py"
        if parent.name == "stream-donations" and marker.is_file():
            return [parent / "data" / "donations.db"]
    return []


def migrate_legacy_database(target_dir: Path, legacy: list[Path]) -> None:
    target = target_dir / "donations.db"
    if target.exists():
        return
    for item in legacy:
        source = Path(item)
        try:
            same = source.resolve() == target.resolve()
        except OSError:
            same = False
        if same or not source.is_file():
            continue
        target_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(target_dir, 0o700)
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(source) + suffix)
            dest = Path(str(target) + suffix)
            if not src.exists():
                continue
            os.chmod(src, 0o600)
            shutil.move(src, dest)
            os.chmod(dest, 0o600)
        return


def _home(home: Path | None) -> Path:
    return (Path.home() if home is None else Path(home)).expanduser().resolve()


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    if environ is None:
        return os.environ
    return environ


def _is_under(path: Path, home: Path) -> bool:
    try:
        path.resolve().relative_to(home.resolve())
    except ValueError:
        return False
    return True


def _under_home(candidate: Path, home: Path, fallback: Path) -> Path:
    path = candidate.expanduser()
    if not path.is_absolute():
        path = home / path
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if _is_under(resolved, home):
        return resolved
    return fallback.resolve()


def _inside_application_tree(path: Path) -> bool:
    for candidate in (path, *path.parents):
        if (candidate / "source" / "collector" / "streamdonations" / "youtube.py").is_file():
            return True
        portable = candidate / "collector" / "streamdonations" / "youtube.py"
        launcher = candidate / "SuperBerry"
        legacy = candidate / "StreamDonations"
        if portable.is_file() and (
            launcher.is_file() or legacy.is_file() or (candidate / "app" / "main.js").is_file()
        ):
            return True
    return False
