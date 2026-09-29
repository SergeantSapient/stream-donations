"""SQLite ledger. The database file is the thing the user processes."""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import threading
from pathlib import Path

from .kinds import KIND_LABELS, PLATFORM_LABELS
from .money import CSV_FIELDS, DISPLAY_KIND_SQL, money_text, quote
from .normalize import Donation
from .util import csv_safe, utc_now_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS donations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    kind TEXT NOT NULL,
    external_id TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    author_name TEXT NOT NULL DEFAULT '',
    author_id TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    amount_value REAL,
    amount_micros INTEGER,
    currency TEXT,
    created_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0,
    processed_at TEXT,
    note TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_donations_created ON donations (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_donations_processed ON donations (processed, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_donations_platform ON donations (platform, created_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_status (
    platform TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    platform TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._tighten()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _tighten(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            if candidate.exists():
                os.chmod(candidate, 0o600)

    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return default
        return str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            self._conn.commit()
        self._tighten()

    def delete_setting(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            self._conn.commit()

    def add_many(self, donations: list[Donation]) -> int:
        if not donations:
            return 0
        received = utc_now_iso()
        inserted = 0
        with self._lock:
            try:
                for donation in donations:
                    if donation.platform not in PLATFORM_LABELS or donation.kind not in KIND_LABELS:
                        raise ValueError(f"unknown donation type {donation.platform}/{donation.kind}")
                    created = donation.created_at or received
                    self._conn.execute(
                        """
                        INSERT OR IGNORE INTO donations (
                            dedupe_key, platform, kind, external_id, channel,
                            author_name, author_id, message, amount_value,
                            amount_micros, currency, created_at, received_at, raw_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            donation.dedupe_key,
                            donation.platform,
                            donation.kind,
                            donation.external_id,
                            donation.channel,
                            donation.author_name,
                            donation.author_id,
                            donation.message,
                            donation.amount_value,
                            donation.amount_micros,
                            donation.currency,
                            created,
                            received,
                            json.dumps(donation.raw, separators=(",", ":"), default=str),
                        ),
                    )
                    inserted += int(self._conn.execute("SELECT changes()").fetchone()[0])
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        self._tighten()
        return inserted

    def clear_donations(self) -> int:
        """Delete every donation. Settings and the ingest log stay."""
        with self._lock:
            count = int(self._conn.execute("SELECT COUNT(*) AS n FROM donations").fetchone()["n"] or 0)
            self._conn.execute("DELETE FROM donations")
            try:
                self._conn.execute("DELETE FROM sqlite_sequence WHERE name = 'donations'")
            except sqlite3.OperationalError:
                pass
            self._conn.commit()
        self._tighten()
        return count

    def list_donations(
        self,
        *,
        platform: str = "",
        kind: str = "",
        processed: str = "",
        query: str = "",
        exclude_kinds: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
        membership_price: float | None = None,
        rates: dict[str, float] | None = None,
    ) -> tuple[list[dict], int]:
        where, params = self._filters(
            platform=platform,
            kind=kind,
            processed=processed,
            query=query,
            exclude_kinds=exclude_kinds or [],
        )
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM donations {where}", params
            ).fetchone()["n"]
            rows = self._conn.execute(
                f"""
                SELECT * FROM donations {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
        return [self._present(row, membership_price, rates) for row in rows], int(total)

    def get_donation(
        self,
        donation_id: int,
        membership_price: float | None = None,
        rates: dict[str, float] | None = None,
    ) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM donations WHERE id = ?", (donation_id,)
            ).fetchone()
        if row is None:
            return None
        return self._present(row, membership_price, rates, include_raw=True)

    def update_donation(
        self,
        donation_id: int,
        *,
        processed: bool | None = None,
        note: str | None = None,
    ) -> dict | None:
        current = self.get_donation(donation_id)
        if current is None:
            return None
        next_processed = current["processed"] if processed is None else bool(processed)
        next_note = current["note"] if note is None else note
        processed_at = current["processed_at"]
        if processed is not None:
            processed_at = utc_now_iso() if next_processed else None
        with self._lock:
            self._conn.execute(
                """
                UPDATE donations
                SET processed = ?, processed_at = ?, note = ?
                WHERE id = ?
                """,
                (1 if next_processed else 0, processed_at, next_note, donation_id),
            )
            self._conn.commit()
        return self.get_donation(donation_id)

    def update_many(self, ids: list[int], *, processed: bool) -> int:
        if not ids:
            return 0
        moment = utc_now_iso() if processed else None
        changed = 0
        with self._lock:
            for donation_id in ids:
                cursor = self._conn.execute(
                    """
                    UPDATE donations
                    SET processed = ?, processed_at = ?
                    WHERE id = ?
                    """,
                    (1 if processed else 0, moment, int(donation_id)),
                )
                changed += cursor.rowcount
            self._conn.commit()
        return changed

    def summary(self) -> dict:
        with self._lock:
            totals = self._conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN processed = 0 THEN 1 ELSE 0 END) AS unprocessed
                FROM donations
                """
            ).fetchone()
            by_platform = self._conn.execute(
                """
                SELECT platform, COUNT(*) AS count,
                       SUM(CASE WHEN processed = 0 THEN 1 ELSE 0 END) AS unprocessed
                FROM donations
                GROUP BY platform
                ORDER BY platform
                """
            ).fetchall()
            by_currency = self._conn.execute(
                """
                SELECT COALESCE(currency, '') AS currency,
                       COUNT(*) AS count,
                       ROUND(SUM(amount_value), 6) AS amount
                FROM donations
                WHERE amount_value IS NOT NULL
                GROUP BY currency
                ORDER BY currency
                """
            ).fetchall()
        return {
            "total": int(totals["total"] or 0),
            "unprocessed": int(totals["unprocessed"] or 0),
            "by_platform": [
                {
                    "platform": row["platform"],
                    "label": PLATFORM_LABELS.get(row["platform"], row["platform"]),
                    "count": int(row["count"]),
                    "unprocessed": int(row["unprocessed"] or 0),
                }
                for row in by_platform
            ],
            "by_currency": [
                {
                    "currency": row["currency"] or None,
                    "count": int(row["count"]),
                    "amount": row["amount"],
                }
                for row in by_currency
            ],
        }

    def export_rows(self, **filters: object) -> list[dict]:
        rows, _total = self.list_donations(limit=500, offset=0, **filters)  # type: ignore[arg-type]
        # Page through so an export is not capped at one page.
        exported = list(rows)
        offset = 500
        total = _total
        while offset < total:
            page, total = self.list_donations(limit=500, offset=offset, **filters)  # type: ignore[arg-type]
            if not page:
                break
            exported.extend(page)
            offset += 500
        full = []
        for row in exported:
            detailed = self.get_donation(int(row["id"]))
            if detailed is not None:
                full.append(detailed)
        return full

    def total_donated(
        self,
        rates: dict[str, float],
        membership_price: float | None,
        **filters: object,
    ) -> float:
        """USD sum. Individual gift receipts are not extra money, so they are omitted."""
        excluded = list(filters.get("exclude_kinds") or [])  # type: ignore[arg-type]
        if "gift_membership_received" not in excluded:
            excluded.append("gift_membership_received")
        filters = {**filters, "exclude_kinds": excluded}
        total = 0.0
        for row in self._money_rows(**filters):  # type: ignore[arg-type]
            priced = quote(row, membership_price, rates)
            if priced.usd is not None:
                total += priced.usd
        return round(total, 2)

    def export_csv(
        self,
        *,
        rates: dict[str, float] | None = None,
        membership_price: float | None = None,
        zone: object = None,
        **filters: object,
    ) -> str:
        from .money import local_date_time

        table = {"USD": 1.0, **(rates or {})}
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in self._money_rows(**filters):  # type: ignore[arg-type]
            priced = quote(row, membership_price, table)
            date, clock = local_date_time(str(row.get("created_at") or ""), zone)  # type: ignore[arg-type]
            writer.writerow(
                {
                    "Date": date,
                    "Time": clock,
                    "Donor": csv_safe(row.get("author_name")),
                    "Amount": money_text(priced.amount),
                    "Platform": csv_safe(PLATFORM_LABELS.get(str(row.get("platform") or ""), row.get("platform"))),
                    "Type": priced.label,
                    "Currency": priced.currency or "",
                    "Amount USD": money_text(priced.usd),
                    "Message": csv_safe(row.get("message")),
                }
            )
        return buffer.getvalue()

    def export_jsonl(self, **filters: object) -> str:
        lines = []
        for row in self.export_rows(**filters):
            lines.append(json.dumps(row, ensure_ascii=False, default=str))
        return "\n".join(lines) + ("\n" if lines else "")

    def set_status(self, platform: str, state: str, detail: str, error: str = "") -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO platform_status (platform, state, detail, error, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(platform) DO UPDATE SET
                    state = excluded.state,
                    detail = excluded.detail,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (platform, state, detail, error, utc_now_iso()),
            )
            self._conn.commit()

    def status(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM platform_status ORDER BY platform"
            ).fetchall()
        return {
            row["platform"]: {
                "state": row["state"],
                "detail": row["detail"],
                "error": row["error"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        }

    def log(self, platform: str, level: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO ingest_log (at, platform, level, message) VALUES (?, ?, ?, ?)",
                (utc_now_iso(), platform, level, message[:800]),
            )
            self._conn.execute(
                """
                DELETE FROM ingest_log
                WHERE id NOT IN (SELECT id FROM ingest_log ORDER BY id DESC LIMIT 400)
                """
            )
            self._conn.commit()

    def recent_log(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT at, platform, level, message FROM ingest_log ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _filters(
        self,
        *,
        platform: str,
        kind: str,
        processed: str,
        query: str,
        exclude_kinds: list[str],
    ) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        if platform:
            clauses.append("platform = ?")
            params.append(platform)
        if kind in DISPLAY_KIND_SQL:
            clauses.append(DISPLAY_KIND_SQL[kind])
        elif kind:
            clauses.append("kind = ?")
            params.append(kind)
        if processed in {"0", "1"}:
            clauses.append("processed = ?")
            params.append(int(processed))
        for excluded in exclude_kinds:
            if excluded:
                clauses.append("kind != ?")
                params.append(excluded)
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            clauses.append(
                "(author_name LIKE ? ESCAPE '\\' OR message LIKE ? ESCAPE '\\' OR channel LIKE ? ESCAPE '\\' OR note LIKE ? ESCAPE '\\')"
            )
            params.extend([like, like, like, like])
        if not clauses:
            return "", params
        return "WHERE " + " AND ".join(clauses), params

    def _money_rows(self, **filters: object) -> list[dict]:
        where, params = self._filters(
            platform=str(filters.get("platform") or ""),
            kind=str(filters.get("kind") or ""),
            processed=str(filters.get("processed") or ""),
            query=str(filters.get("query") or ""),
            exclude_kinds=list(filters.get("exclude_kinds") or []),  # type: ignore[arg-type]
        )
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM donations {where}
                ORDER BY created_at ASC, id ASC
                """,
                params,
            ).fetchall()
        exported = []
        for row in rows:
            item = self._public(row, include_raw=True)
            exported.append(item)
        return exported

    def _present(
        self,
        row: sqlite3.Row,
        membership_price: float | None,
        rates: dict[str, float] | None,
        include_raw: bool = False,
    ) -> dict:
        payload = self._public(row, include_raw=include_raw)
        if rates is None and membership_price is None:
            return payload
        source = dict(payload)
        if not isinstance(source.get("raw"), dict):
            source["raw"] = _raw_object(row["raw_json"])
        priced = quote(source, membership_price, {"USD": 1.0, **(rates or {})})
        payload["kind_label"] = priced.label
        payload["quoted_amount"] = priced.amount
        payload["quoted_currency"] = priced.currency
        payload["amount_usd"] = priced.usd
        return payload

    def _public(self, row: sqlite3.Row, include_raw: bool = False) -> dict:
        payload = {
            "id": row["id"],
            "dedupe_key": row["dedupe_key"],
            "platform": row["platform"],
            "platform_label": PLATFORM_LABELS.get(row["platform"], row["platform"]),
            "kind": row["kind"],
            "kind_label": KIND_LABELS.get(row["kind"], row["kind"]),
            "external_id": row["external_id"],
            "channel": row["channel"],
            "author_name": row["author_name"],
            "author_id": row["author_id"],
            "message": row["message"],
            "amount_value": row["amount_value"],
            "amount_micros": row["amount_micros"],
            "currency": row["currency"],
            "created_at": row["created_at"],
            "received_at": row["received_at"],
            "processed": bool(row["processed"]),
            "processed_at": row["processed_at"],
            "note": row["note"],
        }
        if include_raw:
            payload["raw"] = _raw_object(row["raw_json"])
        return payload


def _raw_object(raw_json: object) -> dict:
    try:
        parsed = json.loads(str(raw_json or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
