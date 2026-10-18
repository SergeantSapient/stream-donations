"""Public YouTube live chat, for a stream the user does not own.

superChatEvents.list only returns purchases on the signed-in channel. A public
broadcast's Super Chats, stickers, and memberships are in the live chat the
watch page already loads:

    GET  https://www.youtube.com/live_chat?v={video_id}
    POST https://www.youtube.com/youtubei/v1/live_chat/get_live_chat

The API key and continuation token come from that page. No channel ownership
and no OAuth consent are required.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from .chat_feed import chat_time, offer_chat, remember_chat
from .money import is_anniversary_text, parse_display_amount, youtube_paid_amount
from .normalize import Donation
from .store import Store
from .transport import Transport, api_error_message
from .util import ApiError, format_utc, ledger_title, redact, stable_key

YOUTUBE_HOSTS = {"youtube.com", "youtu.be", "youtube-nocookie.com"}
YT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 StreamDonations/1.0"
)
# SOCS is the consent cookie the site itself sets. Without it the HTML is a
# consent wall and contains no chat token.
YT_HEADERS = {
    "Accept-Language": "en",
    "Cookie": "SOCS=CAI; CONSENT=YES+cb",
}
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Observed entry for YouTube's own sign-in. It answers 303 to accounts.google.com.
# The window loads this fixed page. The renderer does not choose the address.
YOUTUBE_LOGIN_URL = "https://www.youtube.com/signin"


def validate_youtube_watch(value: str) -> str:
    text = (value or "").strip()
    if _VIDEO_ID_RE.fullmatch(text):
        return text
    parsed = urlparse(text)
    if parsed.scheme != "https":
        raise ValueError("A YouTube link must start with https://")
    if parsed.username or parsed.password:
        raise ValueError("A YouTube link must not include a username or password")
    if parsed.port not in (None, 443):
        raise ValueError("A YouTube link must use port 443")
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host not in YOUTUBE_HOSTS:
        raise ValueError("YouTube link must be on youtube.com or youtu.be")
    return text


def youtube_popup_chat_url(video_id: str) -> str:
    """YouTube's own pop-out chat. Public chat already reads this live_chat page."""
    text = str(video_id or "").strip()
    if not _VIDEO_ID_RE.fullmatch(text):
        raise ValueError("That YouTube chat could not be opened.")
    return f"https://www.youtube.com/live_chat?is_popout=1&v={text}"


def video_id_from_target(value: str) -> str | None:
    text = value.strip()
    if _VIDEO_ID_RE.fullmatch(text):
        return text
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host == "youtu.be":
        part = parsed.path.strip("/").split("/")[0]
        return part if _VIDEO_ID_RE.fullmatch(part) else None
    query = parse_qs(parsed.query)
    if query.get("v") and _VIDEO_ID_RE.fullmatch(query["v"][0]):
        return query["v"][0]
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"live", "embed", "shorts", "v"} and _VIDEO_ID_RE.fullmatch(parts[1]):
        return parts[1]
    return None


def video_id_from_html(html: str) -> str | None:
    for pattern in (
        r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"',
        r'"currentVideoEndpoint":\{"clickTrackingParams":"[^"]*","commandMetadata":\{[^}]*\},"watchEndpoint":\{"videoId":"([A-Za-z0-9_-]{11})"',
        r'"videoId":"([A-Za-z0-9_-]{11})"',
    ):
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return None


# Tabs YouTube puts on a channel URL. None of these is a video id.
_CHANNEL_TABS = {
    "about",
    "channels",
    "community",
    "featured",
    "live",
    "playlists",
    "posts",
    "releases",
    "search",
    "shorts",
    "streams",
    "videos",
}
_CHANNEL_ROOTS = {"channel", "c", "user"}


def validate_youtube_channel(value: str) -> str:
    """A channel URL. A watch link or video id stays in the video field."""
    text = (value or "").strip()
    if _VIDEO_ID_RE.fullmatch(text):
        raise ValueError(
            "Paste a channel URL, such as https://www.youtube.com/@channel. A video id belongs in the video field."
        )
    checked = validate_youtube_watch(text)
    parsed = urlparse(checked)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host == "youtu.be" or video_id_from_target(checked):
        raise ValueError(
            "That is a video link. Paste the channel URL, or turn off follow live and use the video field."
        )
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        raise ValueError("Paste a channel URL, such as https://www.youtube.com/@channel.")
    if parts[0].startswith("@"):
        if not _channel_segment(parts[0][1:]):
            raise ValueError("That YouTube channel address is not valid.")
        extra = parts[1:]
    elif parts[0] in _CHANNEL_ROOTS and len(parts) >= 2 and _channel_segment(parts[1]):
        extra = parts[2:]
    else:
        raise ValueError("Paste a channel URL, such as https://www.youtube.com/@channel or a /channel/UC… link.")
    if len(extra) > 1 or (extra and extra[0].lower() not in _CHANNEL_TABS):
        raise ValueError("Paste the channel URL, not a video or playlist.")
    return checked


def youtube_channel_live_url(value: str) -> str:
    """The public /live page for a channel. The app already fetches YouTube HTML."""
    text = validate_youtube_channel(value)
    parts = [part for part in urlparse(text).path.split("/") if part]
    if parts[-1].lower() in _CHANNEL_TABS:
        parts = parts[:-1]
    return "https://www.youtube.com/" + "/".join(parts) + "/live"


def live_video_from_channel_html(html: str) -> str | None:
    """Video id for the broadcast this /live page is currently presenting.

    That is the one on now, or the upcoming one whose waiting-room chat is
    already open. A channel page lists many videos. The canonical watch link
    has to be that broadcast. A loose videoId is not.
    """
    if "consent.youtube.com" in html and "ytInitialData" not in html:
        raise ValueError("YouTube returned a consent page instead of the channel.")
    canon = re.search(
        r'<link rel="canonical" href="https://(?:www\.)?youtube\.com/watch\?v=([A-Za-z0-9_-]{11})(?:&[^"]*)?"',
        html,
    )
    if not canon:
        return None
    video_id = canon.group(1)
    details = re.search(r'"videoDetails":\{"videoId":"([A-Za-z0-9_-]{11})"', html)
    if details is None:
        if '"isLiveNow":true' in html or '"isUpcoming":true' in html:
            return video_id
        return None
    if details.group(1) != video_id:
        return None
    window = html[details.end():details.end() + 12000]
    next_details = window.find('"videoDetails"')
    if next_details > 0:
        window = window[:next_details]
    # Scheduled broadcast. YouTube opens public chat before isLiveNow flips.
    if '"isUpcoming":true' in window:
        return video_id
    if '"isLiveNow":true' not in html:
        return None
    live_at = window.find('"isLive":true')
    offline_at = window.find('"isLive":false')
    if live_at < 0 or (offline_at >= 0 and offline_at < live_at):
        return None
    return video_id


def stream_title_from_youtube_html(html: str) -> str:
    """Title of the broadcast this page is presenting. A finished video has none."""
    video_id = live_video_from_channel_html(html)
    if not video_id:
        return ""
    marker = '"videoDetails":'
    start = 0
    while True:
        at = html.find(marker, start)
        if at < 0:
            return ""
        start = at + len(marker)
        brace = html.find("{", start)
        if brace < 0 or brace > start + 20:
            continue
        try:
            details, _end = json.JSONDecoder().raw_decode(html[brace:])
        except json.JSONDecodeError:
            continue
        if not isinstance(details, dict) or str(details.get("videoId") or "") != video_id:
            continue
        return ledger_title(details.get("title"))


def _channel_segment(value: str) -> bool:
    if not value or value in {".", ".."} or len(value) > 80:
        return False
    return all(char.isalnum() or char in "._-" for char in value)


def session_from_live_chat_html(html: str) -> dict:
    """Pull the innertube key, client context, and first continuation."""
    if "consent.youtube.com" in html and "ytInitialData" not in html:
        raise ValueError("YouTube returned a consent page instead of the live chat.")
    api_key = _first(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
    version = _first(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html) or "2.20250101.00.00"
    context = {"client": {"clientName": "WEB", "clientVersion": version, "hl": "en", "gl": "US"}}
    config = _json_after(html, "ytcfg.set(")
    if isinstance(config, dict) and isinstance(config.get("INNERTUBE_CONTEXT"), dict):
        context = config["INNERTUBE_CONTEXT"]
        api_key = api_key or str(config.get("INNERTUBE_API_KEY") or "")
    initial = _json_after(html, "ytInitialData")
    continuation, timeout_ms = _continuation_from(initial) if isinstance(initial, dict) else (None, 5000)
    if not continuation:
        match = re.search(
            r'"(?:invalidationContinuationData|timedContinuationData|reloadContinuationData)"\s*:\s*\{[^{}]*"continuation":"([^"]+)"',
            html,
        )
        continuation = match.group(1) if match else None
    if not api_key or not continuation:
        raise ValueError("This video has no public live chat. It may be offline, or chat is turned off.")
    channel = _first(r'"ownerChannelName":"([^"]+)"', html) or _first(r'"author":"([^"]+)"', html) or ""
    return {
        "api_key": api_key,
        "context": context,
        "continuation": continuation,
        "timeout_ms": timeout_ms,
        "channel": channel,
        "actions": _initial_actions(initial) if isinstance(initial, dict) else [],
    }


def chat_lines_from_live_chat_actions(actions: object) -> list[dict]:
    """Viewer text already inside a live-chat poll. Paid notes are included. Nothing is fetched."""
    if not isinstance(actions, list):
        return []
    found: list[dict] = []
    seen: set[str] = set()
    for action in actions:
        for item in _action_items(action):
            renderer = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
            if not isinstance(renderer, dict):
                continue
            text = " ".join(yt_text(renderer.get("message")).split())
            if not text:
                continue
            author = " ".join(yt_text(renderer.get("authorName")).split())
            key = str(renderer.get("id") or "")
            if not key:
                key = stable_key("youtube:chat:", [author, text])
            if key in seen:
                continue
            seen.add(key)
            found.append({
                "key": key,
                "author": author,
                "text": text,
                "time": chat_time(renderer.get("timestampUsec")),
            })
    return found


def donations_from_live_chat_actions(actions: object, channel: str = "") -> list[Donation]:
    if not isinstance(actions, list):
        return []
    found: dict[str, Donation] = {}
    for action in actions:
        for item in _action_items(action):
            donation = _item_donation(item, channel)
            if donation is not None:
                found[donation.dedupe_key] = donation
    return list(found.values())


def next_live_chat_page(payload: dict) -> tuple[str | None, int, list]:
    contents = payload.get("continuationContents") if isinstance(payload, dict) else None
    live = contents.get("liveChatContinuation") if isinstance(contents, dict) else None
    if not isinstance(live, dict):
        return None, 5000, []
    continuation, timeout_ms = _continuation_from(live)
    actions = live.get("actions") if isinstance(live.get("actions"), list) else []
    return continuation, timeout_ms, actions


def _action_items(action: object) -> list[dict]:
    if not isinstance(action, dict):
        return []
    items: list[dict] = []
    direct = action.get("addChatItemAction")
    if isinstance(direct, dict) and isinstance(direct.get("item"), dict):
        items.append(direct["item"])
    replay = action.get("replayChatItemAction")
    if isinstance(replay, dict):
        for nested in replay.get("actions") or []:
            items.extend(_action_items(nested))
    return items


def _item_donation(item: dict, channel: str) -> Donation | None:
    if "liveChatPaidMessageRenderer" in item:
        return _paid(item["liveChatPaidMessageRenderer"], "super_chat", channel)
    if "liveChatPaidStickerRenderer" in item:
        return _paid(item["liveChatPaidStickerRenderer"], "super_sticker", channel)
    if "liveChatMembershipItemRenderer" in item:
        return _membership(item["liveChatMembershipItemRenderer"], channel)
    if "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer" in item:
        return _gift_purchase(item["liveChatSponsorshipsGiftPurchaseAnnouncementRenderer"], channel)
    if "liveChatSponsorshipsGiftRedemptionAnnouncementRenderer" in item:
        return _gift_received(item["liveChatSponsorshipsGiftRedemptionAnnouncementRenderer"], channel)
    return None


def _paid(renderer: dict, kind: str, channel: str) -> Donation | None:
    if not isinstance(renderer, dict):
        return None
    amount, currency = youtube_paid_amount(renderer)
    if kind == "super_sticker":
        sticker = renderer.get("sticker") if isinstance(renderer.get("sticker"), dict) else {}
        label = ""
        access = sticker.get("accessibility") if isinstance(sticker, dict) else None
        if isinstance(access, dict):
            data = access.get("accessibilityData")
            if isinstance(data, dict):
                label = str(data.get("label") or "")
        message = yt_text(renderer.get("message")) or label or "Super Sticker"
    else:
        message = yt_text(renderer.get("message"))
    return _row(renderer, kind, channel, message, amount, currency)


def _membership(renderer: dict, channel: str) -> Donation | None:
    if not isinstance(renderer, dict):
        return None
    primary = yt_text(renderer.get("headerPrimaryText"))
    subtext = yt_text(renderer.get("headerSubtext"))
    comment = yt_text(renderer.get("message"))
    kind = (
        "member_milestone"
        if is_anniversary_text(primary) or is_anniversary_text(subtext)
        else "membership"
    )
    message = " · ".join(part for part in (primary, subtext, comment) if part)
    return _row(renderer, kind, channel, message, None, None)


def _gift_purchase(renderer: dict, channel: str) -> Donation | None:
    if not isinstance(renderer, dict):
        return None
    header = renderer.get("header") if isinstance(renderer.get("header"), dict) else {}
    inner = header.get("liveChatSponsorshipsHeaderRenderer") if isinstance(header.get("liveChatSponsorshipsHeaderRenderer"), dict) else header
    message = yt_text(inner.get("primaryText")) or "Gifted memberships"
    author = yt_text(inner.get("authorName")) or yt_text(renderer.get("authorName"))
    author_id = str(renderer.get("authorExternalChannelId") or inner.get("authorExternalChannelId") or "")
    return _row(renderer, "membership_gift", channel, message, None, None, author=author, author_id=author_id)


def _gift_received(renderer: dict, channel: str) -> Donation | None:
    if not isinstance(renderer, dict):
        return None
    message = yt_text(renderer.get("message")) or "Received a gift membership"
    return _row(renderer, "gift_membership_received", channel, message, None, None)


def _row(
    renderer: dict,
    kind: str,
    channel: str,
    message: str,
    amount: float | None,
    currency: str | None,
    author: str | None = None,
    author_id: str | None = None,
) -> Donation:
    message_id = str(renderer.get("id") or "")
    author_name = author if author is not None else yt_text(renderer.get("authorName"))
    author_channel = author_id if author_id is not None else str(renderer.get("authorExternalChannelId") or "")
    created = _usec_to_iso(renderer.get("timestampUsec"))
    dedupe = f"youtube:public:{message_id}" if message_id else stable_key(
        "youtube:public:", [kind, author_channel, created, amount, message]
    )
    micros = None if amount is None else int(round(amount * 1_000_000))
    return Donation(
        dedupe_key=dedupe,
        platform="youtube",
        kind=kind,
        external_id=message_id,
        channel=channel,
        author_name=author_name,
        author_id=author_channel,
        message=message,
        amount_value=amount,
        amount_micros=micros,
        currency=currency,
        created_at=created,
        raw=renderer,
    )


def yt_text(node: object) -> str:
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    if isinstance(node.get("simpleText"), str):
        return node["simpleText"]
    runs = node.get("runs")
    if isinstance(runs, list):
        return "".join(str(run.get("text") or "") if isinstance(run, dict) else str(run) for run in runs)
    return ""


def _usec_to_iso(value: object) -> str:
    try:
        usec = int(str(value))
    except (TypeError, ValueError):
        return ""
    return format_utc(datetime.fromtimestamp(usec / 1_000_000, tz=timezone.utc))


def _continuation_from(node: object) -> tuple[str | None, int]:
    found: tuple[str, int] | None = None

    def visit(value: object) -> None:
        nonlocal found
        if found and True:
            return
        if isinstance(value, dict):
            for key in ("invalidationContinuationData", "timedContinuationData", "reloadContinuationData"):
                block = value.get(key)
                if isinstance(block, dict) and block.get("continuation"):
                    timeout = int(block.get("timeoutMs") or 5000)
                    if key == "invalidationContinuationData" or found is None:
                        found = (str(block["continuation"]), timeout)
                    if key == "invalidationContinuationData":
                        return
            for child in value.values():
                visit(child)
                if found and found[0]:
                    return
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(node)
    if found is None:
        return None, 5000
    return found


def _initial_actions(initial: dict) -> list:
    renderer = _dig(initial, ("contents", "liveChatRenderer"))
    if isinstance(renderer, dict) and isinstance(renderer.get("actions"), list):
        return renderer["actions"]
    return []


def _dig(node: object, path: tuple[str, ...]) -> object:
    current = node
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _json_after(html: str, marker: str) -> dict | None:
    index = html.find(marker)
    if index < 0:
        return None
    start = html.find("{", index)
    if start < 0:
        return None
    try:
        value, _end = json.JSONDecoder().raw_decode(html[start:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _first(pattern: str, html: str) -> str:
    match = re.search(pattern, html)
    return match.group(1) if match else ""


class PublicChatFollower:
    """Reads one public live chat until the watched URL changes."""

    def __init__(self, store: Store, transport: Transport, chat_feed=None):
        self.store = store
        self.transport = transport
        self._chat_feed = chat_feed
        self._chat_for = ""
        self.detail = ""
        self.error = ""
        self.level = ""
        self._target = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def rest(self) -> None:
        """Stop reading chat. The saved watch URL stays in settings."""
        self._stop.set()
        self._target = ""
        self.detail = ""
        self.error = ""
        self.level = ""
        thread = self._thread
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2)

    def source(self) -> str:
        """What to read now. Follow-live ignores a saved video URL until it is off."""
        if self.store.get_setting("youtube_follow_live") == "1":
            channel = self.store.get_setting("youtube_channel").strip()
            return f"live\n{channel}" if channel else ""
        watch = self.store.get_setting("youtube_watch").strip()
        return f"watch\n{watch}" if watch else ""

    def ensure(self) -> None:
        target = self.source()
        self._chat_for = remember_chat(self._chat_feed, "youtube", self._chat_for, target)
        if target == self._target and (not target or (self._thread and self._thread.is_alive())):
            return
        self._stop.set()
        self._stop = threading.Event()
        self._target = target
        self.detail = ""
        self.error = ""
        self.level = ""
        if not target:
            return
        stop = self._stop
        self._thread = threading.Thread(
            target=self._loop, args=(target, stop), name="youtube-public-chat", daemon=True
        )
        self._thread.start()

    def _loop(self, target: str, stop: threading.Event) -> None:
        mode, source = target.split("\n", 1)
        while not stop.is_set() and not self.store.collection_paused():
            try:
                if mode == "live":
                    video_id = self._resolve_live_channel(source)
                    if not video_id:
                        if not self._live(stop):
                            return
                        self._set_idle(
                            "That channel is not live. Chat waits until a broadcast is live "
                            "or its waiting room is open."
                        )
                        stop.wait(30)
                        continue
                else:
                    video_id = self._resolve(source)
                if not self._live(stop):
                    return
                session = self._bootstrap(video_id)
                if not self._live(stop):
                    return
                channel = str(session.get("channel") or "")
                self._store(session.get("actions") or [], channel, stop, chat=video_id, fresh=True)
                continuation = str(session["continuation"])
                timeout_ms = int(session.get("timeout_ms") or 5000)
                if not self._live(stop):
                    return
                self.error = ""
                self.level = "ok"
                self.detail = (
                    f"Public chat for {video_id}"
                    + (f" ({channel})" if channel else "")
                    + ". Super Chats here do not need you to own the channel or sign in with Google."
                )
                self.store.set_status("youtube", "ok", self.detail)
                while self._live(stop) and continuation:
                    stop.wait(max(1.0, min(timeout_ms / 1000, 20.0)))
                    if not self._live(stop):
                        return
                    continuation, timeout_ms, actions, ended = self._poll(session, continuation)
                    if not self._live(stop):
                        return
                    self._store(actions, channel, stop, chat=video_id)
                    if not self._live(stop):
                        return
                    if ended:
                        if not self._live(stop):
                            return
                        self._set_idle(f"{video_id} has no live chat right now.")
                        stop.wait(30)
                        break
            except TimeoutError:
                continue
            except Exception as exc:
                if not self._live(stop):
                    return
                self.error = redact(str(exc))
                self.detail = ""
                self.level = "error"
                self.store.log("youtube", "error", self.error)
                self.store.set_status("youtube", "error", self.error, error=self.error)
                stop.wait(15)

    def _live(self, stop: threading.Event) -> bool:
        return not stop.is_set() and not self.store.collection_paused()

    def _set_idle(self, detail: str) -> None:
        self.error = ""
        self.level = "idle"
        self.detail = detail
        self.store.set_status("youtube", "idle", detail)

    def _resolve(self, target: str) -> str:
        text = validate_youtube_watch(target)
        video_id = video_id_from_target(text)
        if video_id:
            return video_id
        page = self._get(text)
        video_id = video_id_from_html(page)
        if not video_id:
            raise ValueError("Could not find a YouTube video id at that address. Paste a watch URL or the 11-character id.")
        return video_id

    def _resolve_live_channel(self, channel: str) -> str | None:
        page = self._get(youtube_channel_live_url(channel))
        return live_video_from_channel_html(page)

    def stream_title(self, url: str) -> str:
        """Public watch or /live HTML. This does not call the Data API."""
        return stream_title_from_youtube_html(self._get(url))

    def _bootstrap(self, video_id: str) -> dict:
        html = self._get(f"https://www.youtube.com/live_chat?is_popout=1&v={video_id}")
        session = session_from_live_chat_html(html)
        session["video_id"] = video_id
        return session

    def _poll(self, session: dict, continuation: str) -> tuple[str, int, list, bool]:
        result = self.transport.request(
            "POST",
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?prettyPrint=false&key="
            + session["api_key"],
            json_body={"context": session["context"], "continuation": continuation},
            headers={"Accept": "application/json", **YT_HEADERS},
            allow_hosts=YOUTUBE_HOSTS,
            user_agent=YT_UA,
        )
        if result.status >= 400:
            raise ApiError(api_error_message(result), status=result.status)
        payload = result.json()
        if not isinstance(payload, dict):
            raise ApiError("YouTube live chat did not return an object")
        next_token, timeout_ms, actions = next_live_chat_page(payload)
        if not next_token:
            return "", timeout_ms, actions, True
        return next_token, timeout_ms, actions, False

    def _get(self, url: str) -> str:
        result = self.transport.request(
            "GET",
            url,
            headers={"Accept": "text/html", **YT_HEADERS},
            allow_hosts=YOUTUBE_HOSTS,
            user_agent=YT_UA,
        )
        if result.status >= 400:
            raise ApiError(api_error_message(result), status=result.status)
        return result.body.decode("utf-8", "replace")

    def _store(
        self,
        actions: object,
        channel: str,
        stop: threading.Event,
        *,
        chat: str = "",
        fresh: bool = False,
    ) -> None:
        if not self._live(stop):
            return
        offer_chat(
            self._chat_feed,
            "youtube",
            chat_lines_from_live_chat_actions(actions),
            chat=chat,
            fresh=fresh,
        )
        inserted = self.store.add_many(donations_from_live_chat_actions(actions, channel))
        if inserted:
            self.store.log("youtube", "info", f"Stored {inserted} event(s) from the public YouTube chat.")
