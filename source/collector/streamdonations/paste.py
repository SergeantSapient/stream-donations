"""Parse a live-chat paste into the manual-entry fields.

Regex strings match livetill src/lib/parser (config.ts and parse.ts).
Membership bundles use assumed_membership_amount: the saved price times the
count, except Rumble, which prices the first sub at that price and each
extra sub at $5. At $4.99 the Rumble ladder matches Live Till. An empty
saved price leaves those amounts blank instead of inventing $4.99.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

from .money import assumed_membership_amount, to_usd

DEFAULT_MEMBERSHIP_PRICE = 4.99
MEMBERSHIP_CURRENCY = "USD"
PASTE_LIMIT = 8000
KICK_SUPER_CHAT_MIN_COINS = 500

# Longest symbol is tried first. Insertion order matches Live Till.
_CURRENCIES = {
    "$": "USD",
    "CA$": "CAD",
    "NZ$": "NZD",
    "₱": "PHP",
    "A$": "AUD",
    "€": "EUR",
    "£": "GBP",
    "ARS ": "ARS",
    "DKK ": "DKK",
    "CLP ": "CLP",
    "NT$": "TWD",
    "MYR ": "MYR",
    "IDR ": "IDR",
    "BGN ": "BGN",
    "NOK ": "NOK",
    "¥": "JPY",
    "CRC ": "CRC",
    "AED ": "AED",
    "MX$": "MXN",
    "RSD ": "RSD",
    "R$": "BRL",
    "₹": "INR",
}
_CURRENCY_SYMBOLS = sorted(_CURRENCIES, key=len, reverse=True)

_EXCLUDE_PATTERNS = (
    r"^\d+$",
    r"^Smashers$",
    r"^Reply(\s+\d+)?$",
    r"^New\s+member$",
    r"^#\d+$",
    r"^Like(\s+\d+)?$",
    r"^They've gifted \d+ subscription(s)? in the channel\.$",
    r"^Welcome to .+$",
    r"^Member$",
    r"^Member \(\d+\s+(months?|years?)\)$",
    r"^Member for \d+\s+months?$",
    r"^@.+$",
    r"^\d{1,2}:\d{2}(:\d{2})?$",
)

_EXCLUDE = re.compile(rf"^(?:{'|'.join(_EXCLUDE_PATTERNS)})$", re.ASCII)
# "Sent 5 <channel> gift memberships" or "Sent 5 <channel> memberships".
# The count is the number after Sent. Case does not matter, and "gift" is optional.
_GIFT_MEMBERSHIP = re.compile(
    r"^Sent\s+(\d+)\s+.*\bmemberships?\b\s*$",
    re.ASCII | re.IGNORECASE,
)
_NEW_MEMBERSHIP = re.compile(r"^New\s+member$|^Welcome to .+$", re.ASCII)
# Live Till compiles this and never matches it. The type is the "Super Sticker" line.
re.compile(r"^[A-Z][a-z0-9\s]*$", re.ASCII)
_KICK_GIFT = re.compile(r"^Gifted \d+ subscriptions? to the community!", re.ASCII)
# "sent Cat Jam" is the older copy. Kick now also copies a bare "sent".
_KICK_STICKER = re.compile(r"^sent(?: [A-Z][A-Za-z\s]*)?$", re.ASCII)
_RUMBLE_GIFT = re.compile(r"^Gifted \d+ subscriptions? to (?!the community!).+", re.ASCII)
_KICK_COIN_LINE = re.compile(r"^\d{1,3}(?:,\d{3})+$|^\d+$", re.ASCII)
_AMOUNT = re.compile(r"^\d*\.?\d+$", re.ASCII)
_COUNT = re.compile(r"(\d+)", re.ASCII)
_EXTENSION = re.compile(r"^\[([^\]]+)\]\s*(.+)$")
_DASH = re.compile(r"\s+[-\u2013\u2014]\s+")
_SUPER_STICKER_LABEL = re.compile(r"^super sticker$", re.IGNORECASE)
_SUPER_CHAT_LABEL = re.compile(r"^super chat$", re.IGNORECASE)
_RANT_LABEL = re.compile(r"^rant$", re.IGNORECASE)
_MEMBER_CHROME = re.compile(r"^Member(\s|$)")
_RANK = re.compile(r"^#\d+$", re.ASCII)
_LIKE_CHROME = re.compile(r"^Like(\s|$)", re.IGNORECASE)
_TIMESTAMP = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$", re.ASCII)
_LINE_BREAKS = re.compile(r"[\r\n\u2028\u2029]+")
_SPACES = re.compile(r"[ \t]+")
_ODD_SPACES = re.compile(r"[\u00a0\u202f\u2007\u2009\u200a]")
_POUNDS = re.compile("[\u00a3\uffe1\u20a4]")
_WHITESPACE = re.compile(r"\s")
_SENT_PREFIX = re.compile(r"^sent ")

_PLATFORM_IDS = {"YouTube": "youtube", "Rumble": "rumble", "Kick": "kick"}
_KIND_IDS = {
    "Super Chat": "super_chat",
    "Super Sticker": "super_sticker",
    "Membership": "membership",
}
_CHAT_PLATFORMS = {"youtube": "YouTube", "kick": "Kick", "rumble": "Rumble"}


@dataclass(frozen=True)
class MembershipDonation:
    type: str
    count: int
    description: str


@dataclass(frozen=True)
class ParseOutput:
    donor: str
    amount: str
    currency: str
    message: str
    membership: MembershipDonation | None
    donation_type: str
    platform: str


def one_line(value: str) -> str:
    """Collapse a Super Chat message onto one line."""
    return _SPACES.sub(" ", _LINE_BREAKS.sub(" ", value)).strip()


def parse_entry(text: str, membership_price: float | None = DEFAULT_MEMBERSHIP_PRICE) -> ParseOutput:
    """Parse one paste. Omitting the price uses Live Till's $4.99 default.

    Pass None when the membership price setting is empty. Gift and new-member
    amounts stay blank in that case.
    """
    if membership_price is not None:
        membership_price = round(float(membership_price), 2)
    empty = ParseOutput("", "", MEMBERSHIP_CURRENCY, "", None, "Super Chat", "YouTube")
    if not text or not text.strip():
        return empty
    compact = _parse_extension(text)
    if compact is not None:
        return compact
    parsed = _parse_single(text, membership_price)
    return replace(parsed, platform=_detect_platform(text))


def paste_for_form(text: object, membership_price: float | None, rates: dict[str, float]) -> dict[str, object]:
    """Fields for the manual form. Amount USD is blank when no rate exists."""
    if not isinstance(text, str):
        raise ValueError("Paste must be text.")
    if len(text) > PASTE_LIMIT:
        raise ValueError("Paste is limited to 8000 characters.")
    parsed = parse_entry(text, membership_price)
    platform = _PLATFORM_IDS.get(parsed.platform)
    kind = _KIND_IDS.get(parsed.donation_type)
    if platform is None or kind is None:
        raise ValueError("Paste could not be read.")
    amount_usd = None
    if parsed.amount:
        usd = to_usd(float(parsed.amount), parsed.currency, rates)
        if usd is not None:
            amount_usd = f"{usd:.2f}"
    count = parsed.membership.count if parsed.membership is not None else None
    return {
        "donor": parsed.donor,
        "platform": platform,
        "kind": kind,
        "amount": parsed.amount,
        "currency": parsed.currency,
        "amount_usd": amount_usd,
        "membership_count": count,
        "message": parsed.message,
    }


def _priced(platform: str, count: int, price: float | None) -> str:
    if price is None:
        return ""
    return f"{assumed_membership_amount(platform, count, price):.2f}"


def _split_lines(text: str, *, keep_blank: bool) -> list[str]:
    parts = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n")]
    if keep_blank:
        return parts
    return [line for line in parts if line]


def _normalize_currency_token(line: str) -> str:
    token = unicodedata.normalize("NFKC", line)
    token = _ODD_SPACES.sub(" ", token)
    token = _POUNDS.sub("£", token)
    return token.strip()


def _parse_currency(line: str) -> tuple[str, str] | None:
    token = _normalize_currency_token(line)
    for symbol in _CURRENCY_SYMBOLS:
        if not token.startswith(symbol):
            continue
        cleaned = token[len(symbol):].strip().replace(",", "")
        if cleaned and cleaned != "." and _AMOUNT.fullmatch(cleaned):
            return f"{float(cleaned):.2f}", _CURRENCIES[symbol]
    return None


def _parse_kick_coin_line(line: str) -> int | None:
    if _KICK_COIN_LINE.fullmatch(line) is None:
        return None
    return int(line.replace(",", ""))


def _find_kick_coin_block(lines: list[str], sent_index: int) -> tuple[int, int, str] | None:
    """Coin integer after a `sent {Emote}` line. @-lines are the message, not chrome."""
    message = ""
    for index in range(sent_index + 1, len(lines)):
        line = lines[index]
        if not line:
            continue
        coins = _parse_kick_coin_line(line)
        if coins is not None:
            return index, coins, message
        if line.startswith("@") or _EXCLUDE.fullmatch(line) is None:
            if not message:
                message = line
    return None


def _youtube_gift(lines: list[str], index: int) -> tuple[str, int, int] | None:
    """The gift line, its count, and the last line index it used.

    YouTube sometimes copies the level name and the word memberships on
    separate lines. Join one following line when the pair is a gift sentence.
    """
    line = lines[index]
    match = _GIFT_MEMBERSHIP.fullmatch(line)
    end = index
    if match is None and index + 1 < len(lines) and lines[index + 1]:
        combined = f"{line} {lines[index + 1]}"
        match = _GIFT_MEMBERSHIP.fullmatch(combined)
        if match is not None:
            line = combined
            end = index + 1
    if match is None:
        return None
    return line, int(match.group(1)), end


def _count_of(line: str) -> int | None:
    match = _COUNT.search(line)
    if match is None:
        return None
    return int(match.group(1))


def _chat_platform(name: str) -> str | None:
    return _CHAT_PLATFORMS.get(name.strip().lower())


def _parse_extension(text: str) -> ParseOutput | None:
    lines = _split_lines(text, keep_blank=False)
    if not lines:
        return None
    head = _EXTENSION.fullmatch(" ".join(lines))
    if head is None:
        return None
    platform = _chat_platform(head.group(1))
    if platform is None:
        return None
    body = head.group(2)
    colon = body.find(":")
    if colon < 1:
        return None
    donor = body[:colon].strip()
    if donor.startswith("@"):
        donor = donor[1:].strip()
    if not donor:
        return None
    after = body[colon + 1:].strip()
    if not after:
        return None
    dash = _DASH.search(after)
    if dash is None:
        amount_part = after
        message = ""
    else:
        amount_part = after[:dash.start()].strip()
        message = _DASH.sub("", after[dash.start():], count=1)
    parsed = _parse_currency(amount_part)
    if parsed is None:
        return None
    amount, currency = parsed
    return ParseOutput(donor, amount, currency, one_line(message), None, "Super Chat", platform)


def _parse_single(text: str, price: float | None) -> ParseOutput:
    lines = _split_lines(text, keep_blank=True)
    donor = ""
    amount = ""
    currency = MEMBERSHIP_CURRENCY
    message = ""
    membership: MembershipDonation | None = None
    donation_type = "Super Chat"
    amount_from_currency = False
    message_locked = False
    group = 0
    group_has_content = False
    leftovers: list[tuple[str, int]] = []

    index = 0
    while index < len(lines):
        line = lines[index]
        if not line:
            if group_has_content:
                group += 1
                group_has_content = False
            index += 1
            continue
        group_has_content = True

        if not donor and _youtube_gift(lines, index) is None:
            if line.startswith("@"):
                donor = line[1:].strip()
            elif index == 0:
                donor = line
            index += 1
            continue

        if _SUPER_STICKER_LABEL.fullmatch(line):
            donation_type = "Super Sticker"
            index += 1
            continue
        if _SUPER_CHAT_LABEL.fullmatch(line):
            donation_type = "Super Chat"
            index += 1
            continue
        if _RANT_LABEL.fullmatch(line):
            donation_type = "Super Chat"
            index += 1
            continue

        # "Welcome to …" and "New member" are also chrome, so they have to be
        # claimed before the exclude list.
        if membership is None and not amount and _NEW_MEMBERSHIP.fullmatch(line):
            membership = MembershipDonation("new", 1, line.strip())
            donation_type = "Membership"
            amount = _priced("youtube", 1, price)
            currency = MEMBERSHIP_CURRENCY
            index += 1
            continue

        if _EXCLUDE.fullmatch(line):
            index += 1
            continue
        if _KICK_GIFT.match(line):
            count = _count_of(line)
            if count is not None:
                membership = MembershipDonation("gift", count, line.strip())
                donation_type = "Membership"
                amount = _priced("kick", count, price)
                currency = MEMBERSHIP_CURRENCY
            index += 1
            continue
        gift = _youtube_gift(lines, index)
        if gift is not None:
            gift_line, count, index = gift
            membership = MembershipDonation("gift", count, gift_line.strip())
            donation_type = "Membership"
            amount = _priced("youtube", count, price)
            currency = MEMBERSHIP_CURRENCY
            index += 1
            continue
        if _RUMBLE_GIFT.search(line):
            count = _count_of(line)
            if count is not None:
                membership = MembershipDonation("gift", count, line.strip())
                donation_type = "Membership"
                # First sub at the configured price, each extra at $5.
                amount = _priced("rumble", count, price)
                currency = MEMBERSHIP_CURRENCY
            index += 1
            continue
        if _KICK_STICKER.fullmatch(line):
            block = _find_kick_coin_block(lines, index)
            if block is not None:
                coin_index, coins, coin_message = block
                donation_type = "Super Chat" if coins >= KICK_SUPER_CHAT_MIN_COINS else "Super Sticker"
                amount = f"{coins / 100:.2f}"
                currency = "USD"
                emote = ""
                prefix = _SENT_PREFIX.match(line)
                if prefix is not None:
                    emote = line[prefix.end():].strip()
                message = one_line(coin_message or emote)
                message_locked = True
                index = coin_index + 1
                continue

        if membership is None or membership.type != "gift":
            parsed = _parse_currency(line)
            if parsed is not None:
                amount, currency = parsed
                membership = None
                if donation_type != "Super Sticker":
                    donation_type = "Super Chat"
                amount_from_currency = True
                index += 1
                continue

        leftovers.append((line, group))
        index += 1

    if not message and not message_locked:
        after_header = next((item for item in leftovers if item[1] >= 1), None)
        message = after_header[0] if after_header is not None else (leftovers[0][0] if leftovers else "")

    if membership is not None and membership.type == "new":
        payload = next((item for item in leftovers if item[1] >= 1 or _WHITESPACE.search(item[0])), None)
        if payload is not None:
            donation_type = "Super Chat"
            membership = None
            if not amount_from_currency:
                amount = ""
                currency = MEMBERSHIP_CURRENCY
            later = next((item for item in leftovers if item[1] >= 1), None)
            message = one_line(later[0] if later is not None else payload[0])
        else:
            message = ""

    return ParseOutput(donor, amount, currency, one_line(message), membership, donation_type, "YouTube")


def _has_youtube_chrome(lines: list[str]) -> bool:
    for line in lines:
        if (
            line.startswith("@")
            or _GIFT_MEMBERSHIP.search(line)
            or _NEW_MEMBERSHIP.search(line)
            or _MEMBER_CHROME.search(line)
            or _RANK.search(line)
            or _LIKE_CHROME.search(line)
            or _TIMESTAMP.search(line)
        ):
            return True
    return False


def _detect_platform(text: str) -> str:
    raw = _split_lines(text, keep_blank=True)
    lines = [line for line in raw if line]
    for index, line in enumerate(lines):
        if _KICK_GIFT.search(line):
            return "Kick"
        if _KICK_STICKER.fullmatch(line) and _find_kick_coin_block(lines, index):
            return "Kick"
        if _GIFT_MEMBERSHIP.search(line) or _NEW_MEMBERSHIP.search(line):
            return "YouTube"
        if _RUMBLE_GIFT.search(line) or _RANT_LABEL.fullmatch(line):
            return "Rumble"
    name = raw[0] if raw else ""
    blank_after_name = len(raw) > 1 and raw[1] == ""
    if name and not name.startswith("@") and blank_after_name and not _has_youtube_chrome(lines):
        return "Rumble"
    return "YouTube"
