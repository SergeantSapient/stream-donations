"""Copy one open ledger into archive.db beside the live database.

The file holds archived ledgers only. Secrets, other ledgers, presets, and
BLUE script text stay in the live database.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

ARCHIVE_NAME = "archive.db"
_STREAMS_KEY = "streams"
_COLUMNS = (
    "id",
    "dedupe_key",
    "platform",
    "kind",
    "external_id",
    "channel",
    "author_name",
    "author_id",
    "message",
    "amount_value",
    "amount_micros",
    "currency",
    "created_at",
    "received_at",
    "processed",
    "processed_at",
    "note",
    "raw_json",
    "hidden",
    "stream_id",
)


def write_archived_ledger(path: Path, schema: str, tab: dict, rows: list[dict]) -> int:
    """Replace this ledger's copy in archive.db. Other archived ledgers stay."""
    stream_id = str(tab.get("id") or "")
    if not stream_id:
        raise ValueError("Unknown stream tab.")
    destination = Path(path)
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(destination.parent, 0o700)
    connection = sqlite3.connect(destination)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(schema)
        connection.execute("BEGIN")
        connection.execute("DELETE FROM donations WHERE stream_id = ?", (stream_id,))
        if rows:
            placeholders = ", ".join("?" for _ in _COLUMNS)
            connection.executemany(
                f"INSERT INTO donations ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
                [tuple(row[name] for name in _COLUMNS) for row in rows],
            )
        document = _archived_document(connection, tab)
        connection.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (_STREAMS_KEY, json.dumps(document, separators=(",", ":"))),
        )
        connection.commit()
    except Exception:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        connection.close()
    _chmod_database(destination)
    return len(rows)


def read_archived_document(path: Path) -> dict:
    """The streams document stored in archive.db. Missing file is empty."""
    destination = Path(path)
    if not destination.is_file():
        return {"active_id": "", "tabs": []}
    connection = sqlite3.connect(f"file:{destination}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        return _archived_document(connection, None)
    finally:
        connection.close()


def _archived_document(connection: sqlite3.Connection, tab: dict | None) -> dict:
    row = connection.execute(
        "SELECT value FROM settings WHERE key = ?",
        (_STREAMS_KEY,),
    ).fetchone()
    document = {"active_id": "", "tabs": []}
    if row is not None:
        try:
            parsed = json.loads(row["value"])
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("tabs"), list):
            document["tabs"] = [item for item in parsed["tabs"] if isinstance(item, dict)]
            document["active_id"] = str(parsed.get("active_id") or "")
    if tab is None:
        return document
    stream_id = str(tab["id"])
    document["tabs"] = [item for item in document["tabs"] if item.get("id") != stream_id]
    document["tabs"].append(tab)
    document["active_id"] = stream_id
    return document


def _chmod_database(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            os.chmod(candidate, 0o600)
