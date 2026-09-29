"""Amounts and types for the Smashcast-style ledger.

Stored kinds stay specific so the same event is not recorded twice. What the
ledger, the filter, and the CSV show is one of three types: Super Chat,
Super Sticker, or Membership.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, tzinfo

from .util import as_int, parse_datetime

# Kick calls 100 of its coins one US dollar. The sample ledger marks 500 coins
# ($5.00) as a Super Chat and anything smaller as a Super Sticker.
KICK_COINS_PER_DOLLAR = 100
KICK_SUPER_CHAT_DOLLARS = 5.0
# A Rumble bundle charges the single-sub price once, then exactly $5.00 per extra sub.
RUMBLE_EXTRA_SUB_DOLLARS = 5.0

DISPLAY_LABELS = {
    "super_chat": "Super Chat",
    "super_sticker": "Super Sticker",
    "membership": "Membership",
    "donation": "Donation",
}

_SUPER_CHAT_KINDS = {"super_chat", "fan_funding", "rant"}
_MEMBERSHIP_KINDS = {
    "membership",
    "member_milestone",
    "membership_gift",
    "gift_membership_received",
    "subscription",
    "subscription_renewal",
    "gifted_subscriptions",
}
_GIFT_KINDS = {"membership_gift", "gifted_subscriptions"}
# "Gifted 3 subscriptions" (Rumble, Kick) and "Sent 5 Smash JT gift memberships" (YouTube).
_GIFT_COUNT = re.compile(
    r"\b(?:gifted|sent)\s+(\d+)\b|\b(\d+)\s+(?:\S+\s+){0,6}gift memberships\b",
    re.IGNORECASE,
)

# Used when the ledger filter asks for a display type. `super_chat` is also a
# stored kind; the display meaning wins so Rumble rants and large Kicks are included.
DISPLAY_KIND_SQL = {
    "super_chat": (
        "(kind IN ('super_chat', 'fan_funding', 'rant') "
        "OR (kind = 'kicks' AND amount_value >= 500))"
    ),
    "super_sticker": (
        "(kind = 'super_sticker' "
        "OR (kind = 'kicks' AND (amount_value IS NULL OR amount_value < 500)))"
    ),
    "membership": (
        "(kind IN ('membership', 'member_milestone', 'membership_gift', "
        "'gift_membership_received', 'subscription', 'subscription_renewal', "
        "'gifted_subscriptions'))"
    ),
    "donation": "(kind = 'donation')",
}

CSV_FIELDS = [
    "Date",
    "Time",
    "Donor",
    "Amount",
    "Platform",
    "Type",
    "Currency",
    "Amount USD",
    "Message",
]


@dataclass(frozen=True)
class Quote:
    amount: float | None
    currency: str | None
    usd: float | None
    label: str


def parse_membership_price(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if number < 0:
        return None
    return round(number, 2)


def display_label(kind: str, amount_value: object, currency: object) -> str:
    if kind == "kicks" or str(currency or "").upper() == "KICKS":
        dollars = _kick_dollars(amount_value)
        if dollars is None or dollars < KICK_SUPER_CHAT_DOLLARS:
            return DISPLAY_LABELS["super_sticker"]
        return DISPLAY_LABELS["super_chat"]
    if kind == "donation":
        return DISPLAY_LABELS["donation"]
    if kind in _MEMBERSHIP_KINDS:
        return DISPLAY_LABELS["membership"]
    if kind == "super_sticker":
        return DISPLAY_LABELS["super_sticker"]
    if kind in _SUPER_CHAT_KINDS:
        return DISPLAY_LABELS["super_chat"]
    return DISPLAY_LABELS["super_chat"]


def quote(row: dict, membership_price: float | None, rates: dict[str, float]) -> Quote:
    """Amount in its own currency, plus USD, for one stored donation."""
    kind = str(row.get("kind") or "")
    label = display_label(kind, row.get("amount_value"), row.get("currency"))
    if kind == "gift_membership_received":
        # The gifter's row is the payment. This is only the notice that someone received it.
        return Quote(None, None, None, label)
    if kind == "kicks" or str(row.get("currency") or "").upper() == "KICKS":
        dollars = _kick_dollars(row.get("amount_value"))
        return Quote(dollars, "USD" if dollars is not None else None, dollars, label)
    amount = _number(row.get("amount_value"))
    currency = str(row.get("currency") or "").upper() or None
    if label == DISPLAY_LABELS["membership"] and amount is None:
        if membership_price is None:
            return Quote(None, None, None, label)
        units = gift_units(row) if kind in _GIFT_KINDS else 1
        assumed = assumed_membership_amount(str(row.get("platform") or ""), units, membership_price)
        return Quote(assumed, "USD", assumed, label)
    if amount is None or currency is None:
        return Quote(amount, currency, None, label)
    return Quote(round(amount, 2), currency, to_usd(amount, currency, rates), label)


def assumed_membership_amount(platform: str, units: int, price: float) -> float:
    """Price a membership that arrived without an amount.

    One subscription is the configured price. YouTube and Kick bundles are
    that price times the count. Rumble charges the configured price for the
    first subscription and $5.00 for every additional one, so five subs at
    $4.99 are $24.99 rather than $24.95.
    """
    count = max(1, int(units))
    if platform == "rumble" and count > 1:
        return round(price + (count - 1) * RUMBLE_EXTRA_SUB_DOLLARS, 2)
    return round(price * count, 2)


def gift_units(row: dict) -> int:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    snippet = raw.get("snippet") if isinstance(raw.get("snippet"), dict) else {}
    details = snippet.get("membershipGiftingDetails") if isinstance(snippet.get("membershipGiftingDetails"), dict) else {}
    count = as_int(details.get("giftMembershipsCount"))
    if count and count > 0:
        return count
    gift = raw.get("gift_purchase_notification")
    if isinstance(gift, dict):
        total = as_int(gift.get("total_gifts"))
        if total and total > 0:
            return total
    giftees = raw.get("giftees")
    if isinstance(giftees, list) and giftees:
        return len(giftees)
    match = _GIFT_COUNT.search(str(row.get("message") or ""))
    if match:
        number = next(group for group in match.groups() if group)
        return max(1, int(number))
    text = _youtube_gift_text(raw)
    match = _GIFT_COUNT.search(text)
    if match:
        number = next(group for group in match.groups() if group)
        return max(1, int(number))
    return 1


def _youtube_gift_text(raw: dict) -> str:
    header = raw.get("header") if isinstance(raw.get("header"), dict) else {}
    inner = header.get("liveChatSponsorshipsHeaderRenderer")
    if not isinstance(inner, dict):
        inner = header
    primary = inner.get("primaryText") if isinstance(inner, dict) else None
    if isinstance(primary, str):
        return primary
    if not isinstance(primary, dict):
        return ""
    if isinstance(primary.get("simpleText"), str):
        return primary["simpleText"]
    runs = primary.get("runs")
    if not isinstance(runs, list):
        return ""
    return "".join(str(run.get("text") or "") if isinstance(run, dict) else str(run) for run in runs)


def to_usd(amount: float, currency: str, rates: dict[str, float]) -> float | None:
    code = currency.upper()
    if code == "USD":
        return round(float(amount), 2)
    per_dollar = rates.get(code)
    if not per_dollar:
        return None
    return round(float(amount) / float(per_dollar), 2)


def local_date_time(iso: str, zone: tzinfo | None = None) -> tuple[str, str]:
    parsed = parse_datetime(iso)
    if parsed is None:
        return "", ""
    if zone is None:
        zone = datetime.now().astimezone().tzinfo
    local = parsed.astimezone(zone)
    return local.strftime("%Y-%m-%d"), local.strftime("%H:%M:%S")


def money_text(amount: float | None) -> str:
    if amount is None:
        return ""
    return f"{amount:.2f}"


def _kick_dollars(amount_value: object) -> float | None:
    coins = _number(amount_value)
    if coins is None:
        return None
    return round(coins / KICK_COINS_PER_DOLLAR, 2)


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
