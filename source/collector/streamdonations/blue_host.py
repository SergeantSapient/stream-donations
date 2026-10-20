"""Run a ledger's BLUE script while that ledger is connected.

A soft disconnect does not run it. Connecting the ledger starts the timer
over. Chat and app notices wait for the window. Anything that cannot be done
is written to the log, and the next action still runs.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from .blue import BlueEvent, evaluate
from .fx import cached_usd_rates
from .goals import format_usd, load_tiers, reached_tier, round2, tier_breach, tier_name
from .kinds import PLATFORM_LABELS
from .money import parse_membership_price, quote
from .util import display_donor, format_utc, parse_datetime, utc_now

_MAX_PENDING = 40
_GOAL_TIERS = frozenset({"0", "1", "2", "3", "4", "5"})
_TARGET_LABELS = {
    "youtube": "YouTube chat",
    "rumble": "Rumble chat",
    "kick": "Kick chat",
    "app": "app notice",
    "log": "log notice",
    "system": "system notice",
}


class BlueHost:
    def __init__(self, service):
        self.service = service
        self._lock = threading.Lock()
        self._busy = threading.Lock()
        self._pending: list[dict] = []
        self._seen: set[tuple] = set()
        self._missing: set[tuple] = set()

    def on_donations(self, stream_id: str, donations) -> None:
        """Fresh ledger rows only. A duplicate insert does not call this."""
        try:
            with self._busy:
                tab = self._tab(stream_id)
                if tab is None or str(tab.get("collection_paused") or "") != "0":
                    return
                script = self._script(tab)
                if script is None:
                    return
                now = datetime.now(timezone.utc)
                try:
                    steps, facts, stored, dirty = self._goal_plan(tab, donations)
                except Exception:
                    self._log("BLUE could not read the goal tier.")
                    steps = [[] for _ in donations]
                    facts = [(0, None) for _ in donations]
                    stored, dirty = 0, False
                if dirty:
                    self.service.store.set_stream_values(tab["id"], {"blue_goal_tier": str(stored)})
                for donation, tiers, (tier_now, total) in zip(donations, steps, facts):
                    try:
                        self._dispatch(tab, script, self._donation_event(donation, tab, now, tier_now, total))
                        priced = self._quote_donation(donation, tab)
                        for tier in tiers:
                            self._dispatch(tab, script, self._goal_event(
                                tab,
                                tier,
                                now,
                                donor=display_donor(getattr(donation, "author_name", "")),
                                platform=str(getattr(donation, "platform", "") or ""),
                                amount=priced.usd,
                                total=total,
                            ))
                    except Exception:
                        self._log("BLUE could not read a new donation.")
        except Exception:
            self._log("BLUE could not read a new donation.")

    def tick(self, now: datetime | None = None) -> None:
        """One timer check. A missed minute is not replayed."""
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        else:
            moment = moment.astimezone(timezone.utc)
        try:
            tabs = self.service.store.ledger_snapshots()
        except Exception:
            self._log("BLUE could not check the timer.")
            return
        for tab in tabs:
            try:
                self._timer(tab, moment)
            except Exception:
                self._log("BLUE could not check the timer.")

    def on_connection(self, stream_id: str, paused: bool) -> None:
        """Connecting starts the script over. Disconnecting stops it."""
        if paused:
            self._stop(stream_id)
            return
        self._start(stream_id)

    def note_script(self, stream_id: str, previous: str) -> None:
        """Attaching a different script while connected starts that script over."""
        tab = self._tab(stream_id)
        if tab is None or str(tab.get("collection_paused") or "") != "0":
            return
        current = str(tab.get("blue_script_id") or "")
        if current == str(previous or ""):
            return
        if not current:
            self._stop(stream_id)
            return
        self._start(stream_id)

    def forget_script(self, script_id: str, name: str) -> None:
        """A deleted script stops on every ledger that was running it."""
        for tab in self.service.store.ledger_snapshots():
            if str(tab.get("blue_started_at") or "") and not str(tab.get("blue_script_id") or ""):
                self._stop(tab["id"], name)

    def claim(self) -> list[dict]:
        """Hand the window every waiting chat or app notice. They are not run twice."""
        with self._lock:
            items = list(self._pending)
            self._pending.clear()
        return [
            {
                "id": item["id"],
                "script": item["script"],
                "line": item["line"],
                "kind": item["kind"],
                "target": item["target"],
                "text": item["text"],
                "stream_id": item["stream_id"],
            }
            for item in items
        ]

    def record(self, payload: object) -> None:
        """The window reports a step it could not finish. The message stays out of the log."""
        if not isinstance(payload, dict) or payload.get("ok") is True:
            return
        script = _clip(payload.get("script"), 80) or "script"
        target = str(payload.get("target") or "").strip().lower()
        reason = _clip(payload.get("reason"), 300) or "That action could not be completed."
        try:
            line = int(payload.get("line"))
        except (TypeError, ValueError):
            line = 0
        self._log(f"BLUE {script} line {line}: {_TARGET_LABELS.get(target, 'action')} was not {_verb(target)}. {reason}")

    def _timer(self, tab: dict, now: datetime) -> None:
        if str(tab.get("collection_paused") or "") != "0":
            return
        script = self._script(tab)
        if script is None:
            return
        with self._busy:
            fresh = self._tab(tab["id"]) or tab
            started = parse_datetime(fresh.get("blue_started_at"))
            if started is None:
                self._start_body(fresh["id"])
                return
            if str(fresh.get("blue_goal_tier") or "") not in _GOAL_TIERS:
                self._baseline_body(fresh)
            elapsed = int((now - started).total_seconds() // 60)
            raw_minute = str(fresh.get("blue_timer_minute") or "")
            last = int(raw_minute) if raw_minute.isdigit() else 0
            new_elapsed = elapsed > last and elapsed > 0
            new_clock = self._new_clock_minute(fresh, now)
            if not new_elapsed and not new_clock:
                return
            tier, total = self._ledger_facts(fresh)
            if new_elapsed:
                self.service.store.set_stream_values(fresh["id"], {"blue_timer_minute": str(elapsed)})
                self._dispatch(fresh, script, self._timer_event(elapsed, now, tier, total), "elapsed")
            if new_clock:
                self._dispatch(fresh, script, self._timer_event(max(0, elapsed), now, tier, total), "clock")

    def _new_clock_minute(self, tab: dict, now: datetime) -> bool:
        """True once for each clock minute after the script started. TIME lines use it.

        The minute-since-start tick can sit on either side of a clock minute, so
        TIME lines are checked when the clock minute changes instead. The last
        clock minute is stored on the ledger, like the elapsed minute, so a
        restart of SuperBerry does not run that minute again. The minute the
        script started in does not run.
        """
        minute = int(now.timestamp() // 60)
        raw = str(tab.get("blue_clock_minute") or "")
        if not raw.isdigit():
            # A script started before this was stored: begin with this minute.
            self.service.store.set_stream_values(tab["id"], {"blue_clock_minute": str(minute)})
            return False
        if minute <= int(raw):
            return False
        self.service.store.set_stream_values(tab["id"], {"blue_clock_minute": str(minute)})
        return True

    def _start(self, stream_id: str) -> None:
        with self._busy:
            self._start_body(stream_id)

    def _start_body(self, stream_id: str) -> None:
        tab = self._tab(stream_id)
        script = self._script(tab) if tab is not None else None
        tier = 0
        donor = ""
        platform = ""
        amount = None
        total = None
        if tab is not None:
            tier, total = self._ledger_facts(tab)
            if tier > 0:
                try:
                    breach = self._breach_for_tier(tab, tier)
                    if breach is not None:
                        donor = breach["donor"]
                        platform = breach["platform"]
                        amount = breach["amount_usd"]
                except Exception:
                    self._log("BLUE could not read the goal tier.")
                    tier = 0
                    donor = ""
                    platform = ""
                    amount = None
        started = utc_now()
        self.service.store.set_stream_values(stream_id, {
            "blue_started_at": format_utc(started),
            "blue_timer_minute": "0",
            "blue_clock_minute": str(int(started.timestamp() // 60)),
            "blue_goal_tier": str(tier),
        })
        if script is None or tab is None:
            return
        self._report(stream_id, script)
        self._log(f"BLUE script {script['name']} started.")
        self._dispatch(tab, script, self._goal_event(
            tab,
            tier,
            datetime.now(timezone.utc),
            donor=donor,
            platform=platform,
            amount=amount,
            minutes=0,
            total=total,
        ))

    def _stop(self, stream_id: str, name: str = "") -> None:
        with self._busy:
            self._stop_body(stream_id, name)

    def _stop_body(self, stream_id: str, name: str = "") -> None:
        tab = self._tab(stream_id)
        running = bool(tab and str(tab.get("blue_started_at") or ""))
        script = self._script(tab) if tab is not None else None
        label = name or (script["name"] if script else "")
        self.service.store.set_stream_values(stream_id, {
            "blue_started_at": "",
            "blue_timer_minute": "",
            "blue_clock_minute": "",
            "blue_goal_tier": "",
        })
        self._drop(stream_id, "The ledger was disconnected.")
        if running and label:
            self._log(f"BLUE script {label} stopped.")

    def _baseline_body(self, tab: dict) -> None:
        """A script already running before GOAL existed keeps its current tier quiet."""
        try:
            tier = self._current_tier(tab)
        except Exception:
            self._log("BLUE could not read the goal tier.")
            return
        self.service.store.set_stream_values(tab["id"], {"blue_goal_tier": str(tier)})

    def _goal_plan(self, tab: dict, donations) -> tuple[list[list[int]], list[tuple[int, float | None]], int, bool]:
        """Tiers each new donation newly reaches, the tier to store, and whether it changed.

        The second list is the reached tier and the ledger total after that donation.
        A batch is walked in order, so an earlier gift does not see a later one.
        An empty stored tier on a script that is already running is the tier before
        this batch. Those earlier tiers are not fired again.
        """
        rates = cached_usd_rates(self.service.store)
        fresh = {donation.dedupe_key for donation in donations}
        usd_of: dict[str, float] = {}
        running = 0.0
        for key, usd, _donor, _platform in self.service.store.stream_goal_amounts(tab["id"], rates):
            counted = float(usd or 0)
            if key in fresh:
                usd_of[key] = counted
            else:
                running = round2(running + counted)
        raw = str(tab.get("blue_goal_tier") or "")
        if raw in _GOAL_TIERS:
            stored = int(raw)
            dirty = False
        elif str(tab.get("blue_started_at") or ""):
            stored = self._reached(tab, running)
            dirty = True
        else:
            stored = 0
            dirty = False
        steps: list[list[int]] = []
        facts: list[tuple[int, float | None]] = []
        for donation in donations:
            running = round2(running + usd_of.get(donation.dedupe_key, 0.0))
            reached = self._reached(tab, running)
            facts.append((reached, running))
            if reached > stored:
                steps.append(list(range(stored + 1, reached + 1)))
                stored = reached
                dirty = True
            else:
                steps.append([])
        return steps, facts, stored, dirty

    def _ledger_facts(self, tab: dict) -> tuple[int, float | None]:
        """Reached tier and this ledger's USD total. A failed read leaves tier 0."""
        try:
            rates = cached_usd_rates(self.service.store)
            total = self.service.store.stream_total_usd(tab["id"], rates)
        except Exception:
            self._log("BLUE could not read the ledger total.")
            return 0, None
        try:
            return self._reached(tab, total), total
        except Exception:
            self._log("BLUE could not read the goal tier.")
            return 0, total

    def _current_tier(self, tab: dict) -> int:
        rates = cached_usd_rates(self.service.store)
        return self._reached(tab, self.service.store.stream_total_usd(tab["id"], rates))

    def _reached(self, tab: dict, total: float) -> int:
        return reached_tier(
            total,
            str(tab.get("tiers_enabled") or "") == "1",
            load_tiers(tab.get("goal_tiers")),
        )

    def _breach_for_tier(self, tab: dict, tier: int) -> dict | None:
        """The gift that first reached this tier. Tier 0 has none."""
        if tier <= 0:
            return None
        rates = cached_usd_rates(self.service.store)
        rows = [
            {"amount_usd": usd, "donor": donor, "platform": platform}
            for _key, usd, donor, platform in self.service.store.stream_goal_amounts(tab["id"], rates)
        ]
        return tier_breach(
            rows,
            str(tab.get("tiers_enabled") or "") == "1",
            load_tiers(tab.get("goal_tiers")),
            tier,
        )

    def _tier_name(self, tab: dict, tier: int) -> str:
        """The name saved on this tier. Tier 0 and a blank name are empty."""
        return tier_name(
            str(tab.get("tiers_enabled") or "") == "1",
            load_tiers(tab.get("goal_tiers")),
            tier,
        )

    def _goal_event(
        self,
        tab: dict,
        tier: int,
        now: datetime,
        donor: str = "",
        platform: str = "",
        amount: float | None = None,
        minutes: int | None = None,
        total: float | None = None,
    ) -> BlueEvent:
        local = now.astimezone()
        clock = local.timetz().replace(tzinfo=None, microsecond=0)
        elapsed = self._minutes(tab, now) if minutes is None else minutes
        who = "" if tier <= 0 else display_donor(donor)
        label = self._tier_name(tab, tier)
        raw_platform = "" if tier <= 0 else str(platform or "")
        shown_amount = "" if tier <= 0 or amount is None else format_usd(amount)
        values = {"donor": who, "name": label, "platform": raw_platform}
        numbers = {"tier": tier, "minutes": elapsed}
        if tier > 0 and amount is not None:
            values["amount"] = shown_amount
            numbers["amount"] = amount
        placeholders = {
            "tier": str(tier),
            "time": clock.strftime("%H:%M"),
            "minutes": str(elapsed),
            "donor": who,
            "name": label,
            "amount": shown_amount,
            "platform": "" if tier <= 0 else PLATFORM_LABELS.get(raw_platform, raw_platform),
            "type": "",
        }
        self._remember_total(numbers, placeholders, total)
        return BlueEvent("goal", values, numbers, clock, placeholders)

    def _quote_donation(self, donation, tab: dict):
        rates = cached_usd_rates(self.service.store)
        price = parse_membership_price(tab.get("membership_price"))
        raw = donation.raw if isinstance(getattr(donation, "raw", None), dict) else {}
        return quote(
            {
                "kind": donation.kind,
                "amount_value": donation.amount_value,
                "currency": donation.currency,
                "platform": donation.platform,
                "message": donation.message,
                "raw": raw,
            },
            price,
            rates,
        )

    def _dispatch(self, tab: dict, script: dict, event: BlueEvent, timer: str = "") -> None:
        self._report(tab["id"], script)
        run = evaluate(script["source"], event, timer)
        for action in run.actions:
            try:
                self._perform(tab, script, action)
            except Exception:
                self._fail(script, action.line, action.target, "That action could not be completed.")

    def _perform(self, tab: dict, script: dict, action) -> None:
        text = " ".join(str(action.text or "").split())
        if action.kind == "notice" and action.target == "log":
            if not text:
                self._fail(script, action.line, "log", "There was no message.")
                return
            self._log(f"BLUE {script['name']}: {text[:500]}")
            return
        if action.kind == "notice" and action.target == "system":
            self._system(script, action.line, text)
            return
        if action.kind == "chat" and len(text) > 2000:
            self._fail(script, action.line, action.target, "That message is too long.")
            return
        if not text:
            self._fail(script, action.line, action.target, "There was no message.")
            return
        self._enqueue({
            "id": uuid.uuid4().hex,
            "script": script["name"],
            "line": action.line,
            "kind": action.kind,
            "target": action.target,
            "text": text,
            "stream_id": tab["id"],
        })

    def _system(self, script: dict, line: int, text: str) -> None:
        if not text:
            self._fail(script, line, "system", "There was no message.")
            return
        notifier = getattr(self.service.store, "_notifier", None)
        post = getattr(notifier, "post", None)
        if post is None or post(text[:180], "") is False:
            self._fail(script, line, "system", "System notifications are not available.")

    def _enqueue(self, item: dict) -> None:
        with self._lock:
            if len(self._pending) >= _MAX_PENDING:
                dropped = self._pending.pop(0)
            else:
                dropped = None
            self._pending.append(item)
        if dropped is not None:
            self._fail_item(dropped, "The window did not take it.")

    def _drop(self, stream_id: str, reason: str) -> None:
        with self._lock:
            kept = []
            dropped = []
            for item in self._pending:
                if item["stream_id"] == stream_id:
                    dropped.append(item)
                else:
                    kept.append(item)
            self._pending = kept
        for item in dropped:
            self._fail_item(item, reason)

    def _report(self, stream_id: str, script: dict) -> None:
        token = (stream_id, script["id"], script["source"])
        if token in self._seen:
            return
        self._seen.add(token)
        run = evaluate(script["source"], BlueEvent("donation", {}, {}, None, {}))
        for problem in run.problems:
            self._log(f"BLUE {script['name']} line {problem.line}: {problem.message}")

    def _script(self, tab: dict | None) -> dict | None:
        if tab is None:
            return None
        script_id = str(tab.get("blue_script_id") or "")
        if not script_id:
            return None
        for script in self.service.store.blue_scripts():
            if script["id"] == script_id:
                return script
        token = (tab["id"], script_id)
        if token not in self._missing:
            self._missing.add(token)
            self._log("BLUE script is no longer saved.")
        return None

    def _remember_total(self, numbers: dict, placeholders: dict, total: float | None) -> None:
        if total is None:
            return
        numbers["total"] = total
        placeholders["total"] = format_usd(total)

    def _donation_event(self, donation, tab: dict, now: datetime, tier: int, total: float | None) -> BlueEvent:
        priced = self._quote_donation(donation, tab)
        donor = display_donor(donation.author_name)
        platform = str(donation.platform or "")
        amount = format_usd(priced.usd) if priced.usd is not None else ""
        local = now.astimezone()
        clock = local.timetz().replace(tzinfo=None, microsecond=0)
        minutes = self._minutes(tab, now)
        numbers = {"amount": priced.usd, "minutes": minutes} if priced.usd is not None else {"minutes": minutes}
        numbers["tier"] = tier
        placeholders = {
            "donor": donor,
            "amount": amount,
            "platform": PLATFORM_LABELS.get(platform, platform),
            "type": priced.label,
            "time": clock.strftime("%H:%M"),
            "minutes": str(minutes),
            "tier": str(tier),
        }
        self._remember_total(numbers, placeholders, total)
        return BlueEvent(
            "donation",
            {"platform": platform, "donor": donor, "type": priced.label, "amount": amount},
            numbers,
            clock,
            placeholders,
        )

    def _timer_event(self, minutes: int, now: datetime, tier: int, total: float | None) -> BlueEvent:
        local = now.astimezone()
        clock = local.timetz().replace(tzinfo=None, microsecond=0)
        numbers = {"minutes": minutes, "tier": tier}
        placeholders = {
            "time": clock.strftime("%H:%M"),
            "minutes": str(minutes),
            "donor": "",
            "amount": "",
            "platform": "",
            "type": "",
            "tier": str(tier),
        }
        self._remember_total(numbers, placeholders, total)
        return BlueEvent("timer", {}, numbers, clock, placeholders)

    def _minutes(self, tab: dict, now: datetime) -> int:
        started = parse_datetime(tab.get("blue_started_at"))
        if started is None:
            return 0
        elapsed = (now - started).total_seconds()
        if elapsed < 0:
            return 0
        return int(elapsed // 60)

    def _tab(self, stream_id: str) -> dict | None:
        for tab in self.service.store.ledger_snapshots():
            if tab["id"] == stream_id:
                return tab
        return None

    def _fail(self, script: dict, line: int, target: str, reason: str) -> None:
        label = _TARGET_LABELS.get(target, "action")
        self._log(f"BLUE {script['name']} line {line}: {label} was not {_verb(target)}. {reason}")

    def _fail_item(self, item: dict, reason: str) -> None:
        target = item.get("target") or ""
        self._log(
            f"BLUE {item.get('script') or 'script'} line {item.get('line') or 0}: "
            f"{_TARGET_LABELS.get(target, 'action')} was not {_verb(target)}. {reason}"
        )

    def _log(self, message: str) -> None:
        try:
            self.service.store.log("blue", "info", message)
        except Exception:
            return


def _verb(target: str) -> str:
    if target in {"app", "system"}:
        return "shown"
    if target == "log":
        return "written"
    return "sent"


def _clip(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
