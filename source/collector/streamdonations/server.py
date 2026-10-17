"""Local UI, API, OAuth callbacks, and the Kick webhook listener."""

from __future__ import annotations

import hmac
import html
import json
import re
import threading
import traceback
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .battery import host_battery
from .blue import inspect_script
from .currencies import SUPPORTED_CURRENCIES
from .embed import FRAME_SRC, player_for_ledger
from .fx import rate_status, refresh_rates, usd_rates
from .kinds import KIND_LABELS, PLATFORM_LABELS
from .goals import load_tiers
from .money import DISPLAY_LABELS, parse_membership_price
from .paste import paste_for_form
from .service import Service, displayed_ledger_page_size
from .ledger_file import parse_ledger_jsonl, present_archive
from .settings_file import export_settings, parse_settings_file
from .store import DuplicateManual, Store, _checked_manual_fields
from .util import ApiError, content_disposition, redact, utc_now_iso
from .kick import SCOPES as KICK_SCOPES
from .youtube import SCOPE as YOUTUBE_SCOPE, open_browser
from .youtube_quota import clamp_poll_seconds

WEB_DIR = Path(__file__).resolve().parent / "web"
_DONATION_PATH = re.compile(r"/api/donations/(\d+)")
_STATIC_FILES = {
    "/icons/youtube.svg": ("icons/youtube.svg", "image/svg+xml"),
    "/icons/rumble.svg": ("icons/rumble.svg", "image/svg+xml"),
    "/icons/kick.svg": ("icons/kick.svg", "image/svg+xml"),
    "/icons/superberry.png": ("icons/superberry.png", "image/png"),
    "/fonts/fredoka.woff2": ("fonts/fredoka.woff2", "font/woff2"),
}


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"
    headers: list[tuple[str, str]] = field(default_factory=list)


def json_response(status: int, payload: object, headers: list[tuple[str, str]] | None = None) -> Response:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    return Response(status, body, headers=list(headers or []))


def text_response(status: int, text: str, content_type: str, headers: list[tuple[str, str]] | None = None) -> Response:
    return Response(status, text.encode("utf-8"), content_type, list(headers or []))


def same_secret(left: str, right: str) -> bool:
    if len(left) != len(right) or not left:
        return False
    return hmac.compare_digest(left, right)


class App:
    def __init__(self, store: Store, token: str, ui_port: int, service: Service | None = None):
        self.store = store
        self.token = token
        self.ui_port = ui_port
        self.service = service or Service(store)
        self.service.on_listen_change = self.restart_webhook
        self._webhook: ThreadingHTTPServer | None = None
        self._webhook_thread = None
        # Set by `serve` when nothing else owns the process. The desktop shell
        # stops the collector itself, so the page has no Quit there.
        self.shutdown_hook = None

    def start(self) -> None:
        # Default is paused. Write that before the collector loops read the flag.
        self.store.stream_tabs()
        self.service.start()
        self.restart_webhook()

    def stop(self) -> None:
        self.service.stop()
        self.stop_webhook()

    def restart_webhook(self) -> None:
        self.stop_webhook()
        host = self.store.get_setting("kick_webhook_host", "127.0.0.1") or "127.0.0.1"
        try:
            port = int(self.store.get_setting("kick_webhook_port", "47322") or "47322")
        except ValueError:
            port = 47322
        try:
            server = ThreadingServer((host, port), WebhookHandler)
        except OSError as exc:
            message = f"Kick webhook listener could not bind {host}:{port}: {exc}"
            self.store.set_status("kick", "error", message, error=message)
            self.store.log("kick", "error", message)
            return
        server.app = self  # type: ignore[attr-defined]
        self._webhook = server
        thread = threading.Thread(target=server.serve_forever, name="kick-webhook", daemon=True)
        self._webhook_thread = thread
        thread.start()

    def stop_webhook(self) -> None:
        server = self._webhook
        self._webhook = None
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = self._webhook_thread
        self._webhook_thread = None
        if thread is not None:
            thread.join(timeout=2)

    def google_redirect(self) -> str:
        return f"http://127.0.0.1:{self.ui_port}/oauth/google/callback"

    def kick_redirect(self) -> str:
        return f"http://127.0.0.1:{self.ui_port}/oauth/kick/callback"

    def own_origins(self) -> set[str]:
        """The page's own origins. The Host check only lets these two names in."""
        return {f"http://127.0.0.1:{self.ui_port}", f"http://localhost:{self.ui_port}"}

    def _cross_origin_refusal(self, method: str, headers, body: bytes) -> Response | None:
        """Refuse a changing request that a different web page could have sent.

        The session cookie is SameSite=Strict, but another port on 127.0.0.1 is
        the same site, so its pages carry the cookie. A browser always sends
        Origin on a POST, PUT, PATCH, or DELETE, and the page here, in Electron
        or a browser, sends its own origin. Referer is the fallback when Origin
        is absent; this page sends none (Referrer-Policy: no-referrer), so an
        absent pair is refused. A body must be JSON: a text/plain body is the
        one a page elsewhere can send without the browser asking first.
        A Bearer token is not sent by browsers on their own, so it is exempt.
        """
        if method in {"GET", "HEAD"}:
            return None
        if self._bearer(headers):
            return None
        origin = (headers.get("Origin") or "").strip()
        if not origin:
            referer = (headers.get("Referer") or "").strip()
            parsed = urlparse(referer) if referer else None
            if parsed and parsed.scheme and parsed.netloc:
                origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin.rstrip("/").lower() not in self.own_origins():
            return json_response(403, {"error": "This request did not come from the SuperBerry page."})
        if body:
            content_type = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                return json_response(415, {"error": "Send JSON."})
        return None

    def handle(self, method: str, path: str, query: dict[str, list[str]], headers, body: bytes) -> Response:
        try:
            if path == "/boot" and method == "GET":
                return self._boot(query)
            if path == "/favicon.ico" and method == "GET":
                return self._file("icons/superberry.png", "image/png")
            if path in {"/oauth/google/callback", "/oauth/kick/callback"} and method == "GET":
                provider = "youtube" if "google" in path else "kick"
                return self._oauth_callback(provider, query)
            if not self._authorized(headers):
                return json_response(401, {"error": "Sign in to this local app again."})
            refusal = self._cross_origin_refusal(method, headers, body)
            if refusal is not None:
                return refusal
            if method == "GET" and path in {"/", "/index.html"}:
                return self._file("index.html", "text/html; charset=utf-8")
            if method == "GET" and path == "/app.js":
                return self._file("app.js", "text/javascript; charset=utf-8")
            if method == "GET" and path == "/styles.css":
                return self._file("styles.css", "text/css; charset=utf-8")
            if method == "GET" and path in _STATIC_FILES:
                name, content_type = _STATIC_FILES[path]
                return self._file(name, content_type)
            if path.startswith("/api/"):
                return self._api(method, path, query, body)
            return json_response(404, {"error": "Not found"})
        except DuplicateManual as exc:
            match = exc.match
            return json_response(409, {
                "error": "This ledger already has a matching gift.",
                "duplicate": True,
                "author_name": match.get("author_name") or "",
                "amount": match.get("amount"),
                "currency": match.get("currency") or "",
            })
        except ValueError as exc:
            return json_response(400, {"error": str(exc)})
        except ApiError as exc:
            return json_response(502, {"error": redact(str(exc))})
        except Exception:
            traceback.print_exc()
            return json_response(500, {"error": "Internal error"})

    def _api(self, method: str, path: str, query: dict[str, list[str]], body: bytes) -> Response:
        if path == "/api/health" and method == "GET":
            return json_response(200, {"ok": True})
        if path == "/api/meta" and method == "GET":
            return json_response(200, self._meta())
        if path == "/api/notices" and method == "GET":
            feed = self.store.notices
            after_text = _one(query, "after")
            after = int(after_text) if after_text.isdigit() else None
            if feed is None:
                return json_response(200, {"notices": [], "last": 0})
            return json_response(200, feed.since(after))
        if path == "/api/shutdown" and method == "POST":
            hook = self.shutdown_hook
            if hook is None:
                return json_response(409, {"error": "The SuperBerry desktop app stops the collector itself."})
            self.store.log("ledger", "info", "Quit from the browser page.")
            hook()
            return json_response(200, {"stopping": True})
        if path == "/api/status" and method == "GET":
            return json_response(200, {
                "paused": self.service.paused(),
                "platforms": self.store.status(),
                "battery": host_battery(),
                **self._log_view(),
            })
        if path == "/api/log" and method == "PUT":
            body_object = _object(body)
            if not isinstance(body_object.get("verbose"), bool):
                raise ValueError("Verbose must be true or false.")
            enabled = body_object["verbose"]
            self.store.set_verbose_logs(enabled)
            if enabled:
                self.store.log(
                    "ledger",
                    "info",
                    "Verbose logging is on. New ledger entries include a compacted payload.",
                )
            else:
                self.store.log("ledger", "info", "Verbose logging is off.")
            return json_response(200, self._log_view())
        if path == "/api/settings" and method == "GET":
            return json_response(200, self._public_settings())
        if path == "/api/settings" and method == "PUT":
            self.service.apply_settings(_object(body))
            return json_response(200, self._public_settings())
        if path == "/api/settings/export" and method == "GET":
            exported = export_settings(self._public_settings())
            return Response(
                200,
                exported,
                "application/json; charset=utf-8",
                [("Content-Disposition", 'attachment; filename="stream-donations-settings.json"')],
            )
        if path == "/api/settings/import" and method == "POST":
            imported = parse_settings_file(body)
            if imported.settings:
                self.service.apply_settings(imported.settings)
            payload = self._public_settings()
            payload["settings_import"] = {
                "ignored": list(imported.ignored),
                "version": imported.version,
            }
            self.store.log("ledger", "info", "Imported settings for the open stream tab.")
            return json_response(200, payload)
        if path == "/api/presets" and method == "GET":
            return json_response(200, {"presets": self.store.streamer_presets()})
        if path == "/api/presets" and method == "POST":
            payload = _object(body)
            action = str(payload.get("action") or "").strip().lower()
            if action == "save":
                saved = self.store.save_streamer_preset(
                    payload.get("name"),
                    payload.get("youtube_channel"),
                    payload.get("rumble_channel"),
                    payload.get("kick_watch"),
                    payload.get("tiers_enabled", False),
                    payload.get("goal_tiers"),
                    payload.get("script_id", ""),
                )
                self.store.log("ledger", "info", f"Saved streamer preset {saved['name']}.")
                return json_response(200, {
                    "preset": saved,
                    "presets": self.store.streamer_presets(),
                })
            if action == "delete":
                name = self.store.delete_streamer_preset(payload.get("id"))
                self.store.log("ledger", "info", f"Removed streamer preset {name}.")
                return json_response(200, {"presets": self.store.streamer_presets()})
            if action == "apply":
                preset = self.service.apply_preset(payload.get("id"))
                self.store.log("ledger", "info", f"Applied streamer preset {preset['name']} to the open ledger.")
                return json_response(200, {
                    "preset": preset,
                    "presets": self.store.streamer_presets(),
                    "settings": self._public_settings(),
                })
            raise ValueError("Streamer preset action must be save, delete, or apply.")
        if path == "/api/blue" and method == "GET":
            return json_response(200, {"scripts": self.store.blue_scripts()})
        if path == "/api/blue" and method == "POST":
            payload = _object(body)
            action = str(payload.get("action") or "").strip().lower()
            if action == "save":
                saved = self.store.save_blue_script(payload.get("name"), payload.get("source"))
                self.store.log("ledger", "info", f"Saved BLUE script {saved['name']}.")
                return json_response(200, {
                    "script": saved,
                    "scripts": self.store.blue_scripts(),
                    "problems": inspect_script(saved.get("source")),
                })
            if action == "delete":
                script_id = str(payload.get("id") or "")
                name = self.store.delete_blue_script(script_id)
                self.service.blue.forget_script(script_id, name)
                self.store.log("ledger", "info", f"Removed BLUE script {name}.")
                return json_response(200, {"scripts": self.store.blue_scripts()})
            raise ValueError("BLUE script action must be save or delete.")
        if path == "/api/blue/pending" and method == "GET":
            return json_response(200, {"pending": self.service.blue.claim()})
        if path == "/api/blue/result" and method == "POST":
            self.service.blue.record(_object(body))
            return json_response(200, {"ok": True})
        if path == "/api/embed" and method == "GET":
            platform = _one(query, "platform").strip().lower()
            if platform not in {"youtube", "rumble", "kick"}:
                raise ValueError("Stream source must be YouTube, Rumble, or Kick.")
            # Player URL for the open ledger's iframe. Not written to the log.
            return json_response(200, self._player(platform))
        if path == "/api/chat" and method == "GET":
            # Lines already read for the open ledger. Not written to the log.
            return json_response(200, self.service.chat.since(
                _whole_number(query, "after"),
                _whole_number(query, "generation"),
                self.service.paused(),
            ))
        if path == "/api/chat/destinations" and method == "GET":
            return json_response(200, self.service.chat_destinations())
        if path == "/api/chat/rumble" and method == "GET":
            stream = _one(query, "stream").strip()
            return json_response(200, self.service.rumble_popup(stream or None))
        if path == "/api/chat/youtube" and method == "GET":
            return json_response(200, self.service.youtube_popup())
        if path == "/api/chat/kick" and method == "GET":
            return json_response(200, self.service.kick_popup())
        if path == "/api/chat/send" and method == "POST":
            payload = _object(body)
            return json_response(200, self.service.send_chat(
                payload.get("text"),
                payload.get("platforms"),
                payload.get("stream_id"),
            ))
        if path == "/api/giveaway" and method == "GET":
            return json_response(200, self.service.giveaway.status())
        if path == "/api/giveaway" and method == "POST":
            payload = _object(body)
            action = str(payload.get("action") or "").strip().lower()
            if action == "start":
                # Keyword and winner stay out of the log. The drawing is not saved.
                return json_response(200, self.service.giveaway.start(
                    payload.get("keyword"),
                    payload.get("seconds", 0),
                ))
            if action == "stop":
                return json_response(200, self.service.giveaway.stop())
            if action == "redraw":
                return json_response(200, self.service.giveaway.redraw())
            raise ValueError("Giveaway action must be start, stop, or redraw.")
        if path == "/api/stream" and method == "GET":
            rates, price = self._rates_and_price()
            return json_response(200, self.store.stream_report(rates, price))
        if path == "/api/stream-titles" and method == "GET":
            return json_response(200, self.service.stream_titles())
        if path == "/api/stream" and method == "POST":
            action = str(_object(body).get("action") or "").strip().lower()
            if action == "start":
                self.store.set_stream_started(utc_now_iso())
                self.store.log("ledger", "info", "This ledger's count starts now. Donations stay.")
            elif action == "full":
                self.store.set_stream_started(None)
                self.store.log("ledger", "info", "This ledger's count covers every donation on it.")
            else:
                raise ValueError("Stream action must be start or full.")
            rates, price = self._rates_and_price()
            return json_response(200, self.store.stream_report(rates, price))
        if path == "/api/streams" and method == "POST":
            payload = _object(body)
            action = str(payload.get("action") or "").strip().lower()
            if action == "add":
                self.store.add_stream()
                self.store.log("ledger", "info", "Opened a new ledger. Its title and donations start empty.")
            elif action == "switch":
                self.store.switch_stream(payload.get("id"))
            elif action == "close":
                self.store.close_stream(payload.get("id"))
                self.store.log("ledger", "info", "Closed a ledger. Its donations were removed.")
            elif action == "rename":
                self.store.rename_stream(payload.get("title"))
            else:
                raise ValueError("Stream tab action must be add, switch, close, or rename.")
            if action in {"add", "switch", "close"}:
                self.service.follow_open_ledger()
            rates, price = self._rates_and_price()
            return json_response(200, self.store.stream_report(rates, price))
        if path == "/api/paste" and method == "POST":
            payload = _object(body)
            rates, price = self._rates_and_price()
            return json_response(200, paste_for_form(payload.get("text"), price, rates))
        if path == "/api/donations" and method == "POST":
            payload = _object(body)
            fields = _checked_manual_fields(
                author_name=payload.get("author_name"),
                platform=payload.get("platform"),
                kind=payload.get("kind"),
                amount=payload.get("amount"),
                currency=payload.get("currency", "USD"),
                message=payload.get("message", ""),
                created_at=payload.get("created_at", ""),
            )
            if payload.get("confirm_duplicate") is not True:
                match = self.store.manual_duplicate(fields)
                if match is not None:
                    raise DuplicateManual(match)
            donation_id = self.store.add_manual_entry(
                author_name=fields["author"],
                platform=fields["platform"],
                kind=fields["kind"],
                amount=fields["amount"],
                currency=fields["currency"],
                message=fields["message"],
                created_at=fields["created_at"],
            )
            rates, price = self._rates_and_price()
            donation = self.store.get_donation(donation_id, membership_price=price, rates=rates)
            return json_response(201, donation or {"id": donation_id})
        if path == "/api/donations/restore" and method == "POST":
            payload = _object(body)
            donation_id = self.store.restore_manual_entry(payload)
            rates, price = self._rates_and_price()
            donation = self.store.get_donation(donation_id, membership_price=price, rates=rates)
            return json_response(201, donation or {"id": donation_id})
        if path == "/api/summary" and method == "GET":
            payload = self.store.summary()
            rates, price = self._rates_and_price()
            payload["total_usd"] = self.store.total_donated(rates, price)
            payload["fx"] = rate_status(self.store)
            return json_response(200, payload)
        if path == "/api/rates/refresh" and method == "POST":
            return json_response(200, refresh_rates(self.store))
        if path == "/api/ledger/reset" and method == "POST":
            removed = self.store.clear_donations()
            self.store.log(
                "ledger",
                "info",
                f"This ledger was reset. Removed {removed} donation(s). Other ledgers and settings were kept.",
            )
            return json_response(200, {"removed": removed})
        if path == "/api/donations" and method == "GET":
            filters = _donation_filters(query)
            # Hidden rows stay on the ledger. Totals and exports leave them out.
            filters["include_hidden"] = True
            rates, price = self._rates_and_price()
            rows, total = self.store.list_donations(**filters, membership_price=price, rates=rates)
            return json_response(200, {"donations": rows, "total": total, "limit": filters["limit"], "offset": filters["offset"]})
        if path == "/api/donations/bulk" and method == "POST":
            payload = _object(body)
            ids = payload.get("ids")
            if not isinstance(ids, list) or len(ids) > 500:
                raise ValueError("ids must be a list of at most 500 donation ids.")
            if not isinstance(payload.get("processed"), bool):
                raise ValueError("processed must be true or false.")
            changed = self.store.update_many([int(item) for item in ids], processed=payload["processed"])
            return json_response(200, {"updated": changed})
        match = _DONATION_PATH.fullmatch(path)
        if match and method == "GET":
            rates, price = self._rates_and_price()
            donation = self.store.get_donation(int(match.group(1)), membership_price=price, rates=rates)
            if donation is None:
                return json_response(404, {"error": "Donation not found"})
            return json_response(200, donation)
        if match and method == "PATCH":
            payload = _object(body)
            donation_id = int(match.group(1))
            # A gift edit is separate from a note or a hide. The drawer sends only the note.
            if any(key in payload for key in ("author_name", "platform", "kind", "amount")):
                current = self.store.get_donation(donation_id)
                if current is None:
                    return json_response(404, {"error": "Donation not found"})
                if not current["manual"]:
                    raise ValueError("Automatic entries cannot be edited.")
                created_at = payload.get("created_at", "")
                if created_at is None or str(created_at).strip() == "":
                    created_at = current["created_at"]
                fields = _checked_manual_fields(
                    author_name=payload.get("author_name"),
                    platform=payload.get("platform"),
                    kind=payload.get("kind"),
                    amount=payload.get("amount"),
                    currency=payload.get("currency", "USD"),
                    message=payload.get("message", ""),
                    created_at=created_at,
                )
                if payload.get("confirm_duplicate") is not True:
                    found = self.store.manual_duplicate(fields, ignore_id=donation_id)
                    if found is not None:
                        raise DuplicateManual(found)
                updated = self.store.update_manual_entry(
                    donation_id,
                    author_name=fields["author"],
                    platform=fields["platform"],
                    kind=fields["kind"],
                    amount=fields["amount"],
                    currency=fields["currency"],
                    message=fields["message"],
                    created_at=fields["created_at"],
                )
                if updated is None:
                    return json_response(404, {"error": "Donation not found"})
                rates, price = self._rates_and_price()
                donation = self.store.get_donation(donation_id, membership_price=price, rates=rates)
                return json_response(200, donation or {"id": donation_id})
            processed = payload.get("processed", None)
            note = payload.get("note", None)
            if processed is not None and not isinstance(processed, bool):
                raise ValueError("processed must be true or false.")
            if note is not None:
                if not isinstance(note, str):
                    raise ValueError("note must be text.")
                if len(note) > 2000:
                    raise ValueError("note is limited to 2000 characters.")
            if "hidden" in payload:
                if not isinstance(payload["hidden"], bool):
                    raise ValueError("hidden must be true or false.")
                updated = self.store.set_entry_hidden(donation_id, payload["hidden"])
                if updated is None:
                    return json_response(404, {"error": "Donation not found"})
            if processed is not None or note is not None or "hidden" not in payload:
                updated = self.store.update_donation(donation_id, processed=processed, note=note)
                if updated is None:
                    return json_response(404, {"error": "Donation not found"})
            rates, price = self._rates_and_price()
            updated = self.store.get_donation(donation_id, membership_price=price, rates=rates)
            return json_response(200, updated)
        if match and method == "DELETE":
            donation_id = int(match.group(1))
            if not self.store.delete_manual_entry(donation_id):
                return json_response(404, {"error": "Donation not found"})
            return json_response(200, {"removed": donation_id})
        if path == "/api/export.csv" and method == "GET":
            rates, price = self._rates_and_price()
            filters = _export_filters(query)
            text = self.store.export_csv(rates=rates, membership_price=price, **filters)
            name = self.store.export_download_name(
                "csv", rates=rates, membership_price=price, **filters
            )
            return text_response(
                200,
                text,
                "text/csv; charset=utf-8",
                [("Content-Disposition", content_disposition(name))],
            )
        if path == "/api/export.jsonl" and method == "GET":
            filters = _export_filters(query)
            text = self.store.export_jsonl(**filters)
            rates, price = self._rates_and_price()
            name = self.store.export_download_name(
                "jsonl", rates=rates, membership_price=price, **filters
            )
            return text_response(
                200,
                text,
                "application/x-ndjson; charset=utf-8",
                [("Content-Disposition", content_disposition(name))],
            )
        if path == "/api/export/archive" and method == "POST":
            # The whole ledger, then close. The on-screen filter is not applied.
            # An id archives that ledger. No id archives the one that is open.
            payload = _object(body)
            raw_id = payload.get("id")
            tab_id = raw_id.strip() if isinstance(raw_id, str) else ""
            return json_response(200, self.store.archive_open_ledger(tab_id))
        if path == "/api/archive" and method == "GET":
            # Read only. archive.db is not written, and the open ledger stays.
            from .archive import ARCHIVE_NAME, read_archive_groups

            rates, price = self._rates_and_price()
            return json_response(200, read_archive_groups(
                self.store.path.parent / ARCHIVE_NAME,
                membership_price=price,
                rates=rates,
            ))
        if path == "/api/ledger/archive" and method == "POST":
            # Preview only. The file is not stored and the open ledger is not changed.
            rows = parse_ledger_jsonl(body)
            rates, price = self._rates_and_price()
            return json_response(200, present_archive(rows, membership_price=price, rates=rates))
        if path == "/api/collection" and method == "POST":
            payload = _object(body)
            if not isinstance(payload.get("paused"), bool):
                raise ValueError("paused must be true or false.")
            raw_id = payload.get("id")
            tab_id = raw_id if isinstance(raw_id, str) else None
            self.service.set_paused(payload["paused"], tab_id)
            return json_response(200, {
                "paused": self.service.paused(),
                "platforms": self.store.status(),
                "streams": self.store.stream_tabs(),
            })
        if path == "/api/poll" and method == "POST":
            if self.service.paused():
                return json_response(200, {
                    "ok": False,
                    "paused": True,
                    "errors": ["Collection is paused. Reconnect to send requests again."],
                    "platforms": self.store.status(),
                })
            errors = self.service.poll_now()
            return json_response(200, {
                "ok": not errors,
                "paused": self.service.paused(),
                "errors": errors,
                "platforms": self.store.status(),
            })
        if path == "/api/oauth/youtube/start" and method == "POST":
            url = self.service.begin_oauth("youtube", self.google_redirect())
            return json_response(200, {"url": url, "opened": open_browser(url)})
        if path == "/api/oauth/kick/start" and method == "POST":
            url = self.service.begin_oauth("kick", self.kick_redirect())
            return json_response(200, {"url": url, "opened": open_browser(url)})
        if path == "/api/oauth/youtube/disconnect" and method == "POST":
            self.service.youtube.disconnect()
            return json_response(200, self._public_settings())
        if path == "/api/oauth/kick/disconnect" and method == "POST":
            self.service.kick.disconnect()
            return json_response(200, self._public_settings())
        if path == "/api/kick/subscribe" and method == "POST":
            if self.service.paused():
                raise ValueError("Collection is paused. Reconnect before subscribing.")
            payload = self.service.kick.subscribe()
            self.service.kick.tick(force=True)
            return json_response(200, {"result": payload, "platforms": self.store.status()})
        return json_response(404, {"error": "Not found"})

    def _boot(self, query: dict[str, list[str]]) -> Response:
        token = _one(query, "token")
        if not same_secret(token, self.token):
            return text_response(403, "Forbidden", "text/plain; charset=utf-8")
        cookie = f"sd_session={self.token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000"
        return Response(302, b"", "text/plain; charset=utf-8", [("Set-Cookie", cookie), ("Location", "/")])

    def _oauth_callback(self, provider: str, query: dict[str, list[str]]) -> Response:
        error = _one(query, "error")
        if error:
            description = _one(query, "error_description") or error
            self.store.log(provider, "error", redact(description))
            return self._oauth_page("Sign-in was cancelled", description)
        try:
            self.service.finish_oauth(provider, _one(query, "code"), _one(query, "state"))
        except (ValueError, ApiError) as exc:
            self.store.log(provider, "error", redact(str(exc)))
            return self._oauth_page("Sign-in failed", str(exc))
        label = "YouTube" if provider == "youtube" else "Kick"
        return self._oauth_page(f"{label} connected", "You can close this tab and return to SuperBerry.")

    def _oauth_page(self, title: str, message: str) -> Response:
        page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="/styles.css">
</head>
<body>
<main class="oauth-result">
<h1>{html.escape(title)}</h1>
<p>{html.escape(message)}</p>
</main>
</body>
</html>
"""
        return text_response(200, page, "text/html; charset=utf-8")

    def _bearer(self, headers) -> bool:
        header = headers.get("Authorization", "") or ""
        return header.startswith("Bearer ") and same_secret(header[7:].strip(), self.token)

    def _authorized(self, headers) -> bool:
        if self._bearer(headers):
            return True
        cookie = headers.get("Cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "sd_session" and same_secret(value, self.token):
                return True
        return False

    def _file(self, name: str, content_type: str) -> Response:
        path = WEB_DIR / name
        if not path.is_file():
            return json_response(404, {"error": "Missing interface file"})
        return Response(200, path.read_bytes(), content_type)

    def _meta(self) -> dict:
        return {
            "version": __version__,
            "platforms": [{"id": key, "label": value} for key, value in PLATFORM_LABELS.items()],
            "currencies": [{"code": code, "name": name} for code, name in SUPPORTED_CURRENCIES],
            "kinds": [{"id": key, "label": value} for key, value in DISPLAY_LABELS.items()],
            "default_exclude_kinds": ["gift_membership_received"],
            "database": str(self.store.path),
            "log_file": str(self.store.log_path()),
            "google_redirect_uri": self.google_redirect(),
            "kick_redirect_uri": self.kick_redirect(),
            "kick_webhook_hint": self.service.kick.webhook_hint(),
            "scopes": {
                "youtube": YOUTUBE_SCOPE,
                "kick": KICK_SCOPES,
            },
            "can_quit": self.shutdown_hook is not None,
            "system_notifications": self.store.system_notifications_available(),
        }

    def _log_view(self) -> dict:
        return {
            "verbose": self.store.verbose_logs(),
            "log_file": str(self.store.log_path()),
            "log": self.store.recent_log(),
        }

    def _public_settings(self) -> dict:
        def seconds(key: str, default: str) -> int:
            try:
                return int(self.store.get_setting(key, default) or default)
            except ValueError:
                return int(default)

        return {
            "youtube_client_id": self.store.get_setting("youtube_client_id"),
            "youtube_client_secret_set": bool(self.store.get_setting("youtube_client_secret")),
            "youtube_connected": bool(self.store.get_setting("youtube_refresh_token")),
            "youtube_poll_seconds": clamp_poll_seconds(self.store.get_setting("youtube_poll_seconds", "900")),
            "youtube_watch": self.store.get_setting("youtube_watch"),
            "youtube_channel": self.store.get_setting("youtube_channel"),
            "youtube_follow_live": self.store.get_setting("youtube_follow_live") == "1",
            "rumble_api_url_set": bool(self.store.get_setting("rumble_api_url")),
            "rumble_watch": self.store.get_setting("rumble_watch"),
            "rumble_channel": self.store.get_setting("rumble_channel"),
            "rumble_follow_live": self.store.get_setting("rumble_follow_live") == "1",
            "rumble_poll_seconds": seconds("rumble_poll_seconds", "8"),
            "kick_watch": self.store.get_setting("kick_watch"),
            "kick_follow_live": self.store.get_setting("kick_follow_live") == "1",
            "kick_client_id": self.store.get_setting("kick_client_id"),
            "kick_client_secret_set": bool(self.store.get_setting("kick_client_secret")),
            "kick_connected": bool(self.store.get_setting("kick_refresh_token")),
            "kick_webhook_host": self.store.get_setting("kick_webhook_host", "127.0.0.1") or "127.0.0.1",
            "kick_webhook_port": seconds("kick_webhook_port", "47322"),
            "kick_public_base": self.store.get_setting("kick_public_base"),
            "membership_price": self.store.get_setting("membership_price"),
            "ledger_page_size": displayed_ledger_page_size(self.store.get_setting("ledger_page_size")),
            "tiers_enabled": self.store.get_setting("tiers_enabled") == "1",
            "show_top_donors": self.store.get_setting("show_top_donors", "1") != "0",
            "chat_extra": self.store.get_setting("chat_extra"),
            "goal_tiers": [tier.as_dict() for tier in load_tiers(self.store.get_setting("goal_tiers"))],
            "stream_started_at": self.store.get_setting("stream_started_at") or None,
            "stream_title": self.store.get_setting("stream_title"),
            "fx": rate_status(self.store),
            "google_redirect_uri": self.google_redirect(),
            "kick_redirect_uri": self.kick_redirect(),
            "kick_webhook_hint": self.service.kick.webhook_hint(),
        }


    def _player(self, platform: str) -> dict:
        """Embed URL from the open ledger. Pause does not block this."""
        youtube = self.service.youtube
        rumble = self.service.rumble
        return player_for_ledger(
            platform,
            {
                "youtube_watch": self.store.get_setting("youtube_watch"),
                "youtube_channel": self.store.get_setting("youtube_channel"),
                "youtube_follow_live": self.store.get_setting("youtube_follow_live"),
                "rumble_watch": self.store.get_setting("rumble_watch"),
                "rumble_channel": self.store.get_setting("rumble_channel"),
                "rumble_follow_live": self.store.get_setting("rumble_follow_live"),
                "kick_watch": self.store.get_setting("kick_watch"),
            },
            youtube_video=youtube.public_video_id,
            youtube_live=youtube.public_live_video_id,
            rumble_chat=rumble.resolve_chat_id,
            rumble_live=rumble.resolve_live_channel,
        )

    def _rates_and_price(self) -> tuple[dict[str, float], float | None]:
        return usd_rates(self.store), parse_membership_price(self.store.get_setting("membership_price"))


def _object(body: bytes) -> dict:
    if not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Body was not JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Body must be a JSON object.")
    return payload


def _one(query: dict[str, list[str]], name: str, default: str = "") -> str:
    values = query.get(name) or []
    return values[0] if values else default


def _whole_number(query: dict[str, list[str]], name: str) -> int:
    raw = _one(query, name, "0").strip()
    if not raw.isdigit() or len(raw) > 12:
        raise ValueError(f"{name} must be a whole number.")
    return int(raw)


def _donation_filters(query: dict[str, list[str]]) -> dict:
    platform = _one(query, "platform")
    kind = _one(query, "kind")
    processed = _one(query, "processed")
    if platform and platform not in PLATFORM_LABELS:
        raise ValueError("Unknown platform.")
    if kind and kind not in KIND_LABELS:
        raise ValueError("Unknown kind.")
    if processed not in {"", "0", "1"}:
        raise ValueError("processed must be 0 or 1.")
    exclude = [item for item in (query.get("exclude_kind") or []) if item]
    for item in exclude:
        if item not in KIND_LABELS:
            raise ValueError("Unknown kind.")
    try:
        limit = int(_one(query, "limit", "100"))
        offset = int(_one(query, "offset", "0"))
    except ValueError as exc:
        raise ValueError("limit and offset must be whole numbers.") from exc
    return {
        "platform": platform,
        "kind": kind,
        "processed": processed,
        "query": _one(query, "q"),
        "exclude_kinds": exclude,
        "include_hidden": _one(query, "include_hidden") in {"1", "true"},
        "limit": limit,
        "offset": offset,
    }


def _export_filters(query: dict[str, list[str]]) -> dict:
    filters = _donation_filters(query)
    filters.pop("limit", None)
    filters.pop("offset", None)
    filters.pop("include_hidden", None)
    return filters


_SECURITY_HEADERS = [
    (
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; "
        "connect-src 'self'; frame-src "
        + " ".join(FRAME_SRC)
        + "; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    ),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Cache-Control", "no-store"),
]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "StreamDonations"

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def log_message(self, fmt: str, *args) -> None:
        return

    def _dispatch(self) -> None:
        host = (self.headers.get("Host") or "").split(":")[0].strip("[]").lower()
        if host not in {"127.0.0.1", "localhost"}:
            self._write(text_response(400, "Bad host", "text/plain; charset=utf-8"))
            return
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self._write(json_response(400, {"error": "Bad Content-Length"}))
            return
        if length < 0 or length > 1_000_000:
            self._write(json_response(413, {"error": "Body too large"}))
            return
        body = self.rfile.read(length) if length else b""
        response = self.server.app.handle(self.command, parsed.path, parse_qs(parsed.query), self.headers, body)  # type: ignore[attr-defined]
        self._write(response)

    def _write(self, response: Response) -> None:
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        for key, value in _SECURITY_HEADERS:
            self.send_header(key, value)
        for key, value in response.headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(response.body)


class WebhookHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "StreamDonations"

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/kick/webhook":
            self._plain(404, "Not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self._plain(400, "Bad Content-Length")
            return
        if length < 0 or length > 2_000_000:
            self._plain(413, "Body too large")
            return
        body = self.rfile.read(length) if length else b""
        status, detail = self.server.app.service.kick.ingest_webhook(dict(self.headers.items()), body)  # type: ignore[attr-defined]
        if status >= 400:
            self.server.app.store.log("kick", "error", detail)  # type: ignore[attr-defined]
        self._plain(status, detail if status >= 400 else "")

    def do_GET(self) -> None:
        self._plain(404, "Not found")

    def log_message(self, fmt: str, *args) -> None:
        return

    def _plain(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class ThreadingServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    app: App
