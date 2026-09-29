"""Turn official API payloads into ledger rows.

Each function is pure: it does not talk to the network. Dedupe keys are stable
across polls so the same event is stored once.
"""

from __future__ import annotations

from dataclasses import dataclass

from .kinds import KIND_LABELS, PLATFORM_LABELS
import json

from .util import (
    amount_from_cents,
    as_int,
    channel_id_value,
    parse_micros,
    stable_key,
    to_utc_iso,
)


@dataclass(frozen=True)
class Donation:
    dedupe_key: str
    platform: str
    kind: str
    external_id: str
    channel: str
    author_name: str
    author_id: str
    message: str
    amount_value: float | None
    amount_micros: int | None
    currency: str | None
    created_at: str
    raw: dict

    def __post_init__(self) -> None:
        if self.platform not in PLATFORM_LABELS:
            raise ValueError(f"unknown platform {self.platform}")
        if self.kind not in KIND_LABELS:
            raise ValueError(f"unknown kind {self.kind}")


def normalize_super_chat_event(item: dict) -> Donation | None:
    """One row from YouTube superChatEvents.list.

    That method returns both Super Chats and Super Stickers for the signed-in
    channel over the previous 30 days. Stickers set isSuperStickerEvent.
    """
    if not isinstance(item, dict):
        return None
    snippet = item.get("snippet") or {}
    if not isinstance(snippet, dict):
        return None
    supporter = snippet.get("supporterDetails") or {}
    if not isinstance(supporter, dict):
        supporter = {}
    sticker = bool(snippet.get("isSuperStickerEvent"))
    meta = snippet.get("superStickerMetadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    comment = str(snippet.get("commentText") or "")
    if sticker:
        message = comment or str(meta.get("altText") or "Super Sticker")
        kind = "super_sticker"
    else:
        message = comment
        kind = "super_chat"
    micros, amount = parse_micros(snippet.get("amountMicros"))
    external_id = str(item.get("id") or "")
    dedupe = (
        "youtube:superChatEvent:" + external_id
        if external_id
        else stable_key(
            "youtube:superChatEvent:",
            [supporter.get("channelId"), snippet.get("createdAt"), micros, message],
        )
    )
    return Donation(
        dedupe_key=dedupe,
        platform="youtube",
        kind=kind,
        external_id=external_id,
        channel=str(snippet.get("channelId") or ""),
        author_name=str(supporter.get("displayName") or ""),
        author_id=str(supporter.get("channelId") or ""),
        message=message,
        amount_value=amount,
        amount_micros=micros,
        currency=(str(snippet.get("currency")) if snippet.get("currency") else None),
        created_at=to_utc_iso(snippet.get("createdAt")),
        raw=item,
    )


_CHAT_KINDS = {
    "newSponsorEvent": "membership",
    "memberMilestoneChatEvent": "member_milestone",
    "membershipGiftingEvent": "membership_gift",
    "giftMembershipReceivedEvent": "gift_membership_received",
    "fanFundingEvent": "fan_funding",
}


def normalize_live_chat_message(item: dict, channel_id: str = "") -> Donation | None:
    """Membership and gift events from liveChatMessages.list.

    Super Chats and Super Stickers are intentionally ignored here. Those are
    read from superChatEvents.list, which has a stable id for the same purchase.
    """
    if not isinstance(item, dict):
        return None
    snippet = item.get("snippet") or {}
    if not isinstance(snippet, dict):
        return None
    kind = _CHAT_KINDS.get(str(snippet.get("type") or ""))
    if not kind:
        return None
    author = item.get("authorDetails") or {}
    if not isinstance(author, dict):
        author = {}
    message = _live_chat_message(kind, snippet)
    external_id = str(item.get("id") or "")
    dedupe = (
        "youtube:liveChat:" + external_id
        if external_id
        else stable_key(
            "youtube:liveChat:",
            [kind, author.get("channelId"), snippet.get("publishedAt"), message],
        )
    )
    return Donation(
        dedupe_key=dedupe,
        platform="youtube",
        kind=kind,
        external_id=external_id,
        channel=channel_id or channel_id_value(snippet.get("authorChannelId")),
        author_name=str(author.get("displayName") or ""),
        author_id=channel_id_value(author.get("channelId") or snippet.get("authorChannelId")),
        message=message,
        amount_value=None,
        amount_micros=None,
        currency=None,
        created_at=to_utc_iso(snippet.get("publishedAt")),
        raw=item,
    )


def _live_chat_message(kind: str, snippet: dict) -> str:
    display = str(snippet.get("displayMessage") or "")
    if kind == "membership":
        details = snippet.get("newSponsorDetails") or {}
        level = str(details.get("memberLevelName") or "") if isinstance(details, dict) else ""
        upgrade = bool(details.get("isUpgrade")) if isinstance(details, dict) else False
        if display:
            return display
        if upgrade:
            return f"Upgraded membership{(' to ' + level) if level else ''}"
        return f"New member{(' · ' + level) if level else ''}"
    if kind == "member_milestone":
        details = snippet.get("memberMilestoneChatDetails") or {}
        if not isinstance(details, dict):
            return display
        parts: list[str] = []
        month = as_int(details.get("memberMonth"))
        if month:
            parts.append(f"Member for {month} months")
        level = str(details.get("memberLevelName") or "")
        if level:
            parts.append(level)
        comment = str(details.get("userComment") or "")
        if comment:
            parts.append(comment)
        return " · ".join(parts) or display
    if kind == "membership_gift":
        details = snippet.get("membershipGiftingDetails") or {}
        if not isinstance(details, dict):
            return display or "Gifted memberships"
        count = as_int(details.get("giftMembershipsCount")) or 0
        level = str(details.get("giftMembershipsLevelName") or "")
        noun = "membership" if count == 1 else "memberships"
        text = f"Gifted {count} {noun}"
        if level:
            text += f" ({level})"
        return text
    if kind == "gift_membership_received":
        details = snippet.get("giftMembershipReceivedDetails") or {}
        level = ""
        if isinstance(details, dict):
            level = str(details.get("memberLevelName") or "")
        if display:
            return display
        return f"Received a gift membership{(' (' + level + ')') if level else ''}"
    return display


def rumble_donations(payload: dict) -> list[Donation]:
    """Rants, subscriptions, and gifted subs from the Rumble Live Stream API.

    Followers and ordinary chat messages are not donations and are skipped.
    The API returns the latest item twice (latest_* and recent_*); dedupe keys
    collapse those copies.
    """
    if not isinstance(payload, dict):
        return []
    channel = str(payload.get("channel_name") or payload.get("username") or "")
    found: list[Donation] = []

    def visit(node: object, parent_key: str) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, parent_key)
            return
        if not isinstance(node, dict):
            return
        donation = _classify_rumble(node, parent_key, channel)
        if donation is not None:
            found.append(donation)
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                visit(value, str(key))

    visit(payload, "")
    unique: dict[str, Donation] = {}
    for donation in found:
        unique[donation.dedupe_key] = donation
    return list(unique.values())


_RANT_PARENTS = {"latest_rant", "recent_rants"}
_SUB_PARENTS = {"latest_subscriber", "recent_subscribers"}
_GIFT_PARENTS = {"latest_gifted_sub", "recent_gifted_subs"}


def _classify_rumble(node: dict, parent_key: str, channel: str) -> Donation | None:
    if parent_key in _GIFT_PARENTS or (
        parent_key not in _RANT_PARENTS | _SUB_PARENTS and _is_rumble_gift(node)
    ):
        return _rumble_gift(node, channel)
    if parent_key in _SUB_PARENTS or (
        parent_key not in _RANT_PARENTS | _GIFT_PARENTS and _is_rumble_sub(node)
    ):
        return _rumble_sub(node, channel)
    if parent_key in _RANT_PARENTS or _is_rumble_rant(node):
        return _rumble_rant(node, channel)
    return None


def _is_rumble_gift(node: dict) -> bool:
    return "total_gifts" in node and "purchased_by" in node


def _is_rumble_sub(node: dict) -> bool:
    return "subscribed_on" in node and ("username" in node or "user" in node)


def _is_rumble_rant(node: dict) -> bool:
    if _is_rumble_sub(node) or _is_rumble_gift(node):
        return False
    has_amount = "amount_cents" in node or "amount_dollars" in node
    has_user = "username" in node or "user" in node
    return has_amount and "text" in node and has_user and (
        "created_on" in node or "expires_on" in node
    )


def _rumble_user(node: dict) -> str:
    return str(node.get("username") or node.get("user") or "")


def _rumble_rant(node: dict, channel: str) -> Donation | None:
    author = _rumble_user(node)
    if not author:
        return None
    if "amount_cents" not in node and "amount_dollars" not in node:
        return None
    micros, amount = amount_from_cents(node.get("amount_cents"), node.get("amount_dollars"))
    created = to_utc_iso(node.get("created_on") or node.get("expires_on"))
    text = str(node.get("text") or "")
    return Donation(
        dedupe_key=stable_key(
            "rumble:rant:",
            [author, node.get("created_on"), node.get("amount_cents"), node.get("amount_dollars"), text],
        ),
        platform="rumble",
        kind="rant",
        external_id="",
        channel=channel,
        author_name=author,
        author_id="",
        message=text,
        amount_value=amount,
        amount_micros=micros,
        currency="USD",
        created_at=created,
        raw=node,
    )


def _rumble_sub(node: dict, channel: str) -> Donation | None:
    author = _rumble_user(node)
    subscribed = str(node.get("subscribed_on") or "")
    if not author or not subscribed:
        return None
    micros, amount = amount_from_cents(node.get("amount_cents"), node.get("amount_dollars"))
    return Donation(
        dedupe_key=stable_key(
            "rumble:sub:",
            [author, subscribed, node.get("amount_cents"), node.get("amount_dollars")],
        ),
        platform="rumble",
        kind="subscription",
        external_id="",
        channel=channel,
        author_name=author,
        author_id="",
        message="Subscribed",
        amount_value=amount,
        amount_micros=micros,
        currency="USD" if amount is not None else None,
        created_at=to_utc_iso(subscribed),
        raw=node,
    )


def _rumble_gift(node: dict, channel: str) -> Donation | None:
    author = str(node.get("purchased_by") or "")
    total = as_int(node.get("total_gifts"))
    if not author or total is None:
        return None
    # remaining_gifts changes as gifts are assigned, so it is not part of the key.
    created = to_utc_iso(
        node.get("created_on") or node.get("gifted_on") or node.get("purchased_on")
    )
    gift_type = str(node.get("gift_type") or "")
    noun = "subscription" if total == 1 else "subscriptions"
    message = f"Gifted {total} {noun}"
    if gift_type:
        message += f" ({gift_type})"
    micros, amount = amount_from_cents(node.get("amount_cents"), node.get("amount_dollars"))
    return Donation(
        dedupe_key=stable_key(
            "rumble:gift:",
            [author, total, gift_type, node.get("video_id"), node.get("created_on")],
        ),
        platform="rumble",
        kind="gifted_subscriptions",
        external_id=str(node.get("video_id") or ""),
        channel=channel,
        author_name=author,
        author_id="",
        message=message,
        amount_value=amount,
        amount_micros=micros,
        currency="USD" if amount is not None else None,
        created_at=created,
        raw=node,
    )


def normalize_kick_event(event_type: str, message_id: str, body: dict) -> Donation | None:
    """Map a verified Kick webhook body to a donation, or None if it is not one."""
    if not isinstance(body, dict):
        return None
    event_type = (event_type or "").strip()
    if event_type == "kicks.gifted":
        return _kick_kicks(message_id, body)
    if event_type == "channel.subscription.new":
        return _kick_subscription(message_id, body, "subscription", "Subscribed")
    if event_type == "channel.subscription.renewal":
        return _kick_subscription(message_id, body, "subscription_renewal", "Renewed")
    if event_type == "channel.subscription.gifts":
        return _kick_gifts(message_id, body)
    return None


def _kick_identity(person: object) -> tuple[str, str]:
    if not isinstance(person, dict):
        return "", ""
    if person.get("is_anonymous"):
        return "Anonymous", ""
    name = person.get("username") or person.get("name") or ""
    user_id = person.get("user_id")
    return str(name or ""), "" if user_id is None else str(user_id)


def _kick_channel(body: dict) -> str:
    broadcaster = body.get("broadcaster") or {}
    if not isinstance(broadcaster, dict):
        return ""
    return str(broadcaster.get("channel_slug") or broadcaster.get("username") or "")


def _kick_key(event_type: str, message_id: str, body: dict) -> str:
    if message_id:
        return f"kick:{event_type}:{message_id}"
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return stable_key(f"kick:{event_type}:", [canonical])


def _duration_phrase(prefix: str, duration: object) -> str:
    months = as_int(duration)
    if not months:
        return prefix
    unit = "month" if months == 1 else "months"
    return f"{prefix} ({months} {unit})"


def _kick_kicks(message_id: str, body: dict) -> Donation | None:
    sender_name, sender_id = _kick_identity(body.get("sender"))
    gift = body.get("gift") or {}
    if not isinstance(gift, dict):
        gift = {}
    name = str(gift.get("name") or "")
    text = str(gift.get("message") or "")
    if name and text and text != name:
        message = f"{name}: {text}"
    else:
        message = text or name
    amount = as_int(gift.get("amount"))
    return Donation(
        dedupe_key=_kick_key("kicks.gifted", message_id, body),
        platform="kick",
        kind="kicks",
        external_id=message_id,
        channel=_kick_channel(body),
        author_name=sender_name,
        author_id=sender_id,
        message=message,
        amount_value=None if amount is None else float(amount),
        amount_micros=None,
        currency="KICKS" if amount is not None else None,
        created_at=to_utc_iso(body.get("created_at")),
        raw=body,
    )


def _kick_subscription(message_id: str, body: dict, kind: str, prefix: str) -> Donation | None:
    name, user_id = _kick_identity(body.get("subscriber"))
    event = "channel.subscription.new" if kind == "subscription" else "channel.subscription.renewal"
    return Donation(
        dedupe_key=_kick_key(event, message_id, body),
        platform="kick",
        kind=kind,
        external_id=message_id,
        channel=_kick_channel(body),
        author_name=name,
        author_id=user_id,
        message=_duration_phrase(prefix, body.get("duration")),
        amount_value=None,
        amount_micros=None,
        currency=None,
        created_at=to_utc_iso(body.get("created_at")),
        raw=body,
    )


def _kick_gifts(message_id: str, body: dict) -> Donation | None:
    name, user_id = _kick_identity(body.get("gifter"))
    if not name:
        name = "Anonymous"
    giftees = body.get("giftees") or []
    names: list[str] = []
    if isinstance(giftees, list):
        for person in giftees:
            person_name, _person_id = _kick_identity(person)
            if person_name and person_name != "Anonymous":
                names.append(person_name)
    count = len(giftees) if isinstance(giftees, list) and giftees else 1
    noun = "subscription" if count == 1 else "subscriptions"
    message = f"Gifted {count} {noun}"
    if names:
        message += " to " + ", ".join(names)
    return Donation(
        dedupe_key=_kick_key("channel.subscription.gifts", message_id, body),
        platform="kick",
        kind="gifted_subscriptions",
        external_id=message_id,
        channel=_kick_channel(body),
        author_name=name,
        author_id=user_id,
        message=message,
        amount_value=None,
        amount_micros=None,
        currency=None,
        created_at=to_utc_iso(body.get("created_at")),
        raw=body,
    )
