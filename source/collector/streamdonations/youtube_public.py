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

from .normalize import Donation
from .store import Store
from .transport import Transport, api_error_message
from .util import ApiError, format_utc, redact, stable_key

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
_CURRENCY_PREFIXES = (
    ("US$", "USD"),
    ("CA$", "CAD"),
    ("A$", "AUD"),
    ("NZ$", "NZD"),
    ("HK$", "HKD"),
    ("R$", "BRL"),
    ("€", "EUR"),
    ("£", "GBP"),
    ("¥", "JPY"),
    ("₹", "INR"),
    ("$", "USD"),
)


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
    display = yt_text(renderer.get("purchaseAmountText"))
    amount, currency = parse_display_amount(display)
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
    header = yt_text(renderer.get("headerSubtext"))
    comment = yt_text(renderer.get("message"))
    kind = "member_milestone" if re.search(r"\d+\s+months?", header, re.IGNORECASE) else "membership"
    message = " · ".join(part for part in (header, comment) if part)
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


def parse_display_amount(text: str) -> tuple[float | None, str | None]:
    if not text:
        return None, None
    currency = None
    for code in re.findall(r"\b[A-Z]{3}\b", text):
        currency = code
        break
    stripped = text
    if currency is None:
        for prefix, code in _CURRENCY_PREFIXES:
            if prefix in stripped:
                currency = code
                stripped = stripped.replace(prefix, "", 1)
                break
    match = re.search(r"\d[\d.,]*", stripped)
    if not match:
        return None, currency
    return _parse_number(match.group(0)), currency


def _parse_number(raw: str) -> float | None:
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        fraction = raw.split(",")[-1]
        if len(fraction) == 2 and raw.count(",") == 1:
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


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

    def __init__(self, store: Store, transport: Transport):
        self.store = store
        self.transport = transport
        self.detail = ""
        self.error = ""
        self._target = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def ensure(self) -> None:
        target = self.store.get_setting("youtube_watch")
        if target == self._target and (not target or (self._thread and self._thread.is_alive())):
            return
        self._stop.set()
        self._stop = threading.Event()
        self._target = target
        self.detail = ""
        self.error = ""
        if not target:
            return
        stop = self._stop
        self._thread = threading.Thread(
            target=self._loop, args=(target, stop), name="youtube-public-chat", daemon=True
        )
        self._thread.start()

    def _loop(self, target: str, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                video_id = self._resolve(target)
                session = self._bootstrap(video_id)
                channel = str(session.get("channel") or "")
                self._store(session.get("actions") or [], channel)
                continuation = str(session["continuation"])
                timeout_ms = int(session.get("timeout_ms") or 5000)
                self.error = ""
                self.detail = (
                    f"Public chat for {video_id}"
                    + (f" ({channel})" if channel else "")
                    + ". Super Chats here do not need you to own the channel or sign in with Google."
                )
                self.store.set_status("youtube", "ok", self.detail)
                while not stop.is_set() and continuation:
                    stop.wait(max(1.0, min(timeout_ms / 1000, 20.0)))
                    if stop.is_set():
                        return
                    continuation, timeout_ms, actions, ended = self._poll(session, continuation)
                    self._store(actions, channel)
                    if ended:
                        self.detail = f"{video_id} has no live chat right now."
                        self.store.set_status("youtube", "idle", self.detail)
                        stop.wait(30)
                        break
            except TimeoutError:
                continue
            except Exception as exc:
                self.error = redact(str(exc))
                self.detail = ""
                self.store.log("youtube", "error", self.error)
                self.store.set_status("youtube", "error", self.error, error=self.error)
                stop.wait(15)

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

    def _store(self, actions: object, channel: str) -> None:
        inserted = self.store.add_many(donations_from_live_chat_actions(actions, channel))
        if inserted:
            self.store.log("youtube", "info", f"Stored {inserted} event(s) from the public YouTube chat.")
