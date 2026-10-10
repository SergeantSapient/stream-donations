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

from .blue_host import BlueHost
from .chat_feed import ChatFeed
from .giveaway import Giveaway
from .goals import dump_tiers, normalize_tiers
from .kick import KickClient
from .kick_public import kick_popup_chat_url, kick_slug, validate_kick_watch
from .rumble import (
    RumbleClient,
    rumble_popup_chat_url,
    validate_rumble_api_url,
    validate_rumble_channel,
    validate_rumble_watch,
)
from .store import Store
from .transport import Transport
from .util import ApiError, AuthError, collapsed_chat_text, redact
from .youtube import YouTubeClient
from .youtube_public import validate_youtube_channel, validate_youtube_watch, youtube_popup_chat_url
from .youtube_quota import QuotaPaused, clamp_poll_seconds

_CHAT_PLATFORMS = ("youtube", "kick")

_HOST_NAME = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
)
_ALLOWED_SETTINGS = {
    "youtube_client_id",
    "youtube_client_secret",
    "youtube_watch",
    "youtube_channel",
    "youtube_follow_live",
    "youtube_poll_seconds",
    "rumble_api_url",
    "rumble_watch",
    "rumble_channel",
    "rumble_follow_live",
    "rumble_poll_seconds",
    "kick_client_id",
    "kick_client_secret",
    "kick_watch",
    "kick_follow_live",
    "kick_webhook_host",
    "kick_webhook_port",
    "kick_public_base",
    "membership_price",
    "ledger_page_size",
    "tiers_enabled",
    "show_top_donors",
    "chat_extra",
    "goal_tiers",
    "blue_script_id",
}

PAUSED_DETAIL = (
    "Collection is paused. Credentials and watch URLs were kept. "
    "No API requests or live chat reading until you reconnect."
)


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


class LedgerView:
    """Settings and inserts for one ledger that is not the one on screen.

    Public chat for that ledger keeps running. The open ledger's status line
    and its chat panel stay on the ledger the user is looking at.
    """

    def __init__(self, store: Store, tab_id: str):
        self._store = store
        self.tab_id = tab_id
        self._fields: dict[str, str] = {}

    def refresh(self) -> bool:
        for tab in self._store.ledger_snapshots():
            if tab["id"] != self.tab_id:
                continue
            self._fields = tab
            return tab.get("collection_paused") == "0"
        return False

    def collection_paused(self) -> bool:
        return self._fields.get("collection_paused") != "0"

    def get_setting(self, key: str, default: str = "") -> str:
        if key in self._fields:
            value = self._fields.get(key) or ""
            return value or default
        # The owner Rumble URL is one shared account. A background ledger
        # reads that channel's public chat, not the owner API.
        if key == "rumble_api_url":
            return ""
        return self._store.get_setting(key, default)

    def set_status(self, *_args, **_kwargs) -> None:
        return None

    def add_many(self, donations) -> int:
        return self._store.add_many(donations, stream_id=self.tab_id)

    def log(self, platform: str, level: str, message: str) -> None:
        title = (self._fields.get("stream_title") or "").strip() or "Stream"
        self._store.log(platform, level, f"{message} ({title})")

    def __getattr__(self, name: str):
        return getattr(self._store, name)


class _OtherLedger:
    def __init__(self, view: LedgerView, transport: Transport):
        self.view = view
        self.youtube = YouTubeClient(view, transport, chat_feed=None)
        self.rumble = RumbleClient(view, transport, chat_feed=None)
        self.kick = KickClient(view, transport, chat_feed=None)

    def rest(self) -> None:
        self.youtube.rest()
        self.rumble.rest()
        self.kick.rest()

    def tick(self) -> None:
        self.youtube.tick(public_only=True)
        self.rumble.tick(public_only=True)
        self.kick.tick(public_only=True)


def _ledger_has_target(tab: dict) -> bool:
    if tab.get("youtube_follow_live") == "1" and tab.get("youtube_channel"):
        return True
    if tab.get("youtube_watch"):
        return True
    if tab.get("rumble_follow_live") == "1" and tab.get("rumble_channel"):
        return True
    if tab.get("rumble_watch"):
        return True
    return bool(tab.get("kick_watch"))


class Service:
    def __init__(self, store: Store, transport: Transport | None = None):
        self.store = store
        shared = transport or Transport()
        self._transport = shared
        self.chat = ChatFeed()
        self.giveaway = Giveaway()
        self.chat.on_lines = self.giveaway.observe
        self.youtube = YouTubeClient(store, shared, chat_feed=self.chat)
        self.rumble = RumbleClient(store, shared, chat_feed=self.chat)
        self.kick = KickClient(store, shared, chat_feed=self.chat)
        self.blue = BlueHost(self)
        self.store.on_fresh_donations = self.blue.on_donations
        self._others: dict[str, _OtherLedger] = {}
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
        if self.paused():
            self._mark_paused()
            return
        threading.Thread(target=self.kick.refresh_public_key, name="kick-key", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2)
        with self._poll_lock:
            self._drop_other_ledgers(set())

    def _loop(self, name: str, tick) -> None:
        while not self._stop.is_set():
            try:
                with self._poll_lock:
                    try:
                        tick()
                    finally:
                        if name == "youtube":
                            self._sync_other_ledgers()
                            try:
                                self.blue.tick()
                            except Exception:
                                self.store.log("blue", "error", "BLUE could not check the timer.")
            except Exception as exc:
                message = redact(str(exc))
                self.store.log(name, "error", message)
                self._stop.wait(15)
                continue
            self._stop.wait(1)

    def paused(self) -> bool:
        return self.store.collection_paused()

    def chat_destinations(self) -> dict:
        """Whether the signed-in YouTube and Kick accounts can post.

        Names are cached on the client. This does not look up a pop-out chat.
        """
        return {
            "youtube": self._chat_destination(
                self.youtube,
                "Connect a YouTube account to send chat.",
                "Reconnect YouTube to allow chat. The current sign-in can only read.",
            ),
            "kick": self._chat_destination(
                self.kick,
                "Connect a Kick account to send chat.",
                "Reconnect Kick to allow chat. The current sign-in cannot post.",
            ),
        }

    def send_chat(self, text: object, platforms: object, stream_id: object = None) -> dict:
        """Post one line as the signed-in account. One failure does not skip the other."""
        if isinstance(platforms, str) or not isinstance(platforms, list):
            raise ValueError("Choose YouTube, Kick, or both.")
        chosen: list[str] = []
        for item in platforms:
            name = str(item or "").strip().lower()
            if name == "rumble":
                # The desktop window posts into the signed-in Rumble chat. This process does not.
                raise ValueError("Rumble chat opens in its own window.")
            if name not in _CHAT_PLATFORMS:
                raise ValueError("Choose YouTube, Kick, or both.")
            if name not in chosen:
                chosen.append(name)
        if not chosen:
            raise ValueError("Choose YouTube, Kick, or both.")
        tab = self._chat_tab(stream_id)
        if tab is not None and str(tab.get("collection_paused") or "") != "0":
            return {
                "results": [
                    {"platform": name, "ok": False, "error": "That ledger is disconnected."}
                    for name in chosen
                ]
            }
        message = collapsed_chat_text(text)
        return {"results": [self._send_chat_one(name, message, tab) for name in chosen]}

    def rumble_popup(self, stream_id: str | None = None) -> dict:
        """Pop-out URL for one ledger. Resolved when that button, or a BLUE line, is used."""
        tab = None
        if stream_id:
            tab = self._ledger_tab(stream_id)
            if tab is None:
                raise ValueError("Unknown stream tab.")
            if str(tab.get("collection_paused") or "") != "0":
                raise ValueError("That ledger is disconnected.")
        try:
            chat_id = self._rumble_chat_id(tab)
            url = rumble_popup_chat_url(chat_id)
        except ApiError:
            raise ApiError("That Rumble chat could not be opened.")
        return {"url": url}

    def youtube_popup(self) -> dict:
        """Pop-out URL for the open ledger. Resolved when that button is used."""
        try:
            video_id = self._youtube_popup_video_id()
            url = youtube_popup_chat_url(video_id)
        except ApiError:
            raise ApiError("That YouTube chat could not be opened.")
        return {"url": url}

    def kick_popup(self) -> dict:
        """Pop-out URL for the open ledger's Kick channel. No live lookup."""
        watch = self.store.get_setting("kick_watch").strip()
        if not watch:
            raise ValueError("This ledger has no Kick channel.")
        slug = kick_slug(watch)
        if not slug:
            raise ValueError("This ledger has no Kick channel.")
        return {"url": kick_popup_chat_url(slug)}

    def _youtube_popup_video_id(self) -> str:
        if self.store.get_setting("youtube_follow_live") == "1":
            channel = self.store.get_setting("youtube_channel").strip()
            if not channel:
                raise ValueError("This ledger has no YouTube channel.")
            video_id = self.youtube.public_live_video_id(channel)
            if not video_id:
                raise ValueError("That YouTube channel has no chat open.")
            return str(video_id)
        watch = self.store.get_setting("youtube_watch").strip()
        if not watch:
            raise ValueError("This ledger has no YouTube video.")
        return self.youtube.public_video_id(watch)

    def _chat_destination(self, client, connect_reason: str, scope_reason: str) -> dict:
        connected = bool(client.connected())
        can_send = bool(client.can_send()) if connected else False
        name = ""
        if connected:
            try:
                name = client.sender_name() or ""
            except (QuotaPaused, ApiError, AuthError, ValueError):
                name = ""
        if not connected:
            reason = connect_reason
        elif not can_send:
            reason = scope_reason
        else:
            reason = ""
        return {"connected": connected, "can_send": can_send, "name": name, "reason": reason}

    def _send_chat_one(self, platform: str, message: str, tab: dict | None = None) -> dict:
        try:
            if platform == "youtube":
                if tab is None:
                    self.youtube.send_message(message)
                else:
                    self.youtube.send_message(message, video_id=self._youtube_video_for_tab(tab))
            elif tab is None:
                self.kick.send_message(message)
            else:
                slug = kick_slug(str(tab.get("kick_watch") or ""))
                if not slug:
                    raise ValueError("This ledger has no Kick channel.")
                self.kick.send_message(message, slug=slug)
        except (QuotaPaused, ApiError, AuthError, ValueError) as exc:
            detail = str(exc).strip() or "Chat was not sent."
            self.store.log(platform, "info", "Chat was not sent. " + detail)
            return {"platform": platform, "ok": False, "error": detail}
        self.store.log(platform, "info", "Sent a chat message.")
        return {"platform": platform, "ok": True}

    def _rumble_chat_id(self, tab: dict | None = None) -> str:
        def field(key: str) -> str:
            if tab is None:
                return self.store.get_setting(key).strip()
            return str(tab.get(key) or "").strip()

        if field("rumble_follow_live") == "1":
            channel = field("rumble_channel")
            if not channel:
                raise ValueError("This ledger has no Rumble channel.")
            chat_id = self.rumble.resolve_live_channel(channel)
            if not chat_id:
                raise ValueError("That Rumble channel has no chat open.")
            return str(chat_id)
        watch = field("rumble_watch")
        if not watch:
            raise ValueError("This ledger has no Rumble video.")
        return self.rumble.resolve_chat_id(watch)

    def _youtube_video_for_tab(self, tab: dict) -> str:
        if str(tab.get("youtube_follow_live") or "") == "1":
            channel = str(tab.get("youtube_channel") or "").strip()
            if not channel:
                raise ApiError("This ledger has no YouTube channel.")
            video_id = self.youtube.public_live_video_id(channel)
            if not video_id:
                raise ApiError("That YouTube channel is not live, so there is no chat to post into.")
            return str(video_id).strip()
        watch = str(tab.get("youtube_watch") or "").strip()
        if not watch:
            raise ApiError("This ledger has no YouTube video.")
        from .youtube_public import video_id_from_target

        found = video_id_from_target(watch)
        if found:
            return found
        return self.youtube.public_video_id(watch)

    def _chat_tab(self, stream_id: object) -> dict | None:
        if stream_id is None:
            return None
        if not isinstance(stream_id, str):
            raise ValueError("Unknown stream tab.")
        text = stream_id.strip()
        if not text:
            return None
        tab = self._ledger_tab(text)
        if tab is None:
            raise ValueError("Unknown stream tab.")
        return tab

    def _ledger_tab(self, stream_id: str) -> dict | None:
        for tab in self.store.ledger_snapshots():
            if tab["id"] == stream_id:
                return tab
        return None

    def set_paused(self, paused: bool, tab_id: str | None = None) -> None:
        """Stop or resume one ledger without signing out.

        Every connected ledger keeps collecting. Pausing one leaves the others running.
        """
        with self._poll_lock:
            # Read this before the ledger document is opened. Migration writes
            # "1", which would hide the first pause and skip stopping chat.
            already = self.paused()
            active_before = self.store._active_stream_id()
            wanted = tab_id.strip() if isinstance(tab_id, str) and tab_id.strip() else active_before
            previous = self._ledger_tab(wanted)
            was_paused = previous is None or str(previous.get("collection_paused") or "") != "0"
            target = self.store.set_stream_paused(tab_id, paused)
            if bool(paused) != was_paused:
                self.blue.on_connection(target, bool(paused))
            active = self.store._active_stream_id()
            if target != active:
                self._sync_other_ledgers()
                self.store.log(
                    "ledger",
                    "info",
                    "Paused that ledger. It stays disconnected until you reconnect it."
                    if paused
                    else "That ledger is collecting, including while another ledger is open.",
                )
                return
            if paused:
                self._rest_platforms()
                self._sync_other_ledgers()
                if not already:
                    self.store.log(
                        "ledger",
                        "info",
                        "Soft disconnect. This ledger's API requests and live chat reading are paused. "
                        "Sign-in and watch URLs were kept.",
                    )
                return
            if not already:
                self._sync_other_ledgers()
                return
            self.store.log("ledger", "info", "Reconnected. This ledger is collecting again.")
            self._resume_platforms()
            self._sync_other_ledgers()

    def follow_open_ledger(self) -> None:
        """Point the on-screen collectors at the open ledger. Other connected ledgers stay up."""
        with self._poll_lock:
            if self.paused():
                self._rest_platforms()
            else:
                self._resume_platforms()
            self._sync_other_ledgers()

    def _sync_other_ledgers(self) -> None:
        """Public chat for every connected ledger except the one on screen."""
        open_id = self.store._active_stream_id()
        wanted: set[str] = set()
        for tab in self.store.ledger_snapshots():
            if tab["id"] == open_id or tab.get("collection_paused") != "0":
                continue
            if _ledger_has_target(tab):
                wanted.add(tab["id"])
        self._drop_other_ledgers(wanted)
        for tab_id in wanted:
            bundle = self._others.get(tab_id)
            if bundle is None:
                bundle = self._make_other(tab_id)
                self._others[tab_id] = bundle
            if not bundle.view.refresh():
                self._others.pop(tab_id, None)
                bundle.rest()
                continue
            try:
                bundle.tick()
            except Exception as exc:
                self.store.log("ledger", "error", redact(str(exc)))

    def _make_other(self, tab_id: str) -> _OtherLedger:
        view = LedgerView(self.store, tab_id)
        view.refresh()
        return _OtherLedger(view, self._transport)

    def _drop_other_ledgers(self, keep: set[str]) -> None:
        for tab_id in list(self._others):
            if tab_id in keep:
                continue
            self._others.pop(tab_id).rest()

    def _rest_platforms(self) -> None:
        self.youtube.rest()
        self.rumble.rest()
        self.kick.rest()
        self._mark_paused()

    def _resume_platforms(self) -> None:
        self.youtube._next_super = 0
        self.youtube._next_broadcast = 0
        self.youtube._next_chat = 0
        self.rumble._next_poll = 0
        self.kick._next_status = 0
        for name, client in (
            ("youtube", self.youtube),
            ("rumble", self.rumble),
            ("kick", self.kick),
        ):
            try:
                client.tick()
            except Exception as exc:
                self.store.log(name, "error", redact(str(exc)))

    def _mark_paused(self) -> None:
        for name in ("youtube", "rumble", "kick"):
            self.store.set_status(name, "idle", PAUSED_DETAIL)

    def poll_now(self) -> list[str]:
        errors = []
        with self._poll_lock:
            if self.paused():
                return ["Collection is paused. Reconnect to send requests again."]
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
        previous_script = self.store.get_setting("blue_script_id") if "blue_script_id" in payload else ""
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
            elif key == "youtube_channel":
                text = str(value or "").strip()
                if text:
                    updates[key] = validate_youtube_channel(text)
                else:
                    clears.append(key)
            elif key == "youtube_follow_live":
                updates[key] = "1" if _flag(value) else "0"
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
            elif key == "rumble_channel":
                text = str(value or "").strip()
                if text:
                    updates[key] = validate_rumble_channel(text)
                else:
                    clears.append(key)
            elif key in {"rumble_follow_live", "kick_follow_live"}:
                updates[key] = "1" if _flag(value) else "0"
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
            elif key == "ledger_page_size":
                updates[key] = _bounded_int(value, 1, 500, "Ledger page size")
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
            elif key == "blue_script_id":
                script_id = str(value or "").strip()
                if script_id and not any(script["id"] == script_id for script in self.store.blue_scripts()):
                    raise ValueError("That BLUE script is not saved.")
                if script_id:
                    updates[key] = script_id
                else:
                    clears.append(key)
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
        if "blue_script_id" in payload:
            self.blue.note_script(self.store._active_stream_id(), previous_script)
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


def displayed_ledger_page_size(value: object) -> int:
    """Rows per ledger page. A missing or broken saved value is the old page of 100."""
    text = str(value or "").strip()
    if not text:
        return 100
    try:
        number = int(text)
    except ValueError:
        return 100
    if number < 1 or number > 500:
        return 100
    return number


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
