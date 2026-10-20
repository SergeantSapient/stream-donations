"""Amounts and types for the Smashcast-style ledger.

Stored kinds stay specific so the same event is not recorded twice. What the
ledger, the filter, and the CSV show is one of three types: Super Chat,
Super Sticker, or Membership.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, tzinfo

from .util import as_int, parse_datetime, parse_micros

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

_SUPER_CHAT_KINDS = {"super_chat", "fan_funding", "rant", "member_milestone"}
_MEMBERSHIP_KINDS = {
    "membership",
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
# YouTube anniversary card: "Member for 2 months" or "Member for 1 year".
# It is a complementary Super Chat, so it never gets a membership price.
_ANNIVERSARY = re.compile(
    r"\bmember for\s+\d+\s+(?:months?|years?)\b",
    re.IGNORECASE,
)
_ISO_CURRENCY = re.compile(r"^[A-Z]{3}$")
# Longer prefixes first so "A$" is not read as "$".
_DISPLAY_PREFIXES = (
    ("US$", "USD"),
    ("CA$", "CAD"),
    ("HK$", "HKD"),
    ("NT$", "TWD"),
    ("NZ$", "NZD"),
    ("MX$", "MXN"),
    ("A$", "AUD"),
    ("R$", "BRL"),
    ("₱", "PHP"),
    ("€", "EUR"),
    ("£", "GBP"),
    ("₹", "INR"),
    ("¥", "JPY"),
    ("₩", "KRW"),
    ("₪", "ILS"),
    ("$", "USD"),
)

# Used when the ledger filter asks for a display type. `super_chat` is also a
# stored kind; the display meaning wins so Rumble rants and large Kicks are included.
DISPLAY_KIND_SQL = {
    "super_chat": (
        "(kind IN ('super_chat', 'fan_funding', 'rant', 'member_milestone') "
        "OR (kind = 'kicks' AND amount_value >= 500))"
    ),
    "super_sticker": (
        "(kind = 'super_sticker' "
        "OR (kind = 'kicks' AND (amount_value IS NULL OR amount_value < 500)))"
    ),
    "membership": (
        "(kind IN ('membership', 'membership_gift', "
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


def is_anniversary_text(text: object) -> bool:
    return bool(_ANNIVERSARY.search(str(text or "")))


def is_member_anniversary(row: dict) -> bool:
    """A YouTube anniversary card. Shown as a Super Chat with no price."""
    if str(row.get("kind") or "") == "member_milestone":
        return True
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    snippet = raw.get("snippet") if isinstance(raw.get("snippet"), dict) else {}
    if str(snippet.get("type") or "") == "memberMilestoneChatEvent":
        return True
    if isinstance(snippet.get("memberMilestoneChatDetails"), dict):
        return True
    for key in ("headerPrimaryText", "headerSubtext"):
        if is_anniversary_text(_plain_text(raw.get(key))):
            return True
    return False


def quote(row: dict, membership_price: float | None, rates: dict[str, float]) -> Quote:
    """Amount in its own currency, plus USD, for one stored donation."""
    kind = str(row.get("kind") or "")
    label = display_label(kind, row.get("amount_value"), row.get("currency"))
    if kind == "gift_membership_received":
        # The recipient's notice is not a second payment.
        return Quote(None, None, None, label)
    if is_member_anniversary(row):
        # Complementary Super Chat. YouTube leaves the amount blank.
        # A Kick subscription celebration is the same kind and shows $0.00.
        if str(row.get("platform") or "") == "kick":
            return Quote(0.0, "USD", 0.0, DISPLAY_LABELS["super_chat"])
        return Quote(None, None, None, DISPLAY_LABELS["super_chat"])
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
    if currency is None and str(row.get("platform") or "") == "youtube":
        recovered_amount, recovered_currency = youtube_paid_amount(
            row.get("raw") if isinstance(row.get("raw"), dict) else {}
        )
        if recovered_currency:
            currency = recovered_currency
            if recovered_amount is not None:
                amount = recovered_amount
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


def parse_display_amount(text: str) -> tuple[float | None, str | None]:
    """Amount and ISO currency from a YouTube display string such as ₱50.00."""
    if not text:
        return None, None
    currency = None
    for code in re.findall(r"\b[A-Z]{3}\b", text):
        if _ISO_CURRENCY.fullmatch(code):
            currency = code
            break
    stripped = text
    if currency is None:
        for prefix, code in _DISPLAY_PREFIXES:
            if prefix in stripped:
                currency = code
                stripped = stripped.replace(prefix, "", 1)
                break
    match = re.search(r"\d[\d.,]*", stripped)
    if not match:
        return None, currency
    return _parse_display_number(match.group(0)), currency


def youtube_paid_amount(node: object) -> tuple[float | None, str | None]:
    """Prefer amountMicros plus an ISO currency. The display string is the fallback.

    Public chat usually shows the money as purchaseAmountText. The Data API,
    and any payload that includes it, puts the same purchase in amountMicros
    and currency. Micros are the exact value.
    """
    if not isinstance(node, dict):
        return None, None
    nodes = _paid_nodes(node)
    for item in nodes:
        micros, amount = parse_micros(item.get("amountMicros"))
        code = str(item.get("currency") or "").strip().upper()
        if amount is not None and _ISO_CURRENCY.fullmatch(code):
            return round(amount, 2), code
    display_amount = None
    display_currency = None
    for item in nodes:
        for key in ("purchaseAmountText", "amountDisplayString", "displayString"):
            parsed_amount, parsed_currency = parse_display_amount(_plain_text(item.get(key)))
            if parsed_amount is None and parsed_currency is None:
                continue
            display_amount = parsed_amount if parsed_amount is not None else display_amount
            display_currency = parsed_currency or display_currency
            if display_amount is not None and display_currency is not None:
                return display_amount, display_currency
    if display_amount is not None or display_currency is not None:
        return display_amount, display_currency
    return None, None


def _paid_nodes(node: dict) -> list[dict]:
    found = [node]
    snippet = node.get("snippet") if isinstance(node.get("snippet"), dict) else None
    if snippet is not None:
        found.append(snippet)
    for item in list(found):
        for key in ("superChatDetails", "superStickerDetails", "fanFundingEventDetails"):
            details = item.get(key)
            if isinstance(details, dict):
                found.append(details)
    return found


def _parse_display_number(raw: str) -> float | None:
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


def _plain_text(node: object) -> str:
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


# ISO 4217 minor units (Table A.1, active codes as of 2026-01-01). Codes not
# listed use 2. Kicks are whole coins.
ZERO_DECIMAL_CURRENCIES = frozenset({
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF",
    "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF", "KICKS",
})
THREE_DECIMAL_CURRENCIES = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})
FOUR_DECIMAL_CURRENCIES = frozenset({"CLF", "UYW"})


def currency_decimals(currency: object) -> int:
    """Digits after the point for this currency. Unknown or missing is 2."""
    code = str(currency or "").strip().upper()
    if code in ZERO_DECIMAL_CURRENCIES:
        return 0
    if code in THREE_DECIMAL_CURRENCIES:
        return 3
    if code in FOUR_DECIMAL_CURRENCIES:
        return 4
    return 2


def raw_amount(row: dict) -> tuple[float | None, str | None]:
    """The amount as it was sent, in its own currency, and that currency.

    Kicks stay a coin count with currency KICKS. Nothing is converted and no
    membership price is assumed: a row without a paid amount has none.
    """
    kind = str(row.get("kind") or "")
    if kind == "gift_membership_received":
        return None, None
    amount = _number(row.get("amount_value"))
    currency = str(row.get("currency") or "").strip().upper() or None
    if kind == "kicks" or currency == "KICKS":
        return amount, ("KICKS" if amount is not None else None)
    if is_member_anniversary(row):
        if str(row.get("platform") or "") == "kick":
            return 0.0, "USD"
        return None, None
    if currency is None and str(row.get("platform") or "") == "youtube":
        recovered_amount, recovered_currency = youtube_paid_amount(
            row.get("raw") if isinstance(row.get("raw"), dict) else {}
        )
        if recovered_currency:
            currency = str(recovered_currency).upper()
            if recovered_amount is not None:
                amount = recovered_amount
    if amount is None:
        return None, None
    return amount, currency


def format_raw_amount(amount: object, currency: object) -> str:
    """Just the number: no symbol, no code, no thousands separator."""
    number = _number(amount)
    if number is None:
        return ""
    return f"{number:.{currency_decimals(currency)}f}"


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
