"""Kick official API: OAuth 2.1 and signed donation webhooks.

Subscriptions and Kicks are not available as a pull feed. Kick's events API
delivers them to the webhook URL configured on the developer app:
    POST https://api.kick.com/public/v1/events/subscriptions
    events: channel.subscription.new, channel.subscription.renewal,
            channel.subscription.gifts, kicks.gifted
The desktop app verifies Kick-Event-Signature before storing anything.
OAuth uses https://id.kick.com/oauth/authorize and /oauth/token with PKCE S256.
A sacrificial `redirect=127.0.0.1` parameter is sent before redirect_uri because
Kick's login page rewrites the first 127.0.0.1 it sees to localhost.
"""

from __future__ import annotations

import json
import threading
import time
import unicodedata
import urllib.parse

from .kick_sig import (
    EMBEDDED_KICK_PUBLIC_KEY,
    decode_signature,
    looks_like_public_key,
    signed_payload,
    verify_rsa_sha256,
    webhook_is_fresh,
)
from .kick_public import PublicKickChat, kick_slug
from .normalize import normalize_kick_event
from .store import Store
from .transport import HttpResult, Transport, api_error_message
from .util import ApiError, AuthError, collapsed_chat_text, display_donor, redact

AUTH_URL = "https://id.kick.com/oauth/authorize"
TOKEN_URL = "https://id.kick.com/oauth/token"
API_ROOT = "https://api.kick.com"
API_HOSTS = {"api.kick.com"}
TOKEN_HOSTS = {"id.kick.com"}
SCOPES = "user:read channel:read events:subscribe kicks:read chat:write"
DONATION_EVENTS = [
    {"name": "channel.subscription.new", "version": 1},
    {"name": "channel.subscription.renewal", "version": 1},
    {"name": "channel.subscription.gifts", "version": 1},
    {"name": "kicks.gifted", "version": 1},
]


class KickClient:
    def __init__(self, store: Store, transport: Transport | None = None, chat_feed=None):
        self.store = store
        self.transport = transport or Transport()
        self._auth_lock = threading.Lock()
        self._public_key = EMBEDDED_KICK_PUBLIC_KEY
        self._next_status = 0.0
        self._public = PublicKickChat(store, self.transport, chat_feed)

    @property
    def public_key(self) -> str:
        return self._public_key

    def set_public_key(self, pem: str) -> None:
        if looks_like_public_key(pem):
            self._public_key = pem

    def client_saved(self) -> bool:
        return bool(self.store.get_setting("kick_client_id") and self.store.get_setting("kick_client_secret"))

    def connected(self) -> bool:
        return self.client_saved() and bool(self.store.get_setting("kick_refresh_token"))

    def can_send(self) -> bool:
        granted = set(self.store.get_setting("kick_granted_scope").split())
        return self.connected() and "chat:write" in granted

    def sender_name(self) -> str:
        """Display name of the signed-in Kick account. The email is not stored."""
        cached = self.store.get_setting("kick_sender_name").strip()
        if cached or self.store.get_setting("kick_sender_checked") == "1":
            return cached
        if not self.connected():
            return ""
        self.store.set_setting("kick_sender_checked", "1")
        try:
            payload = self._get_json("/public/v1/users", self._access_token())
        except (ApiError, AuthError):
            return ""
        data = payload.get("data")
        row = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
        name = str(row.get("name") or "").strip()
        user_id = row.get("user_id")
        if name:
            self.store.set_setting("kick_sender_name", name)
        if isinstance(user_id, int) and not isinstance(user_id, bool) and user_id > 0:
            self.store.set_setting("kick_sender_id", str(user_id))
        return name

    def send_message(self, text: str, slug: str | None = None) -> None:
        """Post as the signed-in user into a ledger's Kick channel.

        Without slug, that ledger is the one open on screen.
        """
        if not self.connected():
            raise AuthError("Connect a Kick account to send chat.")
        if not self.can_send():
            raise AuthError("Reconnect Kick to allow chat. The current sign-in cannot post.")
        message = collapsed_chat_text(text)
        if len(message.encode("utf-8")) > 2048 or _grapheme_count(message) > 500:
            raise ValueError("Kick chat allows 500 characters.")
        slug = (slug or "").strip() or kick_slug(self.store.get_setting("kick_watch"))
        if not slug:
            raise ValueError("This ledger has no Kick channel.")
        token = self._access_token()
        broadcaster = self._broadcaster_user_id(token, slug)
        result = self._post_chat(token, broadcaster, message)
        if result.status == 401:
            self.store.delete_setting("kick_access_token")
            result = self._post_chat(self._access_token(), broadcaster, message)
        if result.status >= 400:
            raise ApiError(_kick_status_message(result.status), status=result.status)
        payload = result.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or data.get("is_sent") is False:
            raise ApiError("Kick did not accept that message.")

    def authorization_url(self, redirect_uri: str, state: str, challenge: str) -> str:
        # Order is part of the 127.0.0.1 workaround: `redirect` must come first.
        pairs = [
            ("response_type", "code"),
            ("client_id", self.store.get_setting("kick_client_id")),
            ("redirect", "127.0.0.1"),
            ("redirect_uri", redirect_uri),
            ("scope", SCOPES),
            ("code_challenge", challenge),
            ("code_challenge_method", "S256"),
            ("state", state),
        ]
        return AUTH_URL + "?" + urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote)

    def exchange_code(self, code: str, redirect_uri: str, verifier: str) -> None:
        with self._auth_lock:
            result = self.transport.request(
                "POST",
                TOKEN_URL,
                form={
                    "grant_type": "authorization_code",
                    "client_id": self.store.get_setting("kick_client_id"),
                    "client_secret": self.store.get_setting("kick_client_secret"),
                    "redirect_uri": redirect_uri,
                    "code": code,
                    "code_verifier": verifier,
                },
                allow_hosts=TOKEN_HOSTS,
            )
            self._store_token_result(result, granted_scope=SCOPES)

    def disconnect(self) -> None:
        with self._auth_lock:
            for key in (
                "kick_refresh_token",
                "kick_access_token",
                "kick_access_expiry",
                "kick_granted_scope",
                "kick_sender_name",
                "kick_sender_id",
                "kick_sender_checked",
            ):
                self.store.delete_setting(key)
        if self.store.get_setting("kick_watch"):
            self.store.set_status("kick", "idle", "Kick account disconnected. The public chat is unchanged.")
        else:
            self.store.set_status("kick", "unconfigured", "Disconnected.")

    def rest(self) -> None:
        self._public.rest()

    def tick(self, force: bool = False, public_only: bool = False) -> None:
        if self.store.collection_paused():
            self.rest()
            return
        self._public.ensure()
        if public_only:
            return
        has_watch = bool(self.store.get_setting("kick_watch"))
        if not self.client_saved():
            if has_watch:
                self._report_public()
                return
            self.store.set_status(
                "kick",
                "unconfigured",
                "Paste a public channel. The Kick app and webhooks are only for a channel you own.",
            )
            return
        if not self.connected():
            if has_watch:
                self._report_public()
                return
            self.store.set_status("kick", "idle", "Client saved. Connect Kick, then subscribe to donation events.")
            return
        now = time.monotonic()
        if not force and now < self._next_status:
            return
        self._next_status = time.monotonic() + 300
        try:
            self.refresh_public_key()
            names = self.subscription_names()
        except (ApiError, OSError, ValueError) as exc:
            message = redact(str(exc))
            self.store.set_status("kick", "error", message, error=message)
            self.store.log("kick", "error", message)
            return
        if names:
            detail = "Subscribed to " + ", ".join(names) + ". Waiting for signed webhooks."
            state = "ok"
        else:
            detail = "Connected, but no donation subscriptions yet. Click Subscribe after the webhook URL is reachable."
            state = "idle"
        listen = self.listen_label()
        detail += f" Listener: {listen}/kick/webhook."
        if listen.startswith("127.0.0.1") or listen.startswith("localhost"):
            detail += " Kick cannot deliver to loopback unless you forward this port or set the listen host to 0.0.0.0."
        if self._public.detail:
            detail += " " + self._public.detail
        if self._public.error:
            detail += " Public chat: " + self._public.error
            self.store.set_status("kick", "error", detail, error=self._public.error)
            return
        self.store.set_status("kick", state, detail)

    def _report_public(self) -> None:
        if self._public.error:
            self.store.set_status("kick", "error", self._public.error, error=self._public.error)
        elif self._public.detail:
            self.store.set_status("kick", "ok", self._public.detail)
        else:
            self.store.set_status("kick", "idle", "Opening the public Kick chat.")

    def listen_label(self) -> str:
        host = self.store.get_setting("kick_webhook_host", "127.0.0.1") or "127.0.0.1"
        port = self.store.get_setting("kick_webhook_port", "47322") or "47322"
        return f"{host}:{port}"

    def webhook_hint(self) -> str:
        public = self.store.get_setting("kick_public_base").rstrip("/")
        if public:
            return public + "/kick/webhook"
        return "http://" + self.listen_label() + "/kick/webhook"

    def refresh_public_key(self) -> None:
        if self.store.collection_paused():
            return
        try:
            result = self.transport.request(
                "GET",
                API_ROOT + "/public/v1/public-key",
                allow_hosts=API_HOSTS,
            )
        except (ApiError, OSError, ValueError):
            return
        if result.status != 200:
            return
        try:
            payload = result.json()
        except ApiError:
            return
        if not isinstance(payload, dict):
            return
        data = payload.get("data") or {}
        pem = data.get("public_key") if isinstance(data, dict) else ""
        if isinstance(pem, str) and looks_like_public_key(pem):
            self._public_key = pem

    def subscribe(self) -> dict:
        token = self._access_token()
        result = self.transport.request(
            "POST",
            API_ROOT + "/public/v1/events/subscriptions",
            headers={"Authorization": f"Bearer {token}"},
            json_body={"events": DONATION_EVENTS, "method": "webhook"},
            allow_hosts=API_HOSTS,
        )
        if result.status == 401:
            self.store.delete_setting("kick_access_token")
            token = self._access_token()
            result = self.transport.request(
                "POST",
                API_ROOT + "/public/v1/events/subscriptions",
                headers={"Authorization": f"Bearer {token}"},
                json_body={"events": DONATION_EVENTS, "method": "webhook"},
                allow_hosts=API_HOSTS,
            )
        if result.status >= 400:
            raise ApiError(api_error_message(result), status=result.status)
        payload = result.json()
        if not isinstance(payload, dict):
            raise ApiError("Kick subscribe response was not an object")
        rows = payload.get("data") or []
        errors = []
        names = []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if row.get("error"):
                    errors.append(f"{row.get('name')}: {row.get('error')}")
                elif row.get("name"):
                    names.append(str(row["name"]))
        if errors:
            self.store.log("kick", "error", "; ".join(errors))
        if names:
            self.store.log("kick", "info", "Subscribed to " + ", ".join(names) + ".")
        self._next_status = 0
        return payload

    def subscription_names(self) -> list[str]:
        token = self._access_token()
        result = self.transport.request(
            "GET",
            API_ROOT + "/public/v1/events/subscriptions",
            headers={"Authorization": f"Bearer {token}"},
            allow_hosts=API_HOSTS,
        )
        if result.status == 401:
            self.store.delete_setting("kick_access_token")
            token = self._access_token()
            result = self.transport.request(
                "GET",
                API_ROOT + "/public/v1/events/subscriptions",
                headers={"Authorization": f"Bearer {token}"},
                allow_hosts=API_HOSTS,
            )
        if result.status >= 400:
            raise ApiError(api_error_message(result), status=result.status)
        payload = result.json()
        names = []
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict) and row.get("event"):
                    names.append(str(row["event"]))
        wanted = {item["name"] for item in DONATION_EVENTS}
        return sorted(name for name in names if name in wanted)

    def ingest_webhook(self, headers: dict[str, str], body: bytes) -> tuple[int, str]:
        """Return (http status, detail). Valid events we do not store still get 204.

        Kick unsubscribes an app that fails the same event for about a day, so a
        signed follow or chat message is acknowledged and ignored.
        """
        lowered = {key.lower(): value for key, value in headers.items()}
        message_id = lowered.get("kick-event-message-id", "").strip()
        timestamp = lowered.get("kick-event-message-timestamp", "").strip()
        signature_text = lowered.get("kick-event-signature", "").strip()
        event_type = lowered.get("kick-event-type", "").strip()
        if not message_id or not timestamp or not signature_text or not event_type:
            return 400, "missing Kick event headers"
        try:
            signature = decode_signature(signature_text)
        except Exception:
            return 401, "signature could not be decoded"
        payload = signed_payload(message_id, timestamp, body)
        if not verify_rsa_sha256(self._public_key, payload, signature):
            return 401, "signature did not match Kick's public key"
        if not webhook_is_fresh(timestamp):
            return 400, "webhook timestamp is outside the acceptance window"
        # Acknowledge a valid event without storing it so Kick keeps the subscription.
        if self.store.collection_paused():
            return 204, "paused"
        try:
            parsed = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, "webhook body was not JSON"
        if not isinstance(parsed, dict):
            return 400, "webhook body was not an object"
        donation = normalize_kick_event(event_type, message_id, parsed)
        if donation is None:
            return 204, f"ignored {event_type}"
        inserted = self.store.add_many([donation])
        if inserted:
            self.store.log("kick", "info", f"Stored {event_type} from {display_donor(donation.author_name) or 'unknown'}.")
        return 204, "stored"

    def _access_token(self) -> str:
        with self._auth_lock:
            cached = self._fresh_access_token()
            if cached:
                return cached
            refresh = self.store.get_setting("kick_refresh_token")
            if not refresh:
                raise AuthError("Kick is not connected")
            result = self.transport.request(
                "POST",
                TOKEN_URL,
                form={
                    "grant_type": "refresh_token",
                    "client_id": self.store.get_setting("kick_client_id"),
                    "client_secret": self.store.get_setting("kick_client_secret"),
                    "refresh_token": refresh,
                },
                allow_hosts=TOKEN_HOSTS,
            )
            self._store_token_result(result, keep_refresh=refresh)
            token = self.store.get_setting("kick_access_token")
            if not token:
                raise AuthError("Kick token response had no access token")
            return token

    def _fresh_access_token(self) -> str:
        token = self.store.get_setting("kick_access_token")
        expiry = self.store.get_setting("kick_access_expiry")
        if not token or not expiry:
            return ""
        try:
            expires_at = float(expiry)
        except ValueError:
            return ""
        if time.time() >= expires_at - 60:
            return ""
        return token

    def _store_token_result(self, result, keep_refresh: str = "", granted_scope: str = "") -> None:
        if result.status >= 400:
            raise AuthError(api_error_message(result))
        payload = result.json()
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise AuthError("Kick token response was missing access_token")
        self.store.set_setting("kick_access_token", str(payload["access_token"]))
        expires_in = int(payload.get("expires_in") or 3600)
        self.store.set_setting("kick_access_expiry", str(time.time() + expires_in))
        if payload.get("refresh_token"):
            self.store.set_setting("kick_refresh_token", str(payload["refresh_token"]))
        elif keep_refresh:
            self.store.set_setting("kick_refresh_token", keep_refresh)
        scope = payload.get("scope")
        if isinstance(scope, str) and scope.strip():
            self.store.set_setting("kick_granted_scope", " ".join(scope.split()))
        elif granted_scope:
            self.store.set_setting("kick_granted_scope", granted_scope)
        if granted_scope:
            self.store.delete_setting("kick_sender_name")
            self.store.delete_setting("kick_sender_id")
            self.store.delete_setting("kick_sender_checked")

    def _get_json(self, path: str, token: str) -> dict:
        result = self.transport.request(
            "GET",
            API_ROOT + path,
            headers={"Authorization": f"Bearer {token}"},
            allow_hosts=API_HOSTS,
        )
        if result.status == 401:
            self.store.delete_setting("kick_access_token")
            token = self._access_token()
            result = self.transport.request(
                "GET",
                API_ROOT + path,
                headers={"Authorization": f"Bearer {token}"},
                allow_hosts=API_HOSTS,
            )
        if result.status >= 400:
            raise ApiError(_kick_status_message(result.status), status=result.status)
        payload = result.json()
        if not isinstance(payload, dict):
            raise ApiError("Kick returned a response that was not an object")
        return payload

    def _broadcaster_user_id(self, token: str, slug: str) -> int:
        query = urllib.parse.urlencode({"slug": slug})
        payload = self._get_json("/public/v1/channels?" + query, token)
        data = payload.get("data")
        rows = data if isinstance(data, list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw = row.get("broadcaster_user_id")
            if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
                continue
            row_slug = str(row.get("slug") or "").lower()
            if row_slug and row_slug != slug:
                continue
            return raw
        raise ApiError("Kick did not return a channel id for that name.")

    def _post_chat(self, token: str, broadcaster_user_id: int, content: str) -> HttpResult:
        return self.transport.request(
            "POST",
            API_ROOT + "/public/v1/chat",
            headers={"Authorization": f"Bearer {token}"},
            json_body={
                "broadcaster_user_id": broadcaster_user_id,
                "content": content,
                "type": "user",
            },
            allow_hosts=API_HOSTS,
        )


def _grapheme_count(text: str) -> int:
    """User-perceived characters. Combining marks do not add to the count."""
    count = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        count += 1
    return count


def _kick_status_message(status: int) -> str:
    if status == 401:
        return "Reconnect Kick to allow chat."
    if status == 403:
        return "Kick refused that message. This account may not be allowed to chat there."
    if status == 404:
        return "Kick has no channel for that name."
    if status == 429:
        return "Kick is limiting how often this account can post."
    if status == 400:
        return "Kick rejected that message."
    return "Kick refused that message."
