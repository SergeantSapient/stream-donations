"""Background polling and the settings the collectors share."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass

from .goals import dump_tiers, normalize_tiers
from .kick import KickClient
from .kick_public import validate_kick_watch
from .rumble import RumbleClient, validate_rumble_api_url, validate_rumble_watch
from .store import Store
from .transport import Transport
from .util import redact
from .youtube import YouTubeClient
from .youtube_public import validate_youtube_watch
from .youtube_quota import clamp_poll_seconds

_HOST_NAME = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
)
_ALLOWED_SETTINGS = {
    "youtube_client_id",
    "youtube_client_secret",
    "youtube_watch",
    "youtube_poll_seconds",
    "rumble_api_url",
    "rumble_watch",
    "rumble_poll_seconds",
    "kick_client_id",
    "kick_client_secret",
    "kick_watch",
    "kick_webhook_host",
    "kick_webhook_port",
    "kick_public_base",
    "membership_price",
    "tiers_enabled",
    "show_top_donors",
    "chat_extra",
    "goal_tiers",
}


@dataclass
class PendingAuth:
    provider: str
    verifier: str
    redirect_uri: str
    created: float


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class Service:
    def __init__(self, store: Store, transport: Transport | None = None):
        self.store = store
        shared = transport or Transport()
        self.youtube = YouTubeClient(store, shared)
        self.rumble = RumbleClient(store, shared)
        self.kick = KickClient(store, shared)
        self.on_listen_change = None
        self._stop = threading.Event()
        self._poll_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[str, PendingAuth] = {}
        self._threads: list[threading.Thread] = []
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._loop, args=("youtube", self.youtube.tick), name="youtube", daemon=True),
            threading.Thread(target=self._loop, args=("rumble", self.rumble.tick), name="rumble", daemon=True),
            threading.Thread(target=self._loop, args=("kick", self.kick.tick), name="kick", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        threading.Thread(target=self.kick.refresh_public_key, name="kick-key", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2)

    def _loop(self, name: str, tick) -> None:
        while not self._stop.is_set():
            try:
                with self._poll_lock:
                    tick()
            except Exception as exc:
                message = redact(str(exc))
                self.store.log(name, "error", message)
                self._stop.wait(15)
                continue
            self._stop.wait(1)

    def poll_now(self) -> list[str]:
        errors = []
        with self._poll_lock:
            for name, client in (
                ("youtube", self.youtube),
                ("rumble", self.rumble),
                ("kick", self.kick),
            ):
                try:
                    client.tick(force=True)
                except Exception as exc:
                    message = redact(str(exc))
                    errors.append(f"{name}: {message}")
                    self.store.log(name, "error", message)
        return errors

    def begin_oauth(self, provider: str, redirect_uri: str) -> str:
        if provider == "youtube" and not self.youtube.client_saved():
            raise ValueError("Save the YouTube client id and secret first.")
        if provider == "kick" and not self.kick.client_saved():
            raise ValueError("Save the Kick client id and secret first.")
        if provider not in {"youtube", "kick"}:
            raise ValueError("unknown provider")
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(24)
        with self._pending_lock:
            self._purge_pending()
            self._pending[state] = PendingAuth(provider, verifier, redirect_uri, time.time())
        if provider == "youtube":
            return self.youtube.authorization_url(redirect_uri, state, challenge)
        return self.kick.authorization_url(redirect_uri, state, challenge)

    def finish_oauth(self, provider: str, code: str, state: str) -> None:
        pending = self._pop_pending(state, provider)
        if pending is None:
            raise ValueError("This sign-in expired or was already used. Start it again from the app.")
        if not code:
            raise ValueError("The provider did not return an authorization code.")
        if provider == "youtube":
            self.youtube.exchange_code(code, pending.redirect_uri, pending.verifier)
            self.youtube.tick(force=True)
            return
        if provider == "kick":
            self.kick.exchange_code(code, pending.redirect_uri, pending.verifier)
            self.kick.tick(force=True)
            return
        raise ValueError("unknown provider")

    def apply_settings(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise ValueError("Settings must be an object.")
        unknown = sorted(set(payload) - _ALLOWED_SETTINGS)
        if unknown:
            raise ValueError("Unknown settings: " + ", ".join(unknown))
        updates: dict[str, str] = {}
        clears: list[str] = []
        webhook_changed = False
        for key, value in payload.items():
            if key == "youtube_watch":
                text = str(value or "").strip()
                if text:
                    updates[key] = validate_youtube_watch(text)
                else:
                    clears.append(key)
            elif key in {"youtube_poll_seconds"}:
                updates[key] = _youtube_poll_seconds(value)
            elif key == "rumble_poll_seconds":
                updates[key] = _bounded_int(value, 5, 120, "Rumble poll seconds")
            elif key == "kick_webhook_port":
                updates[key] = _bounded_int(value, 1, 65535, "Kick webhook port")
                webhook_changed = True
            elif key == "kick_webhook_host":
                host = str(value or "").strip()
                if not _valid_host(host):
                    raise ValueError("Kick listen host must be an IP address or hostname.")
                updates[key] = host
                webhook_changed = True
            elif key == "rumble_api_url":
                text = str(value or "").strip()
                if text:
                    updates[key] = validate_rumble_api_url(text)
                else:
                    clears.append(key)
            elif key == "rumble_watch":
                text = str(value or "").strip()
                if text:
                    updates[key] = validate_rumble_watch(text)
                else:
                    clears.append(key)
            elif key == "kick_watch":
                text = str(value or "").strip()
                if text:
                    updates[key] = validate_kick_watch(text)
                else:
                    clears.append(key)
            elif key == "membership_price":
                text = str(value or "").strip()
                if text:
                    updates[key] = _bounded_money(text)
                else:
                    clears.append(key)
            elif key in {"tiers_enabled", "show_top_donors"}:
                updates[key] = "1" if _flag(value) else "0"
            elif key == "chat_extra":
                text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
                if len(text) > 500:
                    raise ValueError("Extra chat note is limited to 500 characters.")
                updates[key] = text
            elif key == "goal_tiers":
                if not isinstance(value, list):
                    raise ValueError("Goal tiers must be a list.")
                updates[key] = dump_tiers(normalize_tiers(value))
            elif key == "kick_public_base":
                text = str(value or "").strip()
                if text and not _valid_public_base(text):
                    raise ValueError("Kick public base must be an http(s) origin, with no query string.")
                if text:
                    updates[key] = text.rstrip("/")
                else:
                    clears.append(key)
            else:
                text = str(value or "").strip()
                if text:
                    updates[key] = text
                else:
                    clears.append(key)
        for key, value in updates.items():
            self.store.set_setting(key, value)
        for key in clears:
            self.store.delete_setting(key)
        if webhook_changed and self.on_listen_change is not None:
            self.on_listen_change()
        # Pick up new credentials on the next loop instead of waiting out the interval.
        self.youtube._next_super = 0
        self.youtube._next_broadcast = 0
        self.rumble._next_poll = 0
        self.kick._next_status = 0

    def _pop_pending(self, state: str, provider: str) -> PendingAuth | None:
        with self._pending_lock:
            self._purge_pending()
            pending = self._pending.pop(state, None)
        if pending is None or pending.provider != provider:
            return None
        return pending

    def _purge_pending(self) -> None:
        cutoff = time.time() - 600
        expired = [key for key, item in self._pending.items() if item.created < cutoff]
        for key in expired:
            del self._pending[key]


def _flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _bounded_money(value: str) -> str:
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError("Membership price must be a dollar amount.") from exc
    if number < 0 or number > 100000:
        raise ValueError("Membership price must be between 0 and 100000.")
    return f"{number:.2f}"


def _youtube_poll_seconds(value: object) -> str:
    if isinstance(value, bool) or value is None or isinstance(value, float):
        raise ValueError("YouTube poll seconds must be a whole number.")
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("YouTube poll seconds must be a whole number.") from exc
    # A saved 30 or 90 second interval is raised, not rejected, so an open
    # settings form from before this floor still saves the rest of the form.
    return str(clamp_poll_seconds(number))


def _bounded_int(value: object, low: int, high: int, label: str) -> str:
    if isinstance(value, bool) or value is None or isinstance(value, float):
        raise ValueError(f"{label} must be a whole number.")
    try:
        number = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a whole number.") from exc
    if number < low or number > high:
        raise ValueError(f"{label} must be between {low} and {high}.")
    return str(number)


def _valid_host(value: str) -> bool:
    if value in {"localhost", "0.0.0.0"}:
        return True
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", value):
        return all(0 <= int(part) <= 255 for part in value.split("."))
    return bool(_HOST_NAME.fullmatch(value)) and len(value) <= 253


def _valid_public_base(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    path = parsed.path or ""
    return path in {"", "/"} or path.rstrip("/") == "/kick/webhook"
