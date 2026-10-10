"""Stream total, goal tiers, and top donors.

The chat line matches Live Till: the next named goal, or how far the total
is past the last one. A cent short of a tier still counts as reached.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .util import display_donor, mention_donor

TIER_COUNT = 5
REACHED_EPS = 0.01
DONOR_BAR_THANKS = "Thank you for the support!"
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class GoalTier:
    enabled: bool
    amount: float | None
    name: str
    bonus: str

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "amount": self.amount,
            "name": self.name,
            "bonus": self.bonus,
        }


def round2(value: float) -> float:
    return round(float(value), 2)


def format_usd(amount: float) -> str:
    rounded = round2(amount)
    sign = "-" if rounded < 0 else ""
    return f"{sign}${abs(rounded):,.2f}"


def one_line(value: object) -> str:
    return _SPACE.sub(" ", str(value or "")).strip()


def empty_tier() -> GoalTier:
    return GoalTier(False, None, "", "")


def empty_tiers() -> list[GoalTier]:
    return [empty_tier() for _ in range(TIER_COUNT)]


def normalize_tiers(value: object) -> list[GoalTier]:
    src = value if isinstance(value, list) else []
    out = empty_tiers()
    for index in range(TIER_COUNT):
        if index >= len(src) or not isinstance(src[index], dict):
            continue
        item = src[index]
        amount = item.get("amount")
        number: float | None
        try:
            number = float(amount) if amount is not None and amount != "" else None
        except (TypeError, ValueError):
            number = None
        if number is None or number <= 0:
            number = None
        else:
            number = round2(number)
        out[index] = GoalTier(
            enabled=bool(item.get("enabled")),
            amount=number,
            name=str(item.get("name") or "")[:80],
            bonus=str(item.get("bonus") or "")[:160],
        )
    return out


def load_tiers(stored: object) -> list[GoalTier]:
    if isinstance(stored, list):
        return normalize_tiers(stored)
    text = str(stored or "").strip()
    if not text:
        return empty_tiers()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return empty_tiers()
    return normalize_tiers(parsed)


def dump_tiers(tiers: list[GoalTier]) -> str:
    return json.dumps([tier.as_dict() for tier in tiers], separators=(",", ":"))


def amount_tiers(tiers: list[GoalTier]) -> list[GoalTier]:
    active = [tier for tier in tiers if tier.enabled and tier.amount is not None and tier.amount > 0]
    return sorted(active, key=lambda tier: tier.amount or 0)


def named_tiers(tiers: list[GoalTier]) -> list[GoalTier]:
    return [tier for tier in amount_tiers(tiers) if tier.name.strip()]


def is_tier_reached(total: float, amount: float) -> bool:
    return round2(total) >= round2(amount) - REACHED_EPS


def reached_tier(total: float, enabled: bool, tiers: list[GoalTier]) -> int:
    """How many enabled goal amounts this total has reached, from 0 to 5.

    The count follows the amounts in ascending order, not the order of the
    form. A disabled row, or a row with no positive amount, does not count.
    A cent short of an amount still counts. Tiers that are off stay at 0.
    """
    if not enabled:
        return 0
    count = 0
    for tier in amount_tiers(tiers):
        if tier.amount is None or not is_tier_reached(total, tier.amount):
            break
        count += 1
    return count


def tier_breach(rows: list[dict], enabled: bool, tiers: list[GoalTier], tier: int) -> dict | None:
    """The gift that first reached this tier. Tier 0 has none.

    The result is that gift's donor, platform, and USD amount. A missing
    amount stays missing rather than becoming zero.
    """
    if tier <= 0 or not enabled:
        return None
    pending = amount_tiers(tiers)
    if tier > len(pending):
        return None
    running = 0.0
    index = 0
    for row in rows:
        raw = row.get("amount_usd")
        running = round2(running + float(raw or 0))
        while index < len(pending) and pending[index].amount is not None and is_tier_reached(running, pending[index].amount):
            index += 1
            if index == tier:
                return {
                    "donor": display_donor(row.get("donor") or row.get("author_name")) or "",
                    "platform": str(row.get("platform") or ""),
                    "amount_usd": None if raw is None else float(raw),
                }
    return None


def tier_donor(rows: list[dict], enabled: bool, tiers: list[GoalTier], tier: int) -> str:
    """Name of the donor whose gift first reached this tier. Tier 0 has none."""
    found = tier_breach(rows, enabled, tiers, tier)
    return "" if found is None else found["donor"]


def tier_name(enabled: bool, tiers: list[GoalTier], tier: int) -> str:
    """The name set on this tier. Tier 0, a missing tier, and a blank name are empty.

    Tier 1 is the lowest enabled amount, not the first row on the form.
    """
    if tier <= 0 or not enabled:
        return ""
    pending = amount_tiers(tiers)
    if tier > len(pending):
        return ""
    return one_line(pending[tier - 1].name)


def tier_label(tier: GoalTier) -> str:
    named = one_line(tier.name)
    if named:
        return named
    if tier.amount is not None:
        return format_usd(tier.amount)
    return "the goal"


def tier_thank_you_line(tier_name: str, username: str) -> str:
    tier = one_line(tier_name) or "the goal"
    who = one_line(username) or "Someone"
    return f"Thank you for helping us reach {tier}, {who}!"


def stream_chat_line(total: float, *, enabled: bool, tiers: list[GoalTier], extra: str) -> str:
    body = f"{format_usd(round2(total))} total for stream."
    if enabled:
        active = named_tiers(tiers)
        if active:
            nxt = next((tier for tier in active if total < (tier.amount or 0) - REACHED_EPS), None)
            if nxt is not None and nxt.amount is not None:
                remaining = round2(nxt.amount - total)
                bonus = f" and {one_line(nxt.bonus)}" if nxt.bonus.strip() else ""
                body += f" {format_usd(remaining)} from {nxt.name.strip()}{bonus}"
            else:
                last = active[-1]
                over = round2(total - (last.amount or 0))
                bonus = f" and {one_line(last.bonus)}" if last.bonus.strip() else ""
                body += f" {format_usd(over)} over {last.name.strip()}{bonus}"
    note = one_line(extra)
    if note:
        body += f" {note}"
    return body


def donor_leaderboard(rows: list[dict], limit: int = 10) -> dict:
    grouped: dict[str, dict] = {}
    for row in rows:
        name = display_donor(row.get("author_name") or row.get("donor")) or "—"
        key = name.casefold()
        slot = grouped.get(key)
        if slot is None:
            slot = {"donor": name, "usd": 0.0, "count": 0, "platforms": []}
            grouped[key] = slot
        slot["usd"] += float(row.get("amount_usd") or 0)
        slot["count"] += 1
        platform = str(row.get("platform_label") or row.get("platform") or "")
        if platform and platform not in slot["platforms"]:
            slot["platforms"].append(platform)
    ranked = sorted(grouped.values(), key=lambda item: (-item["usd"], -item["count"], item["donor"].casefold()))
    return {
        "total": len(ranked),
        "rows": [
            {
                "donor": item["donor"],
                "usd": round2(item["usd"]),
                "count": item["count"],
                "platforms": item["platforms"],
            }
            for item in ranked[: max(0, limit)]
        ],
    }


def donor_bar_line(rows: list[dict], *, mentions: bool = False) -> str:
    names = ", ".join(
        f"{_bar_name(row.get('donor'), mentions)}: {format_usd(float(row.get('usd') or 0))}"
        for row in rows[:3]
    )
    if names:
        return f"Top Donors: {names}. {DONOR_BAR_THANKS}"
    return f"Top Donors: {DONOR_BAR_THANKS}"


def _bar_name(value: object, mentions: bool) -> str:
    shown = display_donor(value) or "—"
    return mention_donor(shown) if mentions else shown


def tier_breaches(rows: list[dict], tiers_enabled: bool, tiers: list[GoalTier]) -> list[dict]:
    if not tiers_enabled:
        return []
    pending = amount_tiers(tiers)
    if not pending:
        return []
    found: list[dict] = []
    running = 0.0
    index = 0
    for row in rows:
        running = round2(running + float(row.get("amount_usd") or 0))
        crossed: list[GoalTier] = []
        while index < len(pending) and pending[index].amount is not None and is_tier_reached(running, pending[index].amount):
            crossed.append(pending[index])
            index += 1
        if not crossed:
            continue
        donor = display_donor(row.get("author_name")) or "Someone"
        for tier in crossed:
            found.append(
                {
                    "amount": tier.amount,
                    "name": tier_label(tier),
                    "id": row.get("id"),
                    "donor": donor,
                    "line": tier_thank_you_line(tier_label(tier), donor),
                    "copy": tier_thank_you_line(tier_label(tier), mention_donor(donor)),
                }
            )
    return found


def type_breakdown(rows: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for row in rows:
        label = str(row.get("kind_label") or "Super Chat")
        slot = grouped.get(label)
        if slot is None:
            slot = {"type": label, "count": 0, "usd": 0.0}
            grouped[label] = slot
        slot["count"] += 1
        slot["usd"] = round2(slot["usd"] + float(row.get("amount_usd") or 0))
    return list(grouped.values())


def running_usd(rows: list[dict]) -> float:
    total = 0.0
    for row in rows:
        total = round2(total + float(row.get("amount_usd") or 0))
    return total
