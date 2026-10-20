"""BLUE's test box. A dry run: nothing is sent, queued, logged, or saved.

The line is read and checked against a made-up event on a real ledger, using
that ledger's total, tier, and saved guard times. The guards are only looked at.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from types import SimpleNamespace

from .blue import BlueEvent, explain, parse_script
from .blue_host import CHAT_LINES_PER_MINUTE
from .blue_scripts import MAX_SOURCE
from .commands import LIMITS, trim_reply
from .fx import cached_usd_rates
from .goals import format_usd
from .kinds import KIND_LABELS, PLATFORM_LABELS

_EVENTS = ("donation", "timer", "goal")
_PLATFORMS = ("youtube", "rumble", "kick", "other")
_NOTICE_TEXT = {
    "app": "Would show this in the app.",
    "log": "Would write this to the log.",
    "system": "Would post this as a system notification.",
}
_NOTICE_LIMITS = {"log": 500, "system": 180}


def dry_run(host, payload: dict) -> dict:
    source = str(payload.get("source") or "")
    if not source.strip():
        raise ValueError("Type a BLUE line to test.")
    if len(source) > MAX_SOURCE:
        raise ValueError("That BLUE script is too long.")
    program = parse_script(source)
    event_name = str(payload.get("event") or "").strip().lower()
    if not event_name:
        event_name = program.commands[0].event if program.commands else "donation"
    if event_name not in _EVENTS:
        raise ValueError("The test event must be a donation, a timer minute, or a goal.")
    store = host.service.store
    tab = _ledger(store, str(payload.get("stream_id") or ""))
    rates = cached_usd_rates(store)
    notes: list[str] = []
    try:
        ledger_total: float | None = store.stream_total_usd(tab["id"], rates)
    except Exception:
        ledger_total = None
        notes.append("This ledger's total could not be read.")
    try:
        ledger_tier = host._reached(tab, ledger_total) if ledger_total is not None else 0
    except Exception:
        ledger_tier = 0
    total = _number(payload.get("total"), "TOTAL")
    tier = _tier(payload.get("tier"))
    use_total = ledger_total if total is None else total
    use_tier = ledger_tier if tier is None else tier
    now = datetime.now(timezone.utc)
    gift = _gift(payload, now)
    if event_name == "donation":
        event = host._donation_event(gift, tab, now, use_tier, use_total)
    elif event_name == "timer":
        minutes = _minutes(payload.get("minutes"))
        event = host._timer_event(host._minutes(tab, now) if minutes is None else minutes, now, use_tier, use_total, tab)
    else:
        goal_tier = use_tier if tier is not None else 1
        priced = host._quote_donation(gift, tab) if goal_tier > 0 else None
        event = host._goal_event(
            tab, goal_tier, now,
            donor=gift.author_name, platform=gift.platform,
            amount=None if priced is None else priced.usd, total=use_total,
        )
    event = _with_overrides(event, payload, event_name)
    report = explain(source, event)
    script, script_lines = _running_script(store, tab)
    for entry in report["lines"]:
        entry["text"] = _source_line(source, entry["line"])
        running_line = script_lines.get(entry["text"].strip()) if script else None
        for action in entry["actions"]:
            action.update(_delivery(host, tab, script, running_line, action, event, gift))
    if event_name == "donation":
        notes.append(
            "The test gift is from now, so the 2-minute age guard lets it through. "
            "A real gift over 2 minutes old, or from before the script started, is skipped."
        )
    if any(action.get("via_platform") for entry in report["lines"] for action in entry["actions"]):
        notes.append(
            "PLATFORM uses where a real gift came from. This test gift has none: a public chat reader's gift "
            "would answer as shown, a signed-in YouTube, Kick webhook, or Rumble owner-API gift also needs to be "
            "from this ledger's chat, and a manual or pasted entry is skipped."
        )
    if "{" in source and ("totalline" in source.casefold() or "topline" in source.casefold()):
        notes.append("{TOTALLINE} and {TOPLINE} always use this ledger's real rows, not the TOTAL typed here.")
    return {
        "dry_run": True,
        "event": event_name,
        "ledger": {
            "id": tab["id"],
            "title": (tab.get("stream_title") or "").strip() or "Stream",
            "total": None if use_total is None else format_usd(use_total),
            "tier": use_tier,
            "total_from": "typed" if total is not None else "ledger",
            "tier_from": "typed" if tier is not None else "ledger",
            "script": script["name"] if script else "",
            "connected": bool(str(tab.get("blue_started_at") or "")) and str(tab.get("collection_paused") or "") == "0",
        },
        "time": event.placeholders.get("time", ""),
        "minutes": event.numbers.get("minutes"),
        "problems": report["problems"],
        "lines": report["lines"],
        "notes": notes,
    }


def _ledger(store, wanted: str) -> dict:
    tabs = store.ledger_snapshots()
    wanted = wanted.strip() or store._active_stream_id()
    for tab in tabs:
        if tab["id"] == wanted:
            return tab
    raise ValueError("That ledger is not open any more.")


def _gift(payload: dict, now: datetime) -> SimpleNamespace:
    platform = str(payload.get("platform") or "youtube").strip().lower()
    if platform not in _PLATFORMS:
        raise ValueError("Platform must be YouTube, Rumble, Kick, or Other.")
    kind = str(payload.get("kind") or "").strip().lower() or {
        "youtube": "super_chat", "rumble": "rant", "kick": "kicks", "other": "donation",
    }[platform]
    if kind not in KIND_LABELS:
        raise ValueError("That donation type is not one the ledger knows.")
    amount = _number(payload.get("amount"), "AMOUNT")
    currency = str(payload.get("currency") or "").strip().upper()[:8] or ("KICKS" if kind == "kicks" else "USD")
    donor = " ".join(str(payload.get("donor") or "").split())[:80]
    return SimpleNamespace(
        dedupe_key="", platform=platform, kind=kind, external_id="", channel="",
        author_name=donor, author_id="", message="",
        amount_value=amount, amount_micros=None if amount is None else int(round(amount * 1_000_000)),
        currency=currency, created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), raw={},
    )


def _with_overrides(event: BlueEvent, payload: dict, name: str) -> BlueEvent:
    placeholders = dict(event.placeholders)
    numbers = dict(event.numbers)
    clock = event.clock
    raw_time = str(payload.get("time") or "").strip()
    if raw_time:
        try:
            parsed = datetime.strptime(raw_time, "%H:%M")
        except ValueError as exc:
            raise ValueError("Time must look like 14:30.") from exc
        clock = parsed.time()
        placeholders["time"] = clock.strftime("%H:%M")
    minutes = _minutes(payload.get("minutes"))
    if minutes is not None and name != "timer":
        numbers["minutes"] = minutes
        placeholders["minutes"] = str(minutes)
    return dataclasses.replace(event, clock=clock, numbers=numbers, placeholders=placeholders)


def _running_script(store, tab: dict) -> tuple[dict | None, dict[str, int]]:
    """The script this ledger runs, and its lines by text. Read only."""
    script_id = str(tab.get("blue_script_id") or "")
    if not script_id:
        return None, {}
    for script in store.blue_scripts():
        if script["id"] == script_id:
            lines = {}
            for number, line in enumerate(str(script.get("source") or "").replace("\r\n", "\n").split("\n"), start=1):
                lines.setdefault(line.strip(), number)
            return script, lines
    return None, {}


def _source_line(source: str, number: int) -> str:
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return lines[number - 1] if 0 < number <= len(lines) else ""


def _delivery(host, tab: dict, script: dict | None, running_line: int | None, action: dict, event, gift) -> dict:
    """What would happen to one action, without doing it."""
    text = " ".join(str(action.get("text") or "").split())
    kind = action["kind"]
    target = action["target"]
    checks: list[dict] = []
    if kind == "notice":
        limit = _NOTICE_LIMITS.get(target)
        final = text[:limit] if limit else text
        if not final:
            return {"would": "skip", "final": "", "why": "There was no message.", "checks": checks}
        return {"would": "notice", "final": final, "why": _NOTICE_TEXT.get(target, ""), "checks": checks}
    if action.get("via_platform") and not target:
        if str(event.values.get("platform") or "").strip():
            why = "PLATFORM does not answer Other-platform entries, since they have no chat."
        else:
            why = "PLATFORM has no gift to answer at tier 0."
        return {"would": "skip", "final": "", "why": why, "checks": checks}
    label = PLATFORM_LABELS.get(target, target)
    if action.get("via_platform"):
        commands = getattr(host.service, "commands", None)
        can_send = getattr(commands, "can_send", None)
        try:
            ready = bool(can_send(target)) if can_send is not None else False
        except Exception:
            ready = False
        checks.append({
            "name": "PLATFORM",
            "ok": ready,
            "message": f"{label} chat can be sent from here." if ready
            else f"{label} chat cannot be sent from here right now, so PLATFORM would skip it.",
        })
        if not ready:
            return {"would": "skip", "final": "", "why": checks[-1]["message"], "checks": checks}
    final = trim_reply(text, LIMITS.get(target, 200))
    if not final:
        return {"would": "skip", "final": "", "why": "There was no message.", "checks": checks}
    repeat = None
    if event.name == "donation":
        if script is not None and running_line is not None:
            stand_in = SimpleNamespace(line=running_line, target=target)
            repeat = host._repeat_key(tab, script, stand_in, event)
        else:
            checks.append({
                "name": "Repeat",
                "ok": True,
                "message": "Not checked: this line is not in the script this ledger runs, so it has no thank-you history.",
            })
    problem, sent = host.peek_chat_slot(tab, target, repeat)
    if repeat is not None:
        checks.append({
            "name": "Repeat",
            "ok": problem != "repeat",
            "message": "This gift was already thanked on that line and chat in the last 2 minutes."
            if problem == "repeat" else "Not thanked on that line and chat in the last 2 minutes.",
        })
    checks.append({
        "name": "Chat cap",
        "ok": sent < CHAT_LINES_PER_MINUTE,
        "message": f"{sent} of {CHAT_LINES_PER_MINUTE} BLUE chat lines went to {label} chat in the last minute.",
    })
    if problem == "repeat":
        why = "That gift was already thanked in the last 2 minutes."
        return {"would": "skip", "final": final, "why": why, "checks": checks}
    if problem == "full":
        why = f"BLUE already queued {CHAT_LINES_PER_MINUTE} chat lines for that chat in the last minute."
        return {"would": "skip", "final": final, "why": why, "checks": checks}
    return {"would": "chat", "final": final, "why": f"Would send to {label} chat.", "checks": checks}


def _number(value: object, label: str) -> float | None:
    text = str(value if value is not None else "").strip().replace(",", "").lstrip("$")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if number != number or abs(number) > 1e12:
        raise ValueError(f"{label} must be a number.")
    return number


def _tier(value: object) -> int | None:
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    if not text.isdigit() or int(text) > 5:
        raise ValueError("TIER must be a whole number from 0 through 5.")
    return int(text)


def _minutes(value: object) -> int | None:
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    if not text.isdigit() or len(text) > 7:
        raise ValueError("MINUTES must be a whole number.")
    return int(text)
