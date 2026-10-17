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
    connection = sqlite3.connect(_read_only_uri(destination), uri=True)
    try:
        connection.row_factory = sqlite3.Row
        return _archived_document(connection, None)
    finally:
        connection.close()


def read_archive_groups(
    path: Path,
    *,
    membership_price: float | None,
    rates: dict[str, float] | None,
    zone: object = None,
) -> dict:
    """Saved ledgers for the archive dialog. One group is a stream on one local date.

    The newest saved ledger comes first. Dates within it are oldest first.
    Hidden rows are included and add nothing to the day's total. raw is used
    for the quote and is not returned. A missing file is an empty list.
    """
    from .goals import round2
    from .kinds import PLATFORM_LABELS
    from .money import local_date_time, quote
    from .util import display_donor

    destination = Path(path)
    if not destination.is_file():
        return {"groups": []}
    try:
        connection = sqlite3.connect(_read_only_uri(destination), uri=True)
    except sqlite3.Error as exc:
        raise ValueError("The archive could not be read.") from exc
    try:
        connection.row_factory = sqlite3.Row
        try:
            document = _archived_document(connection, None)
            fetched = connection.execute(
                "SELECT * FROM donations ORDER BY created_at ASC, id ASC"
            ).fetchall()
        except sqlite3.Error as exc:
            raise ValueError("The archive could not be read.") from exc
    finally:
        connection.close()

    table = {"USD": 1.0, **(rates or {})}
    by_stream: dict[str, list] = {}
    for row in fetched:
        by_stream.setdefault(str(row["stream_id"] or ""), []).append(row)
    order: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for tab in reversed(document["tabs"]):
        stream_id = str(tab.get("id") or "")
        if not stream_id or stream_id in seen:
            continue
        seen.add(stream_id)
        order.append((stream_id, tab))
    for stream_id in by_stream:
        if stream_id not in seen:
            order.append((stream_id, {"id": stream_id, "stream_title": ""}))

    groups = []
    for stream_id, tab in order:
        title = str(tab.get("stream_title") or "").strip() or "Stream"
        buckets: dict[str, dict] = {}
        day_order: list[str] = []
        for row in by_stream.get(stream_id, []):
            created = str(row["created_at"] or "")
            date, _clock = local_date_time(created, zone)  # type: ignore[arg-type]
            if date not in buckets:
                buckets[date] = {"total": 0.0, "entries": []}
                day_order.append(date)
            entry, usd = _archive_entry(row, membership_price, table, quote, display_donor, PLATFORM_LABELS)
            if usd is not None:
                buckets[date]["total"] = round2(buckets[date]["total"] + float(usd))
            buckets[date]["entries"].append(entry)
        if not day_order:
            day_order = [""]
            buckets[""] = {"total": 0.0, "entries": []}
        for date in day_order:
            bucket = buckets[date]
            groups.append({
                "stream_id": stream_id,
                "title": title,
                "date": date,
                "count": len(bucket["entries"]),
                "total_usd": bucket["total"],
                "entries": bucket["entries"],
            })
    return {"groups": groups}


def _archive_entry(row, membership_price, rates, quote, display_donor, platform_labels):
    """One donation for the dialog. The stored payload is not included."""
    raw = {}
    try:
        parsed = json.loads(row["raw_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        raw = parsed
    priced = quote(
        {
            "platform": row["platform"] or "",
            "kind": row["kind"] or "",
            "message": row["message"] or "",
            "amount_value": row["amount_value"],
            "currency": row["currency"],
            "raw": raw,
        },
        membership_price,
        rates,
    )
    hidden = bool(row["hidden"])
    usd = None if hidden else priced.usd
    return {
        "author_name": display_donor(row["author_name"]),
        "platform": row["platform"] or "",
        "platform_label": platform_labels.get(str(row["platform"] or ""), row["platform"] or ""),
        "kind": row["kind"] or "",
        "kind_label": priced.label,
        "message": row["message"] or "",
        "amount_value": row["amount_value"],
        "currency": row["currency"] or "",
        "quoted_amount": priced.amount,
        "quoted_currency": priced.currency or "",
        "amount_usd": priced.usd,
        "created_at": row["created_at"] or "",
        "hidden": hidden,
    }, usd


def _read_only_uri(path: Path) -> str:
    """A file: URI for read-only open. %, #, ?, and spaces in the path are escaped.

    Windows paths become file:///C:/..., which SQLite reads on Windows.
    """
    return Path(path).resolve().as_uri() + "?mode=ro"


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
