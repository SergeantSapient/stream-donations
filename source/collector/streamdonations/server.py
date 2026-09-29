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

from .fx import rate_status, refresh_rates, usd_rates
from .kinds import KIND_LABELS, PLATFORM_LABELS
from .goals import load_tiers
from .money import DISPLAY_LABELS, parse_membership_price
from .service import Service
from .store import Store
from .util import ApiError, redact, utc_now_iso
from .youtube import open_browser

WEB_DIR = Path(__file__).resolve().parent / "web"
_DONATION_PATH = re.compile(r"/api/donations/(\d+)")


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

    def start(self) -> None:
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

    def handle(self, method: str, path: str, query: dict[str, list[str]], headers, body: bytes) -> Response:
        try:
            if path == "/boot" and method == "GET":
                return self._boot(query)
            if path in {"/oauth/google/callback", "/oauth/kick/callback"} and method == "GET":
                provider = "youtube" if "google" in path else "kick"
                return self._oauth_callback(provider, query)
            if not self._authorized(headers):
                return json_response(401, {"error": "Sign in to this local app again."})
            if method == "GET" and path in {"/", "/index.html"}:
                return self._file("index.html", "text/html; charset=utf-8")
            if method == "GET" and path == "/app.js":
                return self._file("app.js", "text/javascript; charset=utf-8")
            if method == "GET" and path == "/styles.css":
                return self._file("styles.css", "text/css; charset=utf-8")
            if path.startswith("/api/"):
                return self._api(method, path, query, body)
            return json_response(404, {"error": "Not found"})
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
        if path == "/api/status" and method == "GET":
            return json_response(200, {"platforms": self.store.status(), "log": self.store.recent_log()})
        if path == "/api/settings" and method == "GET":
            return json_response(200, self._public_settings())
        if path == "/api/settings" and method == "PUT":
            self.service.apply_settings(_object(body))
            return json_response(200, self._public_settings())
        if path == "/api/stream" and method == "GET":
            rates, price = self._rates_and_price()
            return json_response(200, self.store.stream_report(rates, price))
        if path == "/api/stream" and method == "POST":
            action = str(_object(body).get("action") or "").strip().lower()
            if action == "start":
                self.store.set_stream_started(utc_now_iso())
                self.store.log("ledger", "info", "Stream total now counts donations from this moment. The ledger was kept.")
            elif action == "full":
                self.store.set_stream_started(None)
                self.store.log("ledger", "info", "Stream total counts the full ledger again.")
            else:
                raise ValueError("Stream action must be start or full.")
            rates, price = self._rates_and_price()
            return json_response(200, self.store.stream_report(rates, price))
        if path == "/api/donations" and method == "POST":
            payload = _object(body)
            donation_id = self.store.add_manual_entry(
                author_name=payload.get("author_name"),
                platform=payload.get("platform"),
                kind=payload.get("kind"),
                amount=payload.get("amount"),
                message=payload.get("message", ""),
                created_at=payload.get("created_at", ""),
            )
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
            self.store.log("ledger", "info", f"Ledger reset. Removed {removed} donation(s). Settings were kept.")
            return json_response(200, {"removed": removed})
        if path == "/api/donations" and method == "GET":
            filters = _donation_filters(query)
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
            processed = payload.get("processed", None)
            note = payload.get("note", None)
            if processed is not None and not isinstance(processed, bool):
                raise ValueError("processed must be true or false.")
            if note is not None:
                if not isinstance(note, str):
                    raise ValueError("note must be text.")
                if len(note) > 2000:
                    raise ValueError("note is limited to 2000 characters.")
            updated = self.store.update_donation(int(match.group(1)), processed=processed, note=note)
            if updated is None:
                return json_response(404, {"error": "Donation not found"})
            rates, price = self._rates_and_price()
            updated = self.store.get_donation(int(match.group(1)), membership_price=price, rates=rates)
            return json_response(200, updated)
        if path == "/api/export.csv" and method == "GET":
            rates, price = self._rates_and_price()
            text = self.store.export_csv(rates=rates, membership_price=price, **_export_filters(query))
            return text_response(
                200,
                text,
                "text/csv; charset=utf-8",
                [("Content-Disposition", 'attachment; filename="donations.csv"')],
            )
        if path == "/api/export.jsonl" and method == "GET":
            text = self.store.export_jsonl(**_export_filters(query))
            return text_response(
                200,
                text,
                "application/x-ndjson; charset=utf-8",
                [("Content-Disposition", 'attachment; filename="donations.jsonl"')],
            )
        if path == "/api/poll" and method == "POST":
            errors = self.service.poll_now()
            return json_response(200, {"ok": not errors, "errors": errors, "platforms": self.store.status()})
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
        return self._oauth_page(f"{label} connected", "You can close this tab and return to Stream Donations.")

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

    def _authorized(self, headers) -> bool:
        header = headers.get("Authorization", "")
        if header.startswith("Bearer ") and same_secret(header[7:].strip(), self.token):
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
            "platforms": [{"id": key, "label": value} for key, value in PLATFORM_LABELS.items()],
            "kinds": [{"id": key, "label": value} for key, value in DISPLAY_LABELS.items()],
            "default_exclude_kinds": ["gift_membership_received"],
            "database": str(self.store.path),
            "google_redirect_uri": self.google_redirect(),
            "kick_redirect_uri": self.kick_redirect(),
            "kick_webhook_hint": self.service.kick.webhook_hint(),
            "scopes": {
                "youtube": "https://www.googleapis.com/auth/youtube.readonly",
                "kick": "user:read channel:read events:subscribe kicks:read",
            },
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
            "youtube_poll_seconds": seconds("youtube_poll_seconds", "90"),
            "youtube_watch": self.store.get_setting("youtube_watch"),
            "rumble_api_url_set": bool(self.store.get_setting("rumble_api_url")),
            "rumble_watch": self.store.get_setting("rumble_watch"),
            "rumble_poll_seconds": seconds("rumble_poll_seconds", "8"),
            "kick_watch": self.store.get_setting("kick_watch"),
            "kick_client_id": self.store.get_setting("kick_client_id"),
            "kick_client_secret_set": bool(self.store.get_setting("kick_client_secret")),
            "kick_connected": bool(self.store.get_setting("kick_refresh_token")),
            "kick_webhook_host": self.store.get_setting("kick_webhook_host", "127.0.0.1") or "127.0.0.1",
            "kick_webhook_port": seconds("kick_webhook_port", "47322"),
            "kick_public_base": self.store.get_setting("kick_public_base"),
            "membership_price": self.store.get_setting("membership_price"),
            "tiers_enabled": self.store.get_setting("tiers_enabled") == "1",
            "show_top_donors": self.store.get_setting("show_top_donors", "1") != "0",
            "chat_extra": self.store.get_setting("chat_extra"),
            "goal_tiers": [tier.as_dict() for tier in load_tiers(self.store.get_setting("goal_tiers"))],
            "stream_started_at": self.store.get_setting("stream_started_at") or None,
            "fx": rate_status(self.store),
            "google_redirect_uri": self.google_redirect(),
            "kick_redirect_uri": self.kick_redirect(),
            "kick_webhook_hint": self.service.kick.webhook_hint(),
        }


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
        "limit": limit,
        "offset": offset,
    }


def _export_filters(query: dict[str, list[str]]) -> dict:
    filters = _donation_filters(query)
    filters.pop("limit", None)
    filters.pop("offset", None)
    return filters


_SECURITY_HEADERS = [
    (
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
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
