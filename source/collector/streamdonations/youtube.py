"""YouTube Data API collectors for the signed-in channel.

Super Chats and Super Stickers:
    GET https://www.googleapis.com/youtube/v3/superChatEvents?part=id,snippet
Memberships, milestones, and gifted memberships while a broadcast is live:
    GET https://www.googleapis.com/youtube/v3/liveBroadcasts
    GET https://www.googleapis.com/youtube/v3/liveChat/messages
A message posted as the signed-in account:
    POST https://www.googleapis.com/youtube/v3/liveChat/messages?part=snippet
Reading accepts youtube.readonly. Posting requires youtube.force-ssl, which
superChatEvents.list also accepts, so the app requests force-ssl. A grant from
before that change can still read and cannot post until Connect is used again.
The live chat read is skipped when the channel is offline. Posting uses
videos.list liveStreamingDetails.activeLiveChatId for the open ledger's video.
That id exists while the broadcast is live.
"""

from __future__ import annotations

import threading
import time
import urllib.parse
import webbrowser

from .normalize import normalize_live_chat_message, normalize_super_chat_event
from .store import Store
from .transport import HttpResult, Transport, api_error_message
from .util import ApiError, AuthError, collapsed_chat_text, redact
from .youtube_public import PublicChatFollower, video_id_from_target
from .youtube_quota import QuotaPaused, YouTubeQuota, clamp_poll_seconds

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_ROOT = "https://www.googleapis.com/youtube/v3"
SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
# The full youtube scope can also insert. youtube.readonly is a different scope
# and is not a prefix check away from these two.
_SEND_SCOPES = {
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube",
}
# https://developers.google.com/youtube/v3/live/docs/liveChatMessages/insert
INSERT_COST = 20
API_HOSTS = {"www.googleapis.com"}
TOKEN_HOSTS = {"oauth2.googleapis.com"}
_SEND_REASONS = {
    "forbidden": "YouTube refused that message. The signed-in account may not be allowed to chat there.",
    "insufficientPermissions": "Reconnect YouTube to allow chat. The current sign-in can only read.",
    "liveChatDisabled": "That YouTube chat is turned off.",
    "liveChatEnded": "That YouTube chat has ended.",
    "liveChatNotFound": "YouTube has no live chat for that broadcast.",
    "messageTextInvalid": "YouTube rejected that message.",
    "rateLimitExceeded": "YouTube is limiting how often this account can post.",
    "quotaExceeded": "YouTube Data API quota is used for today.",
}


def scope_can_send(scope: str) -> bool:
    return bool(_SEND_SCOPES.intersection(str(scope or "").split()))


class YouTubeClient:
    def __init__(self, store: Store, transport: Transport | None = None, chat_feed=None):
        self.store = store
        self.transport = transport or Transport()
        self._auth_lock = threading.Lock()
        self._next_super = 0.0
        self._next_broadcast = 0.0
        self._next_chat = 0.0
        self._chats: list[dict[str, str]] = []
        self._chat_tokens: dict[str, str] = {}
        self._live_disabled = False
        self._public = PublicChatFollower(store, self.transport, chat_feed)
        self._quota = YouTubeQuota(store)

    def client_saved(self) -> bool:
        return bool(self.store.get_setting("youtube_client_id") and self.store.get_setting("youtube_client_secret"))

    def connected(self) -> bool:
        return self.client_saved() and bool(self.store.get_setting("youtube_refresh_token"))

    def public_video_id(self, target: str) -> str:
        """11-character id for a saved video address. Same lookup public chat uses."""
        return self._public._resolve(target)

    def public_live_video_id(self, channel: str) -> str | None:
        """Current live video id, or None when the channel is offline.

        Uses the public /live page already fetched for chat. It does not spend Data API quota.
        """
        return self._public._resolve_live_channel(channel)

    def public_stream_title(self, url: str) -> str:
        """Title on a public watch or /live page. It does not spend Data API quota."""
        return self._public.stream_title(url)

    def can_send(self) -> bool:
        return self.connected() and scope_can_send(self.store.get_setting("youtube_granted_scope"))

    def sender_name(self) -> str:
        """Channel title for the signed-in account. Fetched once per grant."""
        cached = self.store.get_setting("youtube_sender_name").strip()
        if cached or self.store.get_setting("youtube_sender_checked") == "1":
            return cached
        if not self.connected() or self._quota.paused():
            return ""
        self.store.set_setting("youtube_sender_checked", "1")
        try:
            payload = self._api("/channels", {"part": "snippet", "mine": "true"}, self._access_token())
        except (ApiError, AuthError, QuotaPaused):
            return ""
        items = payload.get("items") or []
        title = ""
        if items and isinstance(items[0], dict):
            snippet = items[0].get("snippet") or {}
            if isinstance(snippet, dict):
                title = str(snippet.get("title") or "").strip()
        if title:
            self.store.set_setting("youtube_sender_name", title)
        return title

    def send_message(self, text: str, video_id: str | None = None) -> None:
        """Post as the signed-in account into a ledger's live chat.

        Without video_id, that ledger is the one open on screen.
        """
        if not self.connected():
            raise AuthError("Connect a YouTube account to send chat.")
        if not scope_can_send(self.store.get_setting("youtube_granted_scope")):
            raise AuthError("Reconnect YouTube to allow chat. The current sign-in can only read.")
        message = collapsed_chat_text(text)
        if self._quota.paused():
            raise QuotaPaused(self._quota.status_detail())
        video_id = (video_id or "").strip() or self._watched_video_id()
        token = self._access_token()
        live_chat_id = self._live_chat_id_for_video(token, video_id)
        if not live_chat_id:
            raise ApiError(
                "That YouTube broadcast has no live chat to post into. "
                "A waiting room can be read here before YouTube will accept a message."
            )
        url = API_ROOT + "/liveChat/messages?" + urllib.parse.urlencode({"part": "snippet"})
        body = {
            "snippet": {
                "liveChatId": live_chat_id,
                "type": "textMessageEvent",
                "textMessageDetails": {"messageText": message},
            }
        }
        result = self._post(url, token, body)
        if result.status == 401:
            self.store.delete_setting("youtube_access_token")
            result = self._post(url, self._access_token(), body)
        if result.status >= 400:
            if _is_quota_response(result):
                self._quota.note_exceeded()
            raise ApiError(_send_failure(result), status=result.status)

    def interval(self) -> float:
        raw = self.store.get_setting("youtube_poll_seconds", "900")
        seconds = clamp_poll_seconds(raw)
        if raw != str(seconds):
            self.store.set_setting("youtube_poll_seconds", str(seconds))
        return float(seconds)

    def _broadcast_wait(self) -> float:
        # Both calls cost 1 unit. Five minutes is enough to notice a broadcast
        # starting. Two minutes while a chat is open notices it ending.
        if self._chats:
            return 120.0
        return 300.0

    def authorization_url(self, redirect_uri: str, state: str, challenge: str) -> str:
        query = urllib.parse.urlencode(
            {
                "client_id": self.store.get_setting("youtube_client_id"),
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "state": state,
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            quote_via=urllib.parse.quote,
        )
        return AUTH_URL + "?" + query

    def exchange_code(self, code: str, redirect_uri: str, verifier: str) -> None:
        with self._auth_lock:
            result = self.transport.request(
                "POST",
                TOKEN_URL,
                form={
                    "code": code,
                    "client_id": self.store.get_setting("youtube_client_id"),
                    "client_secret": self.store.get_setting("youtube_client_secret"),
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": verifier,
                },
                allow_hosts=TOKEN_HOSTS,
            )
            self._store_token_result(result, reset_backfill=True)

    def disconnect(self) -> None:
        with self._auth_lock:
            for key in (
                "youtube_refresh_token",
                "youtube_access_token",
                "youtube_access_expiry",
                "youtube_granted_scope",
                "youtube_sender_name",
                "youtube_sender_checked",
            ):
                self.store.delete_setting(key)
        self._chat_tokens.clear()
        self._chats = []
        self._live_disabled = False
        self._next_broadcast = 0.0
        public = self.store.get_setting("youtube_watch") or (
            self.store.get_setting("youtube_follow_live") == "1" and self.store.get_setting("youtube_channel")
        )
        if public:
            self.store.set_status(
                "youtube",
                "idle",
                "Google account disconnected. The public video chat is unchanged.",
            )
        else:
            self.store.set_status("youtube", "unconfigured", "Disconnected. Connect the channel again to keep collecting.")

    def rest(self) -> None:
        self._public.rest()

    def tick(self, force: bool = False, public_only: bool = False) -> None:
        if self.store.collection_paused():
            self.rest()
            return
        self._public.ensure()
        if public_only:
            return
        follow = self.store.get_setting("youtube_follow_live") == "1"
        has_channel = bool(self.store.get_setting("youtube_channel"))
        has_watch = bool(self.store.get_setting("youtube_watch"))
        if not self.connected():
            if follow and not has_channel:
                self.store.set_status(
                    "youtube",
                    "idle",
                    "Paste a channel URL to follow the live stream. "
                    "The video URL is kept and is not used while this is on.",
                )
                return
            if follow or has_watch:
                self._report_public()
                return
            if not self.client_saved():
                self.store.set_status(
                    "youtube",
                    "unconfigured",
                    "Paste a public video URL. Google sign-in is only for your own channel's 30-day Super Chat history.",
                )
                return
            self.store.set_status("youtube", "idle", "Client saved. Connect the YouTube channel to start collecting.")
            return
        if self._quota.paused():
            self._report_quota_pause()
            return
        errors: list[str] = []
        try:
            token = self._access_token()
        except (ApiError, OSError, ValueError) as exc:
            message = redact(str(exc))
            self.store.set_status("youtube", "error", message, error=message)
            return
        now = time.monotonic()
        inserted_super = 0
        inserted_chat = 0
        if force or now >= self._next_super:
            try:
                inserted_super = self._poll_super_chats(token)
                self._next_super = time.monotonic() + self.interval()
            except QuotaPaused:
                self._report_quota_pause()
                return
            except (ApiError, OSError, ValueError) as exc:
                if self._stop_for_quota(exc):
                    return
                errors.append(redact(str(exc)))
                self._next_super = time.monotonic() + self.interval()
        if force or now >= self._next_broadcast:
            try:
                self._chats = self._active_chats(token)
                self._next_broadcast = time.monotonic() + self._broadcast_wait()
                active = {chat["id"] for chat in self._chats}
                self._chat_tokens = {key: value for key, value in self._chat_tokens.items() if key in active}
            except QuotaPaused:
                self._report_quota_pause()
                return
            except (ApiError, OSError, ValueError) as exc:
                if _live_streaming_disabled(exc):
                    # The signed-in channel has never enabled live streaming.
                    # Memberships on that channel's own broadcast cannot exist.
                    # Super Chat history and a public video URL still work.
                    self._chats = []
                    self._chat_tokens.clear()
                    self._next_broadcast = time.monotonic() + 3600
                    if not self._live_disabled:
                        self._live_disabled = True
                        self.store.log(
                            "youtube",
                            "info",
                            "This Google account is not enabled for live streaming. "
                            "Memberships on your own broadcast are skipped. "
                            "Super Chat history still syncs, and a public video URL is unaffected.",
                        )
                else:
                    if self._stop_for_quota(exc):
                        return
                    errors.append(redact(str(exc)))
                    self._next_broadcast = time.monotonic() + self._broadcast_wait()
        if self._chats and (force or now >= self._next_chat):
            try:
                inserted_chat, wait_seconds = self._poll_chats_once(token)
                self._next_chat = time.monotonic() + wait_seconds
            except QuotaPaused:
                self._report_quota_pause()
                return
            except (ApiError, OSError, ValueError) as exc:
                if self._stop_for_quota(exc):
                    return
                errors.append(redact(str(exc)))
                self._next_chat = time.monotonic() + 30
        if inserted_super:
            self.store.log("youtube", "info", f"Stored {inserted_super} Super Chat or Super Sticker event(s).")
        if inserted_chat:
            self.store.log("youtube", "info", f"Stored {inserted_chat} membership event(s) from live chat.")
        if errors:
            message = "; ".join(errors)
            self.store.set_status("youtube", "error", message, error=message)
            self.store.log("youtube", "error", message)
            return
        if self._chats:
            self._live_disabled = False
            detail = f"Live chat connected ({len(self._chats)}). Super Chats sync about every {int(self.interval())}s."
        elif self._live_disabled:
            detail = (
                "This Google account is not enabled for live streaming, so memberships on your own "
                "broadcast are skipped. Super Chats from the last 30 days still sync."
            )
        else:
            detail = "No active live chat on your channel. Super Chats from the last 30 days still sync."
        if self._public.detail:
            detail += " " + self._public.detail
        if self._public.error:
            detail += " Public chat: " + self._public.error
            self.store.set_status("youtube", "error", detail, error=self._public.error)
            return
        self.store.set_status("youtube", "ok", detail)

    def _stop_for_quota(self, exc: BaseException) -> bool:
        if self._quota.paused() or "quota" in str(exc).lower():
            self._quota.note_exceeded()
            self._report_quota_pause()
            return True
        return False

    def _report_quota_pause(self) -> None:
        detail = self._quota.status_detail()
        if self._public.detail:
            detail += " " + self._public.detail
        if self._public.error:
            detail += " Public chat: " + self._public.error
            self.store.set_status("youtube", "error", detail, error=self._public.error)
            return
        self.store.set_status("youtube", "ok", detail)

    def _report_public(self) -> None:
        if self._public.error or self._public.level == "error":
            message = self._public.error or "Public chat failed."
            self.store.set_status("youtube", "error", message, error=message)
        elif self._public.level == "idle":
            self.store.set_status("youtube", "idle", self._public.detail or "The channel is not live.")
        elif self._public.detail:
            self.store.set_status("youtube", "ok", self._public.detail)
        else:
            self.store.set_status("youtube", "idle", "Opening the public YouTube live chat.")

    def _access_token(self) -> str:
        with self._auth_lock:
            cached = self._fresh_access_token()
            if cached:
                return cached
            refresh = self.store.get_setting("youtube_refresh_token")
            if not refresh:
                raise AuthError("YouTube is not connected")
            result = self.transport.request(
                "POST",
                TOKEN_URL,
                form={
                    "client_id": self.store.get_setting("youtube_client_id"),
                    "client_secret": self.store.get_setting("youtube_client_secret"),
                    "refresh_token": refresh,
                    "grant_type": "refresh_token",
                },
                allow_hosts=TOKEN_HOSTS,
            )
            self._store_token_result(result, keep_refresh=refresh)
            token = self.store.get_setting("youtube_access_token")
            if not token:
                raise AuthError("YouTube token response had no access token")
            return token

    def _fresh_access_token(self) -> str:
        token = self.store.get_setting("youtube_access_token")
        expiry = self.store.get_setting("youtube_access_expiry")
        if not token or not expiry:
            return ""
        try:
            expires_at = float(expiry)
        except ValueError:
            return ""
        if time.time() >= expires_at - 60:
            return ""
        return token

    def _store_token_result(self, result, keep_refresh: str = "", reset_backfill: bool = False) -> None:
        if result.status >= 400:
            raise AuthError(api_error_message(result))
        payload = result.json()
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise AuthError("YouTube token response was missing access_token")
        self.store.set_setting("youtube_access_token", str(payload["access_token"]))
        expires_in = int(payload.get("expires_in") or 3600)
        self.store.set_setting("youtube_access_expiry", str(time.time() + expires_in))
        if payload.get("refresh_token"):
            self.store.set_setting("youtube_refresh_token", str(payload["refresh_token"]))
        elif keep_refresh:
            self.store.set_setting("youtube_refresh_token", keep_refresh)
        scope = payload.get("scope")
        if isinstance(scope, str) and scope.strip():
            self.store.set_setting("youtube_granted_scope", " ".join(scope.split()))
        elif reset_backfill:
            self.store.set_setting("youtube_granted_scope", SCOPE)
        if reset_backfill:
            self.store.delete_setting("youtube_sender_name")
            self.store.delete_setting("youtube_sender_checked")
            # A new grant should walk the 30-day Super Chat history again.
            # Dedupe keeps rows that were already stored. Refreshing an access
            # token must not restart that walk.
            self.store.set_setting("youtube_superchat_backfill_done", "0")

    def _api(self, path: str, query: dict[str, str], token: str):
        url = API_ROOT + path + "?" + urllib.parse.urlencode(query)
        result = self._get(url, token)
        if result.status == 401:
            self.store.delete_setting("youtube_access_token")
            token = self._access_token()
            result = self._get(url, token)
        if result.status >= 400:
            if _is_quota_response(result):
                self._quota.note_exceeded()
            raise ApiError(api_error_message(result), status=result.status)
        payload = result.json()
        if not isinstance(payload, dict):
            raise ApiError("YouTube returned a response that was not an object")
        return payload

    def _get(self, url: str, token: str) -> HttpResult:
        # Count the call before sending it. Google bills a failed request too.
        self._quota.reserve()
        return self.transport.request(
            "GET",
            url,
            headers={"Authorization": f"Bearer {token}"},
            allow_hosts=API_HOSTS,
        )

    def _post(self, url: str, token: str, body: dict) -> HttpResult:
        self._quota.reserve(INSERT_COST)
        return self.transport.request(
            "POST",
            url,
            headers={"Authorization": f"Bearer {token}"},
            json_body=body,
            allow_hosts=API_HOSTS,
        )

    def _watched_video_id(self) -> str:
        if self.store.get_setting("youtube_follow_live") == "1":
            channel = self.store.get_setting("youtube_channel").strip()
            if not channel:
                raise ApiError("This ledger has no YouTube channel.")
            video_id = self.public_live_video_id(channel)
            if not video_id:
                raise ApiError("That YouTube channel is not live, so there is no chat to post into.")
            return str(video_id).strip()
        watch = self.store.get_setting("youtube_watch").strip()
        if not watch:
            raise ApiError("This ledger has no YouTube video.")
        video_id = video_id_from_target(watch)
        if video_id:
            return video_id
        return self.public_video_id(watch)

    def _live_chat_id_for_video(self, token: str, video_id: str) -> str:
        payload = self._api("/videos", {"part": "liveStreamingDetails", "id": video_id}, token)
        items = payload.get("items") or []
        if not items or not isinstance(items[0], dict):
            return ""
        details = items[0].get("liveStreamingDetails") or {}
        if not isinstance(details, dict):
            return ""
        return str(details.get("activeLiveChatId") or "")

    def _poll_super_chats(self, token: str) -> int:
        # Newest-first. The first run walks one page per poll and remembers
        # the cursor, so a restart does not start the 30-day history over.
        # After that, only the newest page is read.
        backfill = self.store.get_setting("youtube_superchat_backfill_done") != "1"
        query = {"part": "id,snippet", "maxResults": "50", "hl": "en"}
        page = self.store.get_setting("youtube_superchat_page") if backfill else ""
        if page:
            query["pageToken"] = page
        payload = self._api("/superChatEvents", query, token)
        donations = []
        for item in payload.get("items") or []:
            donation = normalize_super_chat_event(item)
            if donation is not None:
                donations.append(donation)
        inserted = self.store.add_many(donations)
        next_page = str(payload.get("nextPageToken") or "")
        walked = 0
        if backfill:
            try:
                walked = int(self.store.get_setting("youtube_superchat_pages") or "0")
            except ValueError:
                walked = 0
            walked += 1
        # 40 pages is 2,000 events. Past that, keep refreshing the newest page
        # only. One page per poll, so this walk cannot repeat every minute.
        if not backfill or not next_page or walked >= 40:
            self.store.set_setting("youtube_superchat_backfill_done", "1")
            self.store.delete_setting("youtube_superchat_page")
            self.store.delete_setting("youtube_superchat_pages")
        else:
            self.store.set_setting("youtube_superchat_pages", str(walked))
            self.store.set_setting("youtube_superchat_page", next_page)
        return inserted

    def _active_chats(self, token: str) -> list[dict[str, str]]:
        payload = self._api(
            "/liveBroadcasts",
            {
                "part": "snippet,status",
                "broadcastStatus": "active",
                "broadcastType": "all",
                "maxResults": "50",
            },
            token,
        )
        chats = []
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            snippet = item.get("snippet") or {}
            if not isinstance(snippet, dict):
                continue
            live_chat_id = str(snippet.get("liveChatId") or "")
            if not live_chat_id:
                continue
            chats.append(
                {
                    "id": live_chat_id,
                    "channel": str(snippet.get("channelId") or ""),
                    "title": str(snippet.get("title") or ""),
                }
            )
        self._live_disabled = False
        return chats

    def _poll_chats_once(self, token: str) -> tuple[int, float]:
        inserted = 0
        wait_ms = 5000
        for chat in self._chats:
            query = {
                "liveChatId": chat["id"],
                "part": "snippet,authorDetails",
                "maxResults": "200",
            }
            page = self._chat_tokens.get(chat["id"], "")
            if page:
                query["pageToken"] = page
            payload = self._api("/liveChat/messages", query, token)
            if payload.get("offlineAt"):
                self._chat_tokens.pop(chat["id"], None)
                continue
            donations = []
            for item in payload.get("items") or []:
                donation = normalize_live_chat_message(item, channel_id=chat["channel"])
                if donation is not None:
                    donations.append(donation)
            inserted += self.store.add_many(donations)
            next_token = str(payload.get("nextPageToken") or "")
            if next_token:
                self._chat_tokens[chat["id"]] = next_token
            try:
                wait_ms = max(wait_ms, int(payload.get("pollingIntervalMillis") or 0))
            except (TypeError, ValueError):
                pass
        return inserted, max(2.0, wait_ms / 1000)


def _live_streaming_disabled(exc: BaseException) -> bool:
    return "not enabled for live streaming" in str(exc).lower()


def _send_failure(result: HttpResult) -> str:
    reason = ""
    try:
        payload = result.json()
    except ApiError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            rows = error.get("errors") or []
            if rows and isinstance(rows[0], dict):
                reason = str(rows[0].get("reason") or "")
            if not reason:
                reason = str(error.get("status") or "")
    return _SEND_REASONS.get(reason) or "YouTube refused that message."


def _is_quota_response(result: HttpResult) -> bool:
    if result.status != 403:
        return False
    return "quota" in result.body.decode("utf-8", "replace").lower()


def open_browser(url: str) -> bool:
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False
