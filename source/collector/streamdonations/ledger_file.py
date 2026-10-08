"""Open a JSONL ledger export for viewing.

The file is the same one Export writes: one donation object per line.
Reading it does not change the open ledger and does not write the file back.
A settings file, a CSV spreadsheet, and a database file are refused.
"""

from __future__ import annotations

import json
import math

from .goals import round2
from .kinds import KIND_LABELS, PLATFORM_LABELS
from .money import quote
from .settings_file import KIND as SETTINGS_KIND
from .util import display_donor

MAX_BYTES = 1_000_000
MAX_ROWS = 5000


class _BadLine(Exception):
    """One line is not a donation object. The message stays generic."""


def parse_ledger_jsonl(body: bytes) -> list[dict]:
    """Donation objects from a JSONL export. Nothing is written."""
    text = _decode(body)
    if _is_csv(text):
        raise ValueError("CSV cannot be opened as a ledger.")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _parse_lines(text)
    if isinstance(payload, list):
        raise ValueError("A ledger archive is JSONL, one donation per line.")
    if isinstance(payload, dict) and payload.get("kind") == SETTINGS_KIND:
        raise ValueError("This is a settings file, not a ledger.")
    if isinstance(payload, dict):
        return [_row(payload, 1)]
    raise ValueError("Line 1 is not a ledger row.")


def present_archive(
    rows: list[dict],
    *,
    membership_price: float | None,
    rates: dict[str, float] | None,
) -> dict:
    """Newest first, with a running total. raw is quoted, then left out."""
    table = {"USD": 1.0, **(rates or {})}
    ordered = sorted(rows, key=lambda row: (str(row.get("created_at") or ""), int(row["id"])))
    shown: list[dict] = []
    total = 0.0
    for row in ordered:
        # raw prices anniversaries and YouTube amounts. It is not returned.
        source = {
            "platform": row.get("platform") or "",
            "kind": row.get("kind") or "",
            "message": row.get("message") or "",
            "amount_value": row.get("amount_value"),
            "currency": row.get("currency"),
            "raw": row.get("raw") if isinstance(row.get("raw"), dict) else {},
        }
        priced = quote(source, membership_price, table)
        usd = None if row.get("hidden") else priced.usd
        total = round2(total + (float(usd) if usd is not None else 0.0))
        shown.append({
            "id": row.get("id"),
            "author_name": display_donor(row.get("author_name")),
            "platform": row.get("platform") or "",
            "platform_label": PLATFORM_LABELS.get(str(row.get("platform") or ""), row.get("platform") or ""),
            "kind": row.get("kind") or "",
            "kind_label": priced.label,
            "message": row.get("message") or "",
            "amount_value": row.get("amount_value"),
            "currency": row.get("currency"),
            "quoted_amount": priced.amount,
            "quoted_currency": priced.currency,
            "amount_usd": priced.usd,
            "created_at": row.get("created_at") or "",
            "hidden": bool(row.get("hidden")),
            "manual": bool(row.get("manual")),
            "note": row.get("note") or "",
            "running_usd": total,
        })
    shown.reverse()
    return {"donations": shown, "total_usd": total, "count": len(rows)}


def _decode(body: bytes) -> str:
    if isinstance(body, bytearray):
        body = bytes(body)
    if not isinstance(body, bytes):
        raise ValueError("This ledger file was not JSONL.")
    if len(body) > MAX_BYTES:
        raise ValueError("That ledger file is too large.")
    if body.startswith(b"\xef\xbb\xbf"):
        body = body[3:]
    if body.startswith(b"SQLite format"):
        raise ValueError("A database file stays closed.")
    if not body.strip():
        raise ValueError("This ledger file is empty.")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("This ledger file was not JSONL.") from exc
    if not text.strip():
        raise ValueError("This ledger file is empty.")
    return text


def _is_csv(text: str) -> bool:
    for line in text.splitlines():
        if not line.strip():
            continue
        compact = "".join(line.split()).lower()
        return compact.startswith("date,time,donor")
    return False


def _parse_lines(text: str) -> list[dict]:
    rows: list[dict] = []
    seen = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        seen += 1
        if seen > MAX_ROWS:
            raise ValueError("A ledger archive is limited to 5000 donations.")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Line {number} is not a ledger row.") from exc
        rows.append(_row(payload, number))
    if not rows:
        raise ValueError("This ledger file is empty.")
    return rows


def _row(payload: object, number: int) -> dict:
    try:
        return _build(payload, number)
    except _BadLine:
        raise ValueError(f"Line {number} is not a ledger row.") from None


def _build(payload: object, number: int) -> dict:
    if not isinstance(payload, dict):
        raise _BadLine
    platform = payload.get("platform")
    kind = payload.get("kind")
    if not isinstance(platform, str) or platform.strip().lower() not in PLATFORM_LABELS:
        raise _BadLine
    if not isinstance(kind, str) or kind.strip().lower() not in KIND_LABELS:
        raise _BadLine
    present_id = "id" in payload and payload.get("id") is not None
    raw = payload.get("raw")
    return {
        "id": _identifier(payload.get("id"), number, present_id),
        "platform": platform.strip().lower(),
        "kind": kind.strip().lower(),
        "author_name": _text(payload.get("author_name", "")),
        "message": _text(payload.get("message", "")),
        "amount_value": _amount(payload.get("amount_value") if "amount_value" in payload else None),
        "currency": _currency(payload.get("currency") if "currency" in payload else None),
        "created_at": _text(payload.get("created_at", "")),
        "hidden": _flag(payload.get("hidden", False)),
        "manual": _manual(payload),
        "note": _text(payload.get("note", "")),
        "raw": raw if isinstance(raw, dict) else None,
    }


def _text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _BadLine
    return value


def _currency(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise _BadLine
    text = value.strip()
    return text or None


def _amount(value: object) -> int | float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _BadLine
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _BadLine
        return value
    try:
        as_float = float(value)
    except OverflowError as exc:
        raise _BadLine from exc
    if not math.isfinite(as_float):
        raise _BadLine
    return value


def _flag(value: object) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise _BadLine
    return value


def _identifier(value: object, number: int, present: bool) -> int:
    if not present:
        return number
    if isinstance(value, bool) or not isinstance(value, int):
        raise _BadLine
    return value


def _manual(payload: dict) -> bool:
    if "manual" in payload and payload.get("manual") is not None:
        manual = payload.get("manual")
        if not isinstance(manual, bool):
            raise _BadLine
        return manual
    dedupe = payload.get("dedupe_key")
    return isinstance(dedupe, str) and dedupe.startswith("manual:")
