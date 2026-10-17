"""SQLite ledger. The database file is the thing the user processes."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import threading
import uuid
from pathlib import Path

from .goals import (
    donor_bar_line,
    donor_leaderboard,
    format_usd,
    load_tiers,
    round2,
    running_usd,
    stream_chat_line,
    tier_breaches,
    type_breakdown,
)
from .currencies import SUPPORTED_CURRENCY_CODES
from .kinds import KIND_LABELS, PLATFORM_LABELS
from .money import CSV_FIELDS, DISPLAY_KIND_SQL, money_text, parse_membership_price, quote
from .search import matches_ledger_query
from .normalize import Donation
from .payload_log import payload_message
from .util import csv_safe, display_donor, to_utc_iso, utc_now_iso

# Globals mirror the open ledger. Secrets and streamer presets stay global.
# Other ledgers live in the streams document.
_STREAMS_KEY = "streams"
_STREAM_FIELDS = (
    "stream_title",
    "membership_price",
    "tiers_enabled",
    "show_top_donors",
    "chat_extra",
    "goal_tiers",
    "stream_started_at",
    "youtube_watch",
    "youtube_channel",
    "youtube_follow_live",
    "rumble_watch",
    "rumble_channel",
    "rumble_follow_live",
    "kick_watch",
    "kick_follow_live",
    "collection_paused",
    "blue_script_id",
    "blue_started_at",
    "blue_timer_minute",
    "blue_goal_tier",
)
_BLUE_FIELDS = ("blue_script_id", "blue_started_at", "blue_timer_minute", "blue_goal_tier")
# A new ledger copies presentation only. Title, donations, addresses, follow-live,
# goal tiers, and the pause are not copied. A new ledger starts paused, with tiers off.
_COPIED_ON_NEW_LEDGER = (
    "membership_price",
    "show_top_donors",
    "chat_extra",
)
_WATCH_FIELDS = ("youtube_watch", "rumble_watch", "kick_watch")
_CHANNEL_FIELDS = ("youtube_channel", "rumble_channel")
_FOLLOW_FIELDS = ("youtube_follow_live", "rumble_follow_live", "kick_follow_live")
_STREAM_ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
LOG_NAME = "stream-donations.log"
_LOG_KEEP = 400
_LOG_PAGE = 100
_MANUAL_DEDUPE = re.compile(r"manual:[0-9a-f]{16,64}\Z")

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
    raw_json TEXT NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0,
    stream_id TEXT NOT NULL DEFAULT ''
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
        self._log_file_lock = threading.Lock()
        self._notifier = None
        self.notices = None
        self.on_fresh_donations = None
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._ensure_hidden_column()
        self._ensure_stream_column()
        self._conn.commit()
        self._backfill_stream_ids()
        self._tighten()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _ensure_hidden_column(self) -> None:
        # Ledgers created before hide support have no hidden column.
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(donations)")}
        if "hidden" not in columns:
            self._conn.execute("ALTER TABLE donations ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")

    def _ensure_stream_column(self) -> None:
        # The index is created after the column so an older file can be opened.
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(donations)")}
        if "stream_id" not in columns:
            self._conn.execute("ALTER TABLE donations ADD COLUMN stream_id TEXT NOT NULL DEFAULT ''")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_donations_stream ON donations (stream_id, created_at)"
        )

    def _backfill_stream_ids(self) -> None:
        """Rows from before ledgers were split belong to the first ledger."""
        blank = self._conn.execute(
            "SELECT 1 FROM donations WHERE stream_id = '' LIMIT 1"
        ).fetchone()
        if blank is None:
            return
        first = self._stream_doc()["tabs"][0]["id"]
        self._conn.execute(
            "UPDATE donations SET stream_id = ? WHERE stream_id = ''",
            (first,),
        )
        self._conn.commit()

    def _active_stream_id(self) -> str:
        # Capture this before taking _lock. _stream_doc takes the lock itself.
        return str(self._stream_doc()["active_id"])

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

    def collection_paused(self) -> bool:
        # Absent is not paused. Callers that never open a ledger keep collecting.
        # stream_tabs() writes "1" when the open ledger has no explicit flag.
        return self.get_setting("collection_paused") == "1"

    def set_setting(self, key: str, value: str) -> None:
        self._put_setting(key, value)
        if key in _STREAM_FIELDS:
            self._mirror_stream_field(key, value)

    def _put_setting(self, key: str, value: str) -> None:
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
        self._drop_setting(key)
        if key in _STREAM_FIELDS:
            self._mirror_stream_field(key, "")

    def _drop_setting(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            self._conn.commit()

    def add_many(self, donations: list[Donation], stream_id: str | None = None) -> int:
        if not donations:
            return 0
        received = utc_now_iso()
        stream_id = self._active_stream_id() if stream_id is None else stream_id
        inserted = 0
        fresh: list[Donation] = []
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
                            amount_micros, currency, created_at, received_at, raw_json,
                            stream_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            stream_id,
                        ),
                    )
                    changed = int(self._conn.execute("SELECT changes()").fetchone()[0])
                    inserted += changed
                    if changed:
                        fresh.append(donation)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        self._tighten()
        self._log_new_payloads(fresh)
        self._notify_automated(fresh)
        callback = self.on_fresh_donations
        if fresh and callback is not None:
            try:
                callback(stream_id, fresh)
            except Exception:
                self.log("blue", "error", "BLUE could not read a new donation.")
        return inserted

    def enable_system_notifications(self) -> None:
        """Tell the desktop when an automated row is stored. Manual rows stay quiet."""
        from .notify import NoticeFeed, system_notifier

        self._notifier = system_notifier(log=lambda message: self.log("ledger", "info", message))
        self.notices = NoticeFeed()

    def system_notifications_available(self) -> bool:
        """True when this computer, or the desktop shell, shows the notices itself."""
        from .notify import UnavailableNotifier

        return self._notifier is not None and not isinstance(self._notifier, UnavailableNotifier)

    def _notify_automated(self, donations: list[Donation]) -> None:
        notifier = self._notifier
        if notifier is None or not donations:
            return
        automated = [item for item in donations if not str(item.dedupe_key).startswith("manual:")]
        if not automated:
            return
        try:
            price = parse_membership_price(self.get_setting("membership_price"))
            notifier.donations_added(automated, membership_price=price)
        except Exception:
            return
        feed = self.notices
        if feed is not None:
            try:
                feed.donations_added(automated, membership_price=price)
            except Exception:
                return

    def add_manual_entry(
        self,
        *,
        author_name: object,
        platform: object,
        kind: object,
        amount: object,
        currency: object = "USD",
        message: object = "",
        created_at: object = "",
    ) -> int:
        """Store one hand-entered gift. Amount is in currency, which defaults to USD."""
        fields = _checked_manual_fields(
            author_name=author_name,
            platform=platform,
            kind=kind,
            amount=amount,
            currency=currency,
            message=message,
            created_at=created_at,
        )
        token = uuid.uuid4().hex
        donation = Donation(
            dedupe_key=f"manual:{token}",
            platform=fields["platform"],
            kind=fields["kind"],
            external_id=token,
            channel="",
            author_name=fields["author"],
            author_id="",
            message=fields["message"],
            amount_value=fields["amount"],
            amount_micros=int(round(fields["amount"] * 1_000_000)),
            currency=fields["currency"],
            created_at=fields["created_at"],
            raw={"manual": True},
        )
        if self.add_many([donation]) != 1:
            raise ValueError("Could not store that entry.")
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM donations WHERE dedupe_key = ?", (donation.dedupe_key,)
            ).fetchone()
        self.log(
            "ledger",
            "info",
            f"Manual {fields['kind']} from {fields['author']} for {fields['amount']:.2f} {fields['currency']}.",
        )
        return int(row["id"])

    def manual_duplicate(self, fields: dict, ignore_id: int | None = None) -> dict | None:
        """The first open-ledger row that is the same gift as a hand-entered one.

        Same donor, platform, type, amount, currency, and message. Hidden rows
        still count. Another ledger does not. Automated collection does not call this.
        ignore_id skips the row being edited, so saving it unchanged is not a copy.
        """
        author = display_donor(fields.get("author")).casefold()
        platform = str(fields.get("platform") or "")
        kind = str(fields.get("kind") or "")
        currency = str(fields.get("currency") or "").upper()
        message = " ".join(str(fields.get("message") or "").split()).casefold()
        try:
            amount = round(float(fields["amount"]), 2)
        except (TypeError, ValueError, KeyError):
            return None
        skipped = None if ignore_id is None else int(ignore_id)
        stream_id = self._active_stream_id()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, author_name, platform, kind, message, amount_value, currency
                FROM donations
                WHERE stream_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (stream_id,),
            ).fetchall()
        for row in rows:
            if skipped is not None and int(row["id"]) == skipped:
                continue
            if display_donor(row["author_name"]).casefold() != author:
                continue
            if str(row["platform"] or "") != platform or str(row["kind"] or "") != kind:
                continue
            if str(row["currency"] or "").upper() != currency:
                continue
            if row["amount_value"] is None:
                continue
            if round(float(row["amount_value"]), 2) != amount:
                continue
            stored = " ".join(str(row["message"] or "").split()).casefold()
            if stored != message:
                continue
            return {
                "id": int(row["id"]),
                "author_name": display_donor(row["author_name"]) or author,
                "amount": amount,
                "currency": currency,
            }
        return None

    def set_stream_started(self, when: str | None) -> None:
        if when:
            self.set_setting("stream_started_at", when)
        else:
            self.delete_setting("stream_started_at")

    def ledger_snapshots(self) -> list[dict]:
        """Each ledger's own addresses and pause. The open ledger is unchanged."""
        doc = self._stream_doc()
        snapshots = []
        for tab in doc["tabs"]:
            item = {"id": tab["id"]}
            for key in _STREAM_FIELDS:
                item[key] = str(tab.get(key) or "")
            snapshots.append(item)
        return snapshots

    def stream_tabs(self) -> dict:
        """Id, label, and pause for each ledger. A blank name is shown as Stream.

        The open ledger's pause is mirrored onto the global flag when that flag
        is still unset. Collectors read the global. "1" is paused, "0" is collecting.
        """
        doc = self._stream_doc()
        self._ensure_open_pause_default(doc)
        return {
            "active_id": doc["active_id"],
            "tabs": [
                {
                    "id": tab["id"],
                    "title": (tab.get("stream_title") or "").strip() or "Stream",
                    "paused": tab.get("collection_paused") != "0",
                }
                for tab in doc["tabs"]
            ],
        }

    def set_stream_paused(self, tab_id: str | None, paused: bool) -> str:
        """Pause or reconnect one ledger. Only the open ledger changes the global flag.

        "0" means collecting. Deleting the key would be read back as paused.
        """
        doc = self._stream_doc()
        wanted = tab_id.strip() if isinstance(tab_id, str) else ""
        wanted = wanted or doc["active_id"]
        tab = next((item for item in doc["tabs"] if item["id"] == wanted), None)
        if tab is None:
            raise ValueError("Unknown stream tab.")
        tab["collection_paused"] = "1" if paused else "0"
        self._save_stream_doc(doc)
        if wanted == doc["active_id"]:
            self._put_setting("collection_paused", tab["collection_paused"])
        return wanted

    def add_stream(self) -> str:
        """Open an empty ledger. Presentation is copied; title, addresses, and goals are not."""
        doc = self._stream_doc()
        current = next(tab for tab in doc["tabs"] if tab["id"] == doc["active_id"])
        tab = {key: "" for key in _STREAM_FIELDS}
        for key in _COPIED_ON_NEW_LEDGER:
            tab[key] = current.get(key, "")
        tab["id"] = uuid.uuid4().hex
        tab["stream_title"] = ""
        tab["stream_started_at"] = utc_now_iso()
        tab["collection_paused"] = "1"
        tab["tiers_enabled"] = "0"
        tab["goal_tiers"] = ""
        doc["tabs"].append(tab)
        self._activate_stream(doc, tab["id"])
        return tab["id"]

    def switch_stream(self, tab_id: str) -> None:
        doc = self._stream_doc()
        tab_id = str(tab_id or "").strip()
        if tab_id == doc["active_id"]:
            return
        if not any(tab["id"] == tab_id for tab in doc["tabs"]):
            raise ValueError("Unknown stream tab.")
        self._activate_stream(doc, tab_id)

    def close_stream(self, tab_id: str) -> None:
        doc = self._stream_doc()
        tab_id = str(tab_id or "").strip()
        index = next((i for i, tab in enumerate(doc["tabs"]) if tab["id"] == tab_id), -1)
        if index < 0:
            raise ValueError("Unknown stream tab.")
        # The last ledger is replaced by a blank one, the same shape as +.
        # The address dialog is a separate step and is not opened here.
        if len(doc["tabs"]) == 1:
            self.add_stream()
            self.log("ledger", "info", "Opened a blank ledger.")
            doc = self._stream_doc()
            index = next(i for i, tab in enumerate(doc["tabs"]) if tab["id"] == tab_id)
        was_active = doc["active_id"] == tab_id
        del doc["tabs"][index]
        if was_active:
            neighbor = doc["tabs"][min(index, len(doc["tabs"]) - 1)]
            self._activate_stream(doc, neighbor["id"])
        else:
            self._save_stream_doc(doc)
        self._delete_stream_rows(tab_id)

    def archive_open_ledger(self) -> dict:
        """Copy the open ledger into archive.db, then close it.

        Hidden rows are included. Other ledgers stay. Closing the last ledger
        leaves a new blank one.
        """
        from .archive import ARCHIVE_NAME, write_archived_ledger

        doc = self._stream_doc()
        tab_id = str(doc["active_id"])
        tab = next(item for item in doc["tabs"] if item["id"] == tab_id)
        saved = {key: str(tab.get(key) or "") for key in _STREAM_FIELDS}
        saved["id"] = tab_id
        saved["archived_at"] = utc_now_iso()
        with self._lock:
            fetched = self._conn.execute(
                "SELECT * FROM donations WHERE stream_id = ? ORDER BY id ASC",
                (tab_id,),
            ).fetchall()
            rows = [dict(row) for row in fetched]
        count = write_archived_ledger(self.path.parent / ARCHIVE_NAME, _SCHEMA, saved, rows)
        self.close_stream(tab_id)
        noun = "donation" if count == 1 else "donations"
        self.log("ledger", "info", f"Saved {count} {noun} to the archive and closed that ledger.")
        return {"count": count, "name": ARCHIVE_NAME}

    def rename_stream(self, title: str) -> None:
        text = str(title or "").strip()
        if len(text) > 80:
            raise ValueError("Stream name is limited to 80 characters.")
        self.set_setting("stream_title", text)

    def streamer_presets(self) -> list[dict]:
        # presets imports the channel validators, and those modules import Store.
        from .presets import PRESETS_KEY, parse_presets, public_presets

        return public_presets(parse_presets(self.get_setting(PRESETS_KEY)))

    def save_streamer_preset(
        self,
        name,
        youtube_channel,
        rumble_channel,
        kick_watch,
        tiers_enabled=False,
        goal_tiers=None,
        script_id="",
    ) -> dict:
        """Store a shared preset. The open ledger's addresses and goals stay as they are."""
        from .presets import PRESETS_KEY, dump_presets, parse_presets, upsert_preset

        chosen = str(script_id or "").strip()
        if chosen and not any(script["id"] == chosen for script in self.blue_scripts()):
            raise ValueError("That BLUE script is not saved.")
        presets = parse_presets(self.get_setting(PRESETS_KEY))
        presets, saved = upsert_preset(
            presets,
            name,
            youtube_channel,
            rumble_channel,
            kick_watch,
            tiers_enabled,
            goal_tiers,
            chosen,
        )
        self.set_setting(PRESETS_KEY, dump_presets(presets))
        return saved

    def delete_streamer_preset(self, preset_id: str) -> str:
        from .presets import PRESETS_KEY, dump_presets, parse_presets, remove_preset

        presets = parse_presets(self.get_setting(PRESETS_KEY))
        presets, removed = remove_preset(presets, preset_id)
        if presets:
            self.set_setting(PRESETS_KEY, dump_presets(presets))
        else:
            self.delete_setting(PRESETS_KEY)
        return removed["name"]

    def blue_scripts(self) -> list[dict]:
        from .blue_scripts import SCRIPTS_KEY, parse_scripts, public_scripts

        return public_scripts(parse_scripts(self.get_setting(SCRIPTS_KEY)))

    def save_blue_script(self, name, source) -> dict:
        """Store a shared script. The open ledger keeps the script it already has."""
        from .blue_scripts import SCRIPTS_KEY, dump_scripts, parse_scripts, upsert_script

        scripts = parse_scripts(self.get_setting(SCRIPTS_KEY))
        scripts, saved = upsert_script(scripts, name, source)
        self.set_setting(SCRIPTS_KEY, dump_scripts(scripts))
        return saved

    def delete_blue_script(self, script_id: str) -> str:
        """Remove a script and clear it from presets and ledgers. Channels stay."""
        from .blue_scripts import SCRIPTS_KEY, dump_scripts, parse_scripts, remove_script
        from .presets import PRESETS_KEY, dump_presets, parse_presets

        scripts = parse_scripts(self.get_setting(SCRIPTS_KEY))
        scripts, removed = remove_script(scripts, script_id)
        if scripts:
            self.set_setting(SCRIPTS_KEY, dump_scripts(scripts))
        else:
            self.delete_setting(SCRIPTS_KEY)
        presets = parse_presets(self.get_setting(PRESETS_KEY))
        if any(preset.get("script_id") == removed["id"] for preset in presets):
            for preset in presets:
                if preset.get("script_id") == removed["id"]:
                    preset["script_id"] = ""
            self.set_setting(PRESETS_KEY, dump_presets(presets))
        doc = self._stream_doc()
        changed = False
        for tab in doc["tabs"]:
            if tab.get("blue_script_id") != removed["id"]:
                continue
            tab["blue_script_id"] = ""
            changed = True
            if tab["id"] == doc["active_id"]:
                self._drop_setting("blue_script_id")
        if changed:
            self._save_stream_doc(doc)
        return removed["name"]

    def set_stream_values(self, tab_id: str, values: dict) -> None:
        """Write BLUE fields on one ledger. Other ledgers stay as they are."""
        cleaned: dict[str, str] = {}
        for key, value in dict(values or {}).items():
            if key not in _BLUE_FIELDS:
                raise ValueError("Unknown ledger field.")
            cleaned[key] = str(value or "")
        if not cleaned:
            return
        doc = self._stream_doc()
        tab = next((item for item in doc["tabs"] if item["id"] == tab_id), None)
        if tab is None:
            return
        for key, value in cleaned.items():
            tab[key] = value
        self._save_stream_doc(doc)
        if tab_id != doc["active_id"]:
            return
        for key, value in cleaned.items():
            if value:
                self._put_setting(key, value)
            else:
                self._drop_setting(key)

    def _stream_doc(self) -> dict:
        raw = self.get_setting(_STREAMS_KEY)
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            doc = self._valid_stream_doc(parsed)
            if doc is not None:
                watches_absent = self._watch_keys_absent(parsed)
                if watches_absent:
                    self._keep_global_watches(doc)
                if watches_absent or self._pause_missing(parsed):
                    # Mirror the open ledger, including its pause, after a backfill.
                    self._activate_stream(doc, doc["active_id"])
                return doc
        tab = self._tab_from_globals()
        doc = {"active_id": tab["id"], "tabs": [tab]}
        self._save_stream_doc(doc)
        return doc

    def _valid_stream_doc(self, parsed: object) -> dict | None:
        if not isinstance(parsed, dict):
            return None
        raw_tabs = parsed.get("tabs")
        if not isinstance(raw_tabs, list) or not raw_tabs:
            return None
        tabs: list[dict] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_tabs):
            tab = self._normalize_stream_tab(raw, f"stream-{index + 1}")
            if tab is None or tab["id"] in seen:
                continue
            seen.add(tab["id"])
            tabs.append(tab)
        if not tabs:
            return None
        active = str(parsed.get("active_id") or "")
        if active not in seen:
            active = tabs[0]["id"]
        return {"active_id": active, "tabs": tabs}

    def _normalize_stream_tab(self, raw: object, fallback_id: str) -> dict | None:
        if not isinstance(raw, dict):
            return None
        tab_id = str(raw.get("id") or "").strip() or fallback_id
        if not tab_id or len(tab_id) > 64 or any(char not in _STREAM_ID_CHARS for char in tab_id):
            return None
        tab = {"id": tab_id}
        for key in _STREAM_FIELDS:
            value = raw.get(key, "")
            tab[key] = value if isinstance(value, str) else ""
        tab["stream_title"] = tab["stream_title"].strip()[:80]
        tab["chat_extra"] = tab["chat_extra"][:500]
        for key in _WATCH_FIELDS:
            tab[key] = tab[key].strip()[:2000]
        for key in _CHANNEL_FIELDS:
            tab[key] = tab[key].strip()[:2000]
        for key in _FOLLOW_FIELDS:
            tab[key] = "1" if tab[key] == "1" else "0"
        if tab["collection_paused"] not in {"0", "1"}:
            tab["collection_paused"] = "1"
        if tab["tiers_enabled"] not in {"0", "1"}:
            tab["tiers_enabled"] = "0"
        if tab["show_top_donors"] not in {"0", "1"}:
            tab["show_top_donors"] = "1"
        tab["blue_script_id"] = tab["blue_script_id"].strip()[:64]
        tab["blue_started_at"] = tab["blue_started_at"].strip()[:40]
        minute = tab["blue_timer_minute"].strip()
        tab["blue_timer_minute"] = minute if minute.isdigit() and len(minute) <= 8 else ""
        goal_tier = tab["blue_goal_tier"].strip()
        tab["blue_goal_tier"] = goal_tier if goal_tier in {"0", "1", "2", "3", "4", "5"} else ""
        return tab

    def _tab_from_globals(self) -> dict:
        values = {key: self.get_setting(key) for key in _STREAM_FIELDS}
        if values["show_top_donors"] not in {"0", "1"}:
            values["show_top_donors"] = "1"
        if values["tiers_enabled"] not in {"0", "1"}:
            values["tiers_enabled"] = "0"
        return self._normalize_stream_tab({**values, "id": "stream-1"}, "stream-1") or {
            "id": "stream-1",
            **{key: "" for key in _STREAM_FIELDS},
            "show_top_donors": "1",
            "tiers_enabled": "0",
        }

    def _pause_missing(self, parsed: object) -> bool:
        """Older ledgers had one app-wide pause. A missing flag means paused."""
        if not isinstance(parsed, dict):
            return False
        raw_tabs = parsed.get("tabs")
        if not isinstance(raw_tabs, list):
            return False
        for raw in raw_tabs:
            if isinstance(raw, dict) and raw.get("collection_paused") not in {"0", "1"}:
                return True
        return False

    def _ensure_open_pause_default(self, doc: dict) -> None:
        """Write the open ledger's pause when the global flag is still unset.

        Does not replace an explicit "0" or "1". A reconnect stores "0"; deleting
        the key would be normalized back to paused on the next read.
        """
        active = next(tab for tab in doc["tabs"] if tab["id"] == doc["active_id"])
        flag = "0" if active.get("collection_paused") == "0" else "1"
        if active.get("collection_paused") != flag:
            active["collection_paused"] = flag
            self._save_stream_doc(doc)
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("collection_paused",),
            ).fetchone()
            if row is not None and str(row["value"]) in {"0", "1"}:
                return
            self._conn.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("collection_paused", flag),
            )
            self._conn.commit()
        self._tighten()

    def _watch_keys_absent(self, parsed: object) -> bool:
        """Older tab documents stored watch URLs only as globals."""
        if not isinstance(parsed, dict):
            return False
        raw_tabs = parsed.get("tabs")
        if not isinstance(raw_tabs, list):
            return False
        for raw in raw_tabs:
            if isinstance(raw, dict) and any(key in raw for key in _WATCH_FIELDS):
                return False
        return True

    def _keep_global_watches(self, doc: dict) -> None:
        # Older ledgers shared one address. Each keeps its own copy of that address.
        values = {key: self.get_setting(key).strip()[:2000] for key in _WATCH_FIELDS}
        for tab in doc["tabs"]:
            for key in _WATCH_FIELDS:
                tab[key] = values[key]

    def _save_stream_doc(self, doc: dict) -> None:
        self._put_setting(_STREAMS_KEY, json.dumps(doc, separators=(",", ":")))

    def _delete_stream_rows(self, tab_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM donations WHERE stream_id = ?", (tab_id,))
            self._conn.commit()
        self._tighten()

    def _activate_stream(self, doc: dict, tab_id: str) -> None:
        tab = next(item for item in doc["tabs"] if item["id"] == tab_id)
        if tab.get("collection_paused") != "0":
            tab["collection_paused"] = "1"
        doc["active_id"] = tab_id
        # Pause before the address swap so a live chat cannot attach to the other ledger.
        self._put_setting("collection_paused", "1")
        self._save_stream_doc(doc)
        for key in _STREAM_FIELDS:
            if key == "collection_paused":
                continue
            value = str(tab.get(key) or "")
            if value:
                self._put_setting(key, value)
            else:
                self._drop_setting(key)
        self._put_setting("collection_paused", tab["collection_paused"])
        self._save_stream_doc(doc)

    def _mirror_stream_field(self, key: str, value: str) -> None:
        doc = self._stream_doc()
        for tab in doc["tabs"]:
            if tab["id"] != doc["active_id"]:
                continue
            if key == "stream_title":
                value = value.strip()[:80]
            tab[key] = value
            break
        else:
            return
        self._save_stream_doc(doc)

    def stream_report(
        self,
        rates: dict[str, float],
        membership_price: float | None,
    ) -> dict:
        started = self.get_setting("stream_started_at")
        tiers = load_tiers(self.get_setting("goal_tiers"))
        enabled = self.get_setting("tiers_enabled") == "1"
        extra = self.get_setting("chat_extra")
        show = self.get_setting("show_top_donors", "1") != "0"
        # The banner, goals, and top donors always cover this ledger. A saved
        # start time is not a window.
        rows = self._stream_rows("", membership_price, rates)
        total = running_usd(rows)
        donors = donor_leaderboard(rows)
        thanks = tier_breaches(rows, enabled, tiers)
        last = rows[-1] if rows else None
        doc = self._stream_doc()
        active = next(tab for tab in doc["tabs"] if tab["id"] == doc["active_id"])
        return {
            "total_usd": total,
            "count": len(rows),
            "by_type": type_breakdown(rows),
            "chat_line": stream_chat_line(total, enabled=enabled, tiers=tiers, extra=extra),
            "started_at": started or None,
            "title": active.get("stream_title") or "",
            "streams": self.stream_tabs(),
            "scope": "ledger",
            "show_top_donors": show,
            "tiers_enabled": enabled,
            "chat_extra": extra,
            "tiers": [tier.as_dict() for tier in tiers],
            "donors": donors,
            "donor_bar": donor_bar_line(donors["rows"]),
            "donor_bar_copy": donor_bar_line(donors["rows"], mentions=True),
            "thanks": thanks,
            "last": _last_line(last),
        }

    def stream_total_usd(self, stream_id: str, rates: dict[str, float]) -> float:
        """This ledger's USD total. Rates already saved are used, and nothing is fetched."""
        rows = self._goal_rows(stream_id, rates)
        return running_usd(rows)

    def stream_goal_amounts(
        self,
        stream_id: str,
        rates: dict[str, float],
    ) -> list[tuple[str, float | None, str, str]]:
        """(dedupe key, USD or none, donor, platform) for rows in this ledger's total, oldest first."""
        found = []
        for row in self._goal_rows(stream_id, rates):
            usd = row.get("amount_usd")
            found.append((
                str(row.get("dedupe_key") or ""),
                None if usd is None else float(usd),
                display_donor(row.get("author_name")) or "",
                str(row.get("platform") or ""),
            ))
        return found

    def _goal_rows(self, stream_id: str, rates: dict[str, float]) -> list[dict]:
        tab = next((item for item in self.ledger_snapshots() if item["id"] == stream_id), None)
        if tab is None:
            return []
        return self._presented_stream_rows(
            stream_id,
            parse_membership_price(tab.get("membership_price")),
            rates,
            "",
        )

    def _stream_rows(
        self,
        started: str,
        membership_price: float | None,
        rates: dict[str, float],
    ) -> list[dict]:
        return self._presented_stream_rows(self._active_stream_id(), membership_price, rates, started)

    def _presented_stream_rows(
        self,
        stream_id: str,
        membership_price: float | None,
        rates: dict[str, float],
        started: str,
    ) -> list[dict]:
        clauses = ["kind != ?", "hidden = 0", "stream_id = ?"]
        params: list[object] = ["gift_membership_received", stream_id]
        if started:
            clauses.append("created_at >= ?")
            params.append(started)
        with self._lock:
            found = self._conn.execute(
                f"""
                SELECT * FROM donations
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at ASC, id ASC
                """,
                params,
            ).fetchall()
        return [self._present(row, membership_price, rates) for row in found]

    def clear_donations(self) -> int:
        """Delete every donation on the open ledger. Other ledgers, settings, and the log stay."""
        stream_id = self._active_stream_id()
        with self._lock:
            count = int(
                self._conn.execute(
                    "SELECT COUNT(*) AS n FROM donations WHERE stream_id = ?",
                    (stream_id,),
                ).fetchone()["n"]
                or 0
            )
            self._conn.execute("DELETE FROM donations WHERE stream_id = ?", (stream_id,))
            remaining = int(self._conn.execute("SELECT COUNT(*) AS n FROM donations").fetchone()["n"] or 0)
            if remaining == 0:
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
        include_hidden: bool = False,
        limit: int = 100,
        offset: int = 0,
        membership_price: float | None = None,
        rates: dict[str, float] | None = None,
    ) -> tuple[list[dict], int]:
        stream_id = self._active_stream_id()
        where, params = self._filters(
            platform=platform,
            kind=kind,
            processed=processed,
            exclude_kinds=exclude_kinds or [],
            include_hidden=include_hidden,
            stream_id=stream_id,
        )
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        with self._lock:
            # Running totals walk this ledger. A filter or page only chooses which rows come back.
            chrono = self._conn.execute(
                "SELECT * FROM donations WHERE stream_id = ? ORDER BY created_at ASC, id ASC",
                (stream_id,),
            ).fetchall()
            matched_ids = self._conn.execute(
                f"""
                SELECT id FROM donations {where}
                ORDER BY created_at DESC, id DESC
                """,
                params,
            ).fetchall()
        by_id = self._with_running_usd(chrono, membership_price, rates)
        matched = [
            by_id[int(item["id"])]
            for item in matched_ids
            if int(item["id"]) in by_id and matches_ledger_query(by_id[int(item["id"])], query)
        ]
        return matched[offset : offset + limit], len(matched)

    def get_donation(
        self,
        donation_id: int,
        membership_price: float | None = None,
        rates: dict[str, float] | None = None,
    ) -> dict | None:
        stream_id = self._active_stream_id()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM donations WHERE id = ? AND stream_id = ?",
                (donation_id, stream_id),
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
        stream_id = self._active_stream_id()
        with self._lock:
            self._conn.execute(
                """
                UPDATE donations
                SET processed = ?, processed_at = ?, note = ?
                WHERE id = ? AND stream_id = ?
                """,
                (1 if next_processed else 0, processed_at, next_note, donation_id, stream_id),
            )
            self._conn.commit()
        return self.get_donation(donation_id)

    def set_entry_hidden(self, donation_id: int, hidden: bool) -> dict | None:
        """Drop an automatic entry from totals and exports without deleting it."""
        current = self.get_donation(donation_id)
        if current is None:
            return None
        if current["manual"]:
            raise ValueError("A manual entry is deleted, not hidden.")
        stream_id = self._active_stream_id()
        with self._lock:
            self._conn.execute(
                "UPDATE donations SET hidden = ? WHERE id = ? AND stream_id = ?",
                (1 if hidden else 0, int(donation_id), stream_id),
            )
            self._conn.commit()
        if hidden:
            self.log("ledger", "info", "Hid an automatic ledger entry. Totals and exports leave it out.")
        else:
            self.log("ledger", "info", "An automatic ledger entry counts again.")
        return self.get_donation(donation_id)

    def update_manual_entry(
        self,
        donation_id: int,
        *,
        author_name: object,
        platform: object,
        kind: object,
        amount: object,
        currency: object = "USD",
        message: object = "",
        created_at: object = "",
    ) -> dict | None:
        """Replace the visible fields of one hand-entered row.

        The id and manual dedupe key stay. Note, processed, hidden, raw, and
        received_at stay. A blank time keeps the stored time. An automatic row
        cannot be edited.
        """
        current = self.get_donation(donation_id)
        if current is None:
            return None
        if not current["manual"]:
            raise ValueError("Automatic entries cannot be edited.")
        kept = created_at if str(created_at or "").strip() else current["created_at"]
        fields = _checked_manual_fields(
            author_name=author_name,
            platform=platform,
            kind=kind,
            amount=amount,
            currency=currency,
            message=message,
            created_at=kept,
        )
        stream_id = self._active_stream_id()
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE donations
                SET platform = ?, kind = ?, author_name = ?, message = ?,
                    amount_value = ?, amount_micros = ?, currency = ?, created_at = ?
                WHERE id = ? AND stream_id = ? AND dedupe_key = ?
                """,
                (
                    fields["platform"],
                    fields["kind"],
                    fields["author"],
                    fields["message"],
                    fields["amount"],
                    int(round(fields["amount"] * 1_000_000)),
                    fields["currency"],
                    fields["created_at"],
                    int(donation_id),
                    stream_id,
                    current["dedupe_key"],
                ),
            )
            self._conn.commit()
            changed = cursor.rowcount
        saved = self.get_donation(donation_id)
        # SQLite reports 0 rows when every new value matches the stored row.
        unchanged = (
            changed == 0
            and saved is not None
            and saved.get("dedupe_key") == current["dedupe_key"]
        )
        if changed != 1 and not unchanged:
            return None
        self.log(
            "ledger",
            "info",
            f"Edited a manual {fields['kind']} from {fields['author']} for {fields['amount']:.2f} {fields['currency']}.",
        )
        return saved

    def delete_manual_entry(self, donation_id: int) -> bool:
        """Delete one hand-entered row. Automatic rows stay until the ledger is reset."""
        current = self.get_donation(donation_id)
        if current is None:
            return False
        if not current["manual"]:
            raise ValueError("An automatic entry stays in the ledger. Hide it, or reset this ledger.")
        stream_id = self._active_stream_id()
        with self._lock:
            self._conn.execute(
                "DELETE FROM donations WHERE id = ? AND stream_id = ?",
                (int(donation_id), stream_id),
            )
            self._conn.commit()
        self.log("ledger", "info", "Deleted a manual ledger entry.")
        return True

    def restore_manual_entry(self, payload: object) -> int:
        """Put one deleted hand-entered row back. Automatic rows cannot come back this way."""
        if not isinstance(payload, dict):
            raise ValueError("Only a deleted manual entry can be restored.")
        dedupe = str(payload.get("dedupe_key") or "").strip().lower()
        if not _MANUAL_DEDUPE.fullmatch(dedupe):
            raise ValueError("Only a deleted manual entry can be restored.")
        fields = _checked_manual_fields(
            author_name=payload.get("author_name"),
            platform=payload.get("platform"),
            kind=payload.get("kind"),
            amount=payload.get("amount", payload.get("amount_value")),
            currency=payload.get("currency", "USD"),
            message=payload.get("message", ""),
            created_at=payload.get("created_at", ""),
            require_time=True,
        )
        note = payload.get("note", "")
        if not isinstance(note, str):
            raise ValueError("note must be text.")
        if len(note) > 2000:
            raise ValueError("note is limited to 2000 characters.")
        processed = payload.get("processed", False)
        if not isinstance(processed, bool):
            raise ValueError("processed must be true or false.")
        processed_at = _optional_time(payload.get("processed_at")) if processed else None
        if processed and processed_at is None:
            processed_at = utc_now_iso()
        received = _optional_time(payload.get("received_at")) or utc_now_iso()
        reuse_id = _optional_row_id(payload.get("id"))
        token = dedupe.split(":", 1)[1]
        raw = json.dumps({"manual": True}, separators=(",", ":"))
        stream_id = self._active_stream_id()
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM donations WHERE dedupe_key = ?", (dedupe,)
            ).fetchone()
            if existing is not None:
                raise ValueError("That entry is already in the ledger.")
            if reuse_id is not None:
                taken = self._conn.execute(
                    "SELECT id FROM donations WHERE id = ?", (reuse_id,)
                ).fetchone()
                if taken is not None:
                    reuse_id = None
            columns = [
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
            ]
            values: list[object] = [
                dedupe,
                fields["platform"],
                fields["kind"],
                token,
                "",
                fields["author"],
                "",
                fields["message"],
                fields["amount"],
                int(round(fields["amount"] * 1_000_000)),
                fields["currency"],
                fields["created_at"],
                received,
                1 if processed else 0,
                processed_at,
                note,
                raw,
                0,
                stream_id,
            ]
            if reuse_id is not None:
                columns.insert(0, "id")
                values.insert(0, reuse_id)
            placeholders = ", ".join("?" for _ in columns)
            try:
                self._conn.execute(
                    f"INSERT INTO donations ({', '.join(columns)}) VALUES ({placeholders})",
                    values,
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                self._conn.rollback()
                raise ValueError("That entry is already in the ledger.") from exc
            if reuse_id is None:
                reuse_id = int(self._conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        self._tighten()
        self.log("ledger", "info", "Restored a manual ledger entry.")
        return reuse_id

    def update_many(self, ids: list[int], *, processed: bool) -> int:
        if not ids:
            return 0
        moment = utc_now_iso() if processed else None
        stream_id = self._active_stream_id()
        changed = 0
        with self._lock:
            for donation_id in ids:
                cursor = self._conn.execute(
                    """
                    UPDATE donations
                    SET processed = ?, processed_at = ?
                    WHERE id = ? AND stream_id = ?
                    """,
                    (1 if processed else 0, moment, int(donation_id), stream_id),
                )
                changed += cursor.rowcount
            self._conn.commit()
        return changed

    def summary(self) -> dict:
        stream_id = self._active_stream_id()
        with self._lock:
            totals = self._conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN processed = 0 THEN 1 ELSE 0 END) AS unprocessed
                FROM donations
                WHERE stream_id = ?
                """,
                (stream_id,),
            ).fetchone()
            by_platform = self._conn.execute(
                """
                SELECT platform, COUNT(*) AS count,
                       SUM(CASE WHEN processed = 0 THEN 1 ELSE 0 END) AS unprocessed
                FROM donations
                WHERE stream_id = ?
                GROUP BY platform
                ORDER BY platform
                """,
                (stream_id,),
            ).fetchall()
            by_currency = self._conn.execute(
                """
                SELECT COALESCE(currency, '') AS currency,
                       COUNT(*) AS count,
                       ROUND(SUM(amount_value), 6) AS amount
                FROM donations
                WHERE stream_id = ? AND hidden = 0 AND amount_value IS NOT NULL
                GROUP BY currency
                ORDER BY currency
                """,
                (stream_id,),
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
        filters = {key: value for key, value in filters.items() if key != "include_hidden"}
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
        filters = {key: value for key, value in filters.items() if key != "include_hidden"}
        excluded = list(filters.get("exclude_kinds") or [])  # type: ignore[arg-type]
        if "gift_membership_received" not in excluded:
            excluded.append("gift_membership_received")
        filters = {**filters, "exclude_kinds": excluded}
        total = 0.0
        for row in self._money_rows(**filters):  # type: ignore[arg-type]
            priced = quote(row, membership_price, rates)
            if not self._matches_priced_query(row, priced, filters):
                continue
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

        filters = {key: value for key, value in filters.items() if key != "include_hidden"}
        table = {"USD": 1.0, **(rates or {})}
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in self._money_rows(**filters):  # type: ignore[arg-type]
            priced = quote(row, membership_price, table)
            if not self._matches_priced_query(row, priced, filters):
                continue
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

    def log_path(self) -> Path:
        return self.path.parent / LOG_NAME

    def verbose_logs(self) -> bool:
        return self.get_setting("verbose_logs") == "1"

    def set_verbose_logs(self, enabled: bool) -> None:
        self.set_setting("verbose_logs", "1" if enabled else "0")

    def log(self, platform: str, level: str, message: str) -> None:
        # The page reads the newest rows here. The file keeps every line.
        at = utc_now_iso()
        text = "" if message is None else str(message)
        with self._lock:
            self._conn.execute(
                "INSERT INTO ingest_log (at, platform, level, message) VALUES (?, ?, ?, ?)",
                (at, platform, level, text),
            )
            self._conn.execute(
                """
                DELETE FROM ingest_log
                WHERE id NOT IN (SELECT id FROM ingest_log ORDER BY id DESC LIMIT ?)
                """,
                (_LOG_KEEP,),
            )
            self._conn.commit()
        self._append_log_file(at, platform, text)

    def recent_log(self, limit: int = _LOG_PAGE) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT at, platform, level, message FROM ingest_log ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, _LOG_PAGE)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _log_new_payloads(self, donations: list[Donation]) -> None:
        if not donations or not self.verbose_logs():
            return
        for donation in donations:
            self.log(donation.platform, "info", payload_message(donation.raw, donation.dedupe_key))

    def _append_log_file(self, at: str, platform: str, message: str) -> None:
        path = self.log_path()
        line = f"{at}  {platform}  {message}\n".encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            with self._log_file_lock:
                path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                os.chmod(path.parent, 0o700)
                # Windows has no O_NOFOLLOW, so a link is refused here instead.
                if not hasattr(os, "O_NOFOLLOW") and path.is_symlink():
                    return
                fd = os.open(path, flags, 0o600)
                try:
                    os.chmod(path, 0o600)
                    view = memoryview(line)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0:
                            raise OSError("log write made no progress")
                        view = view[written:]
                finally:
                    os.close(fd)
        except OSError:
            return

    def _matches_priced_query(self, row: dict, priced: object, filters: dict) -> bool:
        view = dict(row)
        view["quoted_amount"] = getattr(priced, "amount", None)
        view["quoted_currency"] = getattr(priced, "currency", None)
        view["amount_usd"] = getattr(priced, "usd", None)
        view["kind_label"] = getattr(priced, "label", None) or row.get("kind_label")
        return matches_ledger_query(view, str(filters.get("query") or ""))

    def _filters(
        self,
        *,
        platform: str,
        kind: str,
        processed: str,
        exclude_kinds: list[str],
        include_hidden: bool = False,
        stream_id: str,
    ) -> tuple[str, list[object]]:
        clauses: list[str] = ["stream_id = ?"]
        params: list[object] = [stream_id]
        if not include_hidden:
            clauses.append("hidden = 0")
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
        if not clauses:
            return "", params
        return "WHERE " + " AND ".join(clauses), params

    def _money_rows(self, **filters: object) -> list[dict]:
        where, params = self._filters(
            platform=str(filters.get("platform") or ""),
            kind=str(filters.get("kind") or ""),
            processed=str(filters.get("processed") or ""),
            exclude_kinds=list(filters.get("exclude_kinds") or []),  # type: ignore[arg-type]
            stream_id=self._active_stream_id(),
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

    def _with_running_usd(
        self,
        rows: list[sqlite3.Row],
        membership_price: float | None,
        rates: dict[str, float] | None,
    ) -> dict[int, dict]:
        """Chronological ledger USD after each row. A missing quote adds nothing."""
        found: dict[int, dict] = {}
        total = 0.0
        for row in rows:
            item = self._present(row, membership_price, rates)
            usd = None if item.get("hidden") else item.get("amount_usd")
            total = round2(total + (float(usd) if usd is not None else 0.0))
            item["running_usd"] = total
            found[int(item["id"])] = item
        return found

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
            "author_name": display_donor(row["author_name"]),
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
            "hidden": bool(row["hidden"]),
            "manual": str(row["dedupe_key"] or "").startswith("manual:"),
        }
        if include_raw:
            payload["raw"] = _raw_object(row["raw_json"])
        return payload


class DuplicateManual(Exception):
    """A hand-entered gift matches one already stored on the open ledger."""

    def __init__(self, match: dict):
        super().__init__("This ledger already has a matching gift.")
        self.match = match


def _checked_manual_fields(
    *,
    author_name: object,
    platform: object,
    kind: object,
    amount: object,
    currency: object = "USD",
    message: object = "",
    created_at: object = "",
    require_time: bool = False,
) -> dict:
    """Shared checks for a new manual gift and for putting a deleted one back."""
    author = display_donor(author_name)
    if not author:
        raise ValueError("Donor name is required.")
    if len(author) > 200:
        raise ValueError("Donor name is limited to 200 characters.")
    platform_id = str(platform or "").strip().lower()
    if platform_id not in PLATFORM_LABELS:
        raise ValueError("Platform must be YouTube, Rumble, Kick, or Other.")
    kind_id = str(kind or "").strip().lower()
    if kind_id not in {"super_chat", "super_sticker", "membership", "donation"}:
        raise ValueError("Type must be Super Chat, Super Sticker, Membership, or Donation.")
    code = str(currency or "USD").strip().upper() or "USD"
    if code not in SUPPORTED_CURRENCY_CODES:
        raise ValueError("Currency must be a supported code, such as USD or PHP.")
    if isinstance(amount, bool) or amount is None or str(amount).strip() == "":
        raise ValueError("Amount is required.")
    try:
        number = round(float(amount), 2)
    except (TypeError, ValueError) as exc:
        raise ValueError("Amount must be a number.") from exc
    if number <= 0 or number > 100000:
        raise ValueError("Amount must be greater than 0 and at most 100000.")
    text = str(message or "").strip()
    if len(text) > 2000:
        raise ValueError("Message is limited to 2000 characters.")
    raw_when = str(created_at or "").strip()
    if require_time and not raw_when:
        raise ValueError("Time must be a real date.")
    when = to_utc_iso(created_at) if raw_when else utc_now_iso()
    if not when:
        raise ValueError("Time must be a real date.")
    return {
        "author": author,
        "platform": platform_id,
        "kind": kind_id,
        "amount": number,
        "currency": code,
        "message": text,
        "created_at": when,
    }


def _optional_time(value: object) -> str | None:
    if value is None or value == "":
        return None
    parsed = to_utc_iso(value)
    if not parsed:
        raise ValueError("Time must be a real date.")
    return parsed


def _optional_row_id(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("That entry cannot be restored.")
    return value


def _last_line(row: dict | None) -> str:
    if not row:
        return ""
    who = display_donor(row.get("author_name")) or "Someone"
    amount = row.get("amount_usd")
    shown = format_usd(float(amount)) if isinstance(amount, (int, float)) else "—"
    platform = row.get("platform_label") or row.get("platform") or "a platform"
    kind = row.get("kind_label") or "gift"
    return f"{who} sent {kind} for {shown} on {platform}."


def _raw_object(raw_json: object) -> dict:
    try:
        parsed = json.loads(str(raw_json or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
