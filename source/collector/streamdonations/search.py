"""Ledger search: one box, with optional field prefixes.

A bare word matches the donor, message, channel, note, platform, type, currency,
or amount. Prefixes narrow that:

    @name     donor
    $4.99     exact amount, original value or USD
    #chat     Super Chat
    #sticker  Super Sticker
    #member   Membership
    !text     message

Several tokens all have to match. Repeated type or amount prefixes match any
one of those values.
"""

from __future__ import annotations

import re

from .money import DISPLAY_LABELS, display_label

_MONEY = re.compile(r"\d+(?:\.\d+)?")
_TYPE_WORDS = {
    "chat": "super_chat",
    "superchat": "super_chat",
    "sticker": "super_sticker",
    "supersticker": "super_sticker",
    "member": "membership",
    "members": "membership",
    "membership": "membership",
}
_LABEL_TO_TYPE = {label.casefold(): key for key, label in DISPLAY_LABELS.items()}


def matches_ledger_query(row: dict, query: str) -> bool:
    text = str(query or "").strip()
    if not text:
        return True
    donors: list[str] = []
    messages: list[str] = []
    types: list[str] = []
    amounts: list[int] = []
    words: list[str] = []
    unknown_type = False
    for prefix, value in _tokens(text):
        if prefix == "@":
            donors.append(value)
        elif prefix == "!":
            messages.append(value)
        elif prefix == "#":
            kind = _TYPE_WORDS.get(_type_key(value))
            if kind:
                types.append(kind)
            else:
                unknown_type = True
        elif prefix == "$":
            cents = _money_cents(value)
            if cents is None:
                return False
            amounts.append(cents)
        else:
            words.append(value)
    if unknown_type and not types:
        return False
    donor = _donor_text(row.get("author_name"))
    message = str(row.get("message") or "")
    for name in donors:
        if _donor_text(name).casefold() not in donor.casefold():
            return False
    for snippet in messages:
        if snippet.casefold() not in message.casefold():
            return False
    if types and _shown_type(row) not in types:
        return False
    if amounts and not any(cents in amounts for cents in _amount_cents(row)):
        return False
    if words:
        haystack = _haystack(row)
        for word in words:
            if word.casefold() not in haystack:
                return False
    return True


def _tokens(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    index = 0
    length = len(text)
    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break
        prefix = ""
        if text[index] in "@!#$":
            prefix = text[index]
            index += 1
        if index < length and text[index] == '"':
            index += 1
            end = text.find('"', index)
            if end < 0:
                value = text[index:]
                index = length
            else:
                value = text[index:end]
                index = end + 1
        else:
            end = index
            while end < length and not text[end].isspace():
                end += 1
            value = text[index:end]
            index = end
        if value:
            found.append((prefix, value))
    return found


def _type_key(value: str) -> str:
    return re.sub(r"[\s_-]+", "", value.casefold())


def _donor_text(value: object) -> str:
    return str(value or "").strip().lstrip("@")


def _shown_type(row: dict) -> str:
    label = display_label(str(row.get("kind") or ""), row.get("amount_value"), row.get("currency"))
    return _LABEL_TO_TYPE.get(label.casefold(), "")


def _money_cents(value: object) -> int | None:
    text = str(value or "").strip().replace(",", "")
    if not text or _MONEY.fullmatch(text) is None:
        return None
    return int(round(float(text) * 100))


def _amount_cents(row: dict) -> set[int]:
    found: set[int] = set()
    for key in ("amount_value", "quoted_amount", "amount_usd"):
        cents = _money_cents(row.get(key))
        if cents is not None:
            found.add(cents)
    return found


def _haystack(row: dict) -> str:
    parts = [
        _donor_text(row.get("author_name")),
        str(row.get("author_name") or ""),
        str(row.get("message") or ""),
        str(row.get("channel") or ""),
        str(row.get("note") or ""),
        str(row.get("platform") or ""),
        str(row.get("platform_label") or ""),
        str(row.get("kind") or ""),
        str(row.get("kind_label") or ""),
        display_label(str(row.get("kind") or ""), row.get("amount_value"), row.get("currency")),
        str(row.get("currency") or ""),
        str(row.get("quoted_currency") or ""),
    ]
    for key in ("amount_value", "quoted_amount", "amount_usd"):
        cents = _money_cents(row.get(key))
        if cents is not None:
            parts.append(f"{cents / 100:.2f}")
    return " ".join(parts).casefold()
