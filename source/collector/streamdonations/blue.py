"""BLUE, Berry Lexical User Events.

One line is one command. Keywords are not case-sensitive. The message inside
double quotes keeps the case the user typed. Punctuation outside those quotes
is ignored, and so is a word that is not a keyword, unless that word is the
value of a comparison. A line that cannot be read is reported and skipped.
The following lines still run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time as clock_time

from .util import display_donor

_KEYWORDS = frozenset({
    "on", "donation", "timer", "goal", "time", "minutes", "platform", "amount", "total", "donor", "type", "tier", "name",
    "if", "equal", "equals", "above", "below", "modulus", "modulo", "mod",
    "and", "or", "not", "then", "send", "chat", "to",
    "youtube", "rumble", "kick", "all", "notice", "app", "log", "system",
    "raw", "rawamount", "raw_amount", "raw-amount", "currency",
})
_FIELDS = frozenset({
    "platform", "amount", "total", "donor", "type", "time", "minutes", "tier", "name",
    "rawamount", "raw_amount", "raw-amount", "currency",
})
_FIELD_ALIASES = {"raw_amount": "rawamount", "raw-amount": "rawamount"}
_NUMBER_FIELDS = frozenset({"amount", "minutes", "tier", "total", "rawamount"})
# Ends a comparison. "IS" followed by a value and one of these means EQUALS.
_COMPARISON_END = frozenset({"then", "and", "or"})
_STRUCTURAL = frozenset({
    "on", "if", "and", "or", "not", "then", "send", "chat", "notice", "to",
    "equal", "equals", "above", "below", "modulus", "modulo", "mod",
})
# Whole numbers only, so MODULO leaves 0 through n - 1.
_WHOLE_FIELDS = frozenset({"minutes", "tier", "time"})
_MODULO_FIELDS = _NUMBER_FIELDS | {"time"}
# "%" is MODULO right after a field when a number follows. Anywhere else it is filler.
_PERCENT = "%"
# "-" is the sign of the number after it, even with a space between them.
_MINUS = "-"
_MARKS = frozenset({_PERCENT, _MINUS})
_CHAT_ORDER = ("youtube", "rumble", "kick")
_NOTICE_TARGETS = frozenset({"app", "log", "system"})
_TIME = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?")
_NUMBER = re.compile(r"\$?\d+(?:\.\d+)?")
_PLACEHOLDER = re.compile(r"\{([A-Za-z][A-Za-z_]*)\}")
# Underscores do not matter: {RAW_AMOUNT} is {RAWAMOUNT}.
_PLACEHOLDER_ALIASES = {"totalblurb": "totalline", "topblurb": "topline"}
# The source platform of the event. Only a chat target. Not on a timer.
PLATFORM_TARGET = "platform"
_CURRENCY_ALIASES = {"kick": "kicks", "kicks": "kicks"}
_TYPE_ALIASES = {
    "super chat": "super chat",
    "superchat": "super chat",
    "chat": "super chat",
    "super sticker": "super sticker",
    "supersticker": "super sticker",
    "sticker": "super sticker",
    "membership": "membership",
    "member": "membership",
    "subscription": "membership",
    "sub": "membership",
    "donation": "donation",
}
_PLATFORM_ALIASES = {
    "youtube": "youtube",
    "rumble": "rumble",
    "kick": "kick",
    "other": "other",
}


class BlueSyntax(Exception):
    """One line could not be read. The other lines are still used."""


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str


@dataclass(frozen=True)
class BlueProblem:
    line: int
    message: str


@dataclass(frozen=True)
class _Comparison:
    negate: bool
    field_name: str
    modulo: float | None
    op: str
    value: str


@dataclass(frozen=True)
class _Command:
    line: int
    event: str
    groups: tuple
    actions: tuple
    payload: str

    @property
    def uses_time(self) -> bool:
        """A TIME comparison. On ON TIMER such a line runs once per clock minute."""
        return any(item.field_name == "time" for group in self.groups for item in group)


@dataclass
class BlueProgram:
    commands: list[_Command] = field(default_factory=list)
    problems: list[BlueProblem] = field(default_factory=list)


@dataclass(frozen=True)
class BlueEvent:
    """One donation, one timer minute, or one goal tier."""

    name: str
    values: dict
    numbers: dict
    clock: clock_time | None
    placeholders: dict
    # Placeholders that depend on the target, such as {TOTALLINE}: name ->
    # fill(target, other_characters, placeholder_count). Filled after the rest.
    lookup: object = None


@dataclass(frozen=True)
class BlueAction:
    line: int
    kind: str
    target: str
    text: str
    # The target came only from PLATFORM, the platform of the gift.
    via_platform: bool = False


@dataclass
class BlueRun:
    actions: list[BlueAction]
    problems: list[BlueProblem]


def inspect_script(source: object) -> list[dict]:
    """Lines that will be skipped when the script runs. Saving still keeps them."""
    program = parse_script(source)
    return [{"line": problem.line, "message": problem.message} for problem in program.problems]


def parse_script(source: object) -> BlueProgram:
    program = BlueProgram()
    text = str(source or "").replace("\r\n", "\n").replace("\r", "\n")
    for number, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        try:
            command = _parse_line(line, number)
        except BlueSyntax as exc:
            program.problems.append(BlueProblem(number, str(exc)))
            continue
        if command is not None:
            program.commands.append(command)
    return program


def evaluate(source: object, event: BlueEvent, timer: str = "") -> BlueRun:
    """Actions whose event and conditions match. A bad line adds nothing.

    timer picks ON TIMER lines: "elapsed" runs the lines without TIME (once per
    minute since the start), "clock" runs the lines with TIME (once per clock
    minute). Empty runs every line.
    """
    program = parse_script(source)
    actions: list[BlueAction] = []
    if event.name not in {"donation", "timer", "goal"}:
        return BlueRun(actions, program.problems)
    for command in program.commands:
        if command.event != event.name:
            continue
        if event.name == "timer" and timer == "elapsed" and command.uses_time:
            continue
        if event.name == "timer" and timer == "clock" and not command.uses_time:
            continue
        if not _matches(command.groups, event):
            continue
        for kind, target, via_platform in _resolve(command.actions, event):
            actions.append(BlueAction(command.line, kind, target, _fill(command.payload, event, target), via_platform))
    return BlueRun(actions, program.problems)


def _resolve(actions: tuple, event: BlueEvent) -> list[tuple[str, str, bool]]:
    """Concrete targets for one line. PLATFORM becomes the gift's platform.

    A platform named on the line and also reached through PLATFORM is sent once,
    as the named target. PLATFORM with no chat platform (other, a manual row's
    "other", or a goal at tier 0) resolves to an empty target.
    """
    source = _platform_key(event.values.get("platform", ""))
    chosen: list[tuple[str, str, bool]] = []
    seen: dict[tuple[str, str], int] = {}
    for kind, target, excluded in actions:
        via = target == PLATFORM_TARGET
        if via:
            if source in excluded:
                continue
            resolved = source if source in _CHAT_ORDER else ""
        else:
            if PLATFORM_TARGET in excluded and target == source:
                continue
            resolved = target
        key = (kind, resolved)
        if resolved and key in seen:
            index = seen[key]
            if not via and chosen[index][2]:
                chosen[index] = (kind, resolved, False)
            continue
        if resolved:
            seen[key] = len(chosen)
        chosen.append((kind, resolved, via))
    return chosen


def _parse_line(line: str, number: int) -> _Command | None:
    tokens = _tokenize(line)
    if not tokens:
        return None
    if not any(token.kind == "keyword" for token in tokens):
        if any(token.kind == "payload" for token in tokens):
            raise BlueSyntax("A BLUE line starts with ON.")
        return None
    return _Parser(tokens).command(number)


def _tokenize(line: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    size = len(line)
    while index < size:
        char = line[index]
        if char.isspace():
            index += 1
            continue
        if char in {'"', "'"}:
            text, index = _quoted(line, index, char)
            tokens.append(_Token("payload" if char == '"' else "value", text))
            continue
        timed = _TIME.match(line, index)
        if timed:
            tokens.append(_Token("value", timed.group(0)))
            index = timed.end()
            continue
        number = _NUMBER.match(line, index)
        if number:
            tokens.append(_Token("value", number.group(0)))
            index = number.end()
            continue
        if char in {_PERCENT, _MINUS}:
            # Filler, except "%" as MODULO and "-" as the sign of a number.
            tokens.append(_Token("word", char))
            index += 1
            continue
        if char.isalpha():
            end = index + 1
            while end < size and (line[end].isalpha() or line[end] in "-_"):
                end += 1
            word = line[index:end]
            kind = "keyword" if word.casefold() in _KEYWORDS else "word"
            tokens.append(_Token(kind, word))
            index = end
            continue
        index += 1
    return tokens


def _quoted(line: str, start: int, mark: str) -> tuple[str, int]:
    chars: list[str] = []
    index = start + 1
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            chars.append(line[index + 1])
            index += 2
            continue
        if char == mark:
            return "".join(chars), index + 1
        chars.append(char)
        index += 1
    raise BlueSyntax("A quote was left open.")


class _Parser:
    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.index = 0

    def command(self, number: int) -> _Command:
        for index, token in enumerate(self.tokens):
            if token.kind == "keyword" and token.text.casefold() == "raw":
                after = self.tokens[index + 1] if index + 1 < len(self.tokens) else None
                if after is None or after.kind != "keyword" or after.text.casefold() != "amount":
                    raise BlueSyntax("RAW only goes before AMOUNT.")
        while self._has() and not self._is("on"):
            if self._peek().kind == "payload":
                raise BlueSyntax("A BLUE line starts with ON.")
            self._advance()
        if not self._is("on"):
            raise BlueSyntax("A BLUE line starts with ON.")
        self._advance()
        while self._has() and self._peek().kind == "word":
            self._advance()
        if self._is("donation"):
            event = "donation"
        elif self._is("timer"):
            event = "timer"
        elif self._is("goal"):
            event = "goal"
        else:
            raise BlueSyntax("BLUE does not know that event.")
        self._advance()
        self._event = event
        groups = self._conditions()
        actions = self._actions()
        return _Command(number, event, tuple(groups), tuple(actions), self._payload())

    def _conditions(self) -> list[list[_Comparison]]:
        groups: list[list[_Comparison]] = [[]]
        while self._has() and not self._is("then"):
            self._skip_words()
            if not self._has() or self._is("then"):
                break
            if self._is("if") or self._is("and") or self._is("on"):
                self._advance()
                continue
            if self._is("or"):
                self._advance()
                groups.append([])
                continue
            groups[-1].append(self._comparison())
        if not self._is("then"):
            raise BlueSyntax("That line needs THEN.")
        self._advance()
        return [group for group in groups if group]

    def _comparison(self) -> _Comparison:
        negate = False
        self._skip_words()
        while self._is("not"):
            negate = not negate
            self._advance()
            self._skip_words()
        field_name = self._field()
        modulo = None
        saw_is = False
        while True:
            saw_is = self._skip_filler(saw_is)
            if self._is("not"):
                negate = not negate
                self._advance()
                continue
            if modulo is None and self._at_modulo():
                self._advance()
                modulo = self._modulo()
                continue
            break
        if self._is("equal") or self._is("equals"):
            op = "equal"
        elif self._is("above"):
            op = "above"
        elif self._is("below"):
            op = "below"
        elif saw_is and self._implicit_value():
            # "CURRENCY IS JPY": IS with a value and nothing after it means EQUALS.
            item = _Comparison(negate, field_name, modulo, "equal", self._value())
            _check_comparison(item)
            return item
        else:
            raise BlueSyntax("That comparison needs equal, above, or below.")
        self._advance()
        item = _Comparison(negate, field_name, modulo, op, self._value())
        _check_comparison(item)
        return item

    def _at_modulo(self) -> bool:
        if self._is("modulo") or self._is("modulus") or self._is("mod"):
            return True
        return self._percent_modulo()

    def _percent_modulo(self) -> bool:
        """A "%" here is MODULO when a number follows it. Any other "%" is filler."""
        token = self._peek()
        if token is None or token.kind != "word" or token.text != _PERCENT:
            return False
        index = self.index + 1
        while index < len(self.tokens) and self.tokens[index].kind == "word" and self.tokens[index].text == _MINUS:
            index += 1
        return index < len(self.tokens) and self.tokens[index].kind == "value"

    def _signed(self) -> bool:
        """Skip "%" and "-" before a value. True when a "-" was among them."""
        negative = False
        while self._has() and self._peek().kind == "word" and self._peek().text in _MARKS:
            if self._advance().text == _MINUS:
                negative = True
        return negative

    def _field(self) -> str:
        if self._is("raw"):
            self._advance()
            if not self._is("amount"):
                raise BlueSyntax("RAW only goes before AMOUNT.")
            self._advance()
            return "rawamount"
        if not self._is_field():
            raise BlueSyntax("That comparison needs a field.")
        name = self._advance().text.casefold()
        return _FIELD_ALIASES.get(name, name)

    def _skip_filler(self, saw_is: bool) -> bool:
        """Skip filler words. Stops at the value of an "IS value" comparison."""
        while self._has() and self._peek().kind == "word":
            if self._percent_modulo():
                return saw_is
            if saw_is and self._implicit_value():
                return saw_is
            if self._peek().text.casefold() == "is":
                saw_is = True
            self._advance()
        return saw_is

    def _implicit_value(self) -> bool:
        # A "-" sign (or a stray "%") before the value belongs to it.
        index = self.index
        while index < len(self.tokens) and self.tokens[index].kind == "word" and self.tokens[index].text in _MARKS:
            index += 1
        token = self.tokens[index] if index < len(self.tokens) else None
        if token is None or token.kind == "payload":
            return False
        text = token.text.casefold()
        if text in _STRUCTURAL or text in _FIELDS or text == "raw":
            return False
        after = self.tokens[index + 1] if index + 1 < len(self.tokens) else None
        return after is None or (after.kind == "keyword" and after.text.casefold() in _COMPARISON_END)

    def _actions(self) -> list[tuple[str, str]]:
        actions: list[tuple[str, str]] = []
        payloads: list[str] = []
        while self._has():
            if self._peek().kind == "payload":
                payloads.append(self._advance().text)
                continue
            if self._is("send") or self._is("and") or self._is("or") or self._is("to"):
                self._advance()
                continue
            if self._is("chat"):
                self._advance()
                actions.extend(("chat",) + item for item in self._targets(True))
                continue
            if self._is("notice"):
                self._advance()
                actions.extend(("notice",) + item for item in self._targets(False))
                continue
            if self._peek().kind == "word":
                self._advance()
                continue
            raise BlueSyntax("That action is not a chat or a notice.")
        if not actions:
            raise BlueSyntax("That line has no action.")
        self._payloads = payloads
        return actions

    def _targets(self, chat: bool) -> list[tuple[str, frozenset]]:
        """(target, excluded) pairs. Only PLATFORM needs the excluded names later."""
        found: list[str] = []
        excluded: set[str] = set()
        excluding = False
        saw = False
        while self._has():
            if self._peek().kind == "payload":
                break
            if self._is("not"):
                excluding = True
                self._advance()
                continue
            if self._is("and") or self._is("or") or self._is("to"):
                self._advance()
                continue
            if self._is("chat") or self._is("notice") or self._is("send"):
                break
            name = self._target_name(chat)
            if name is None:
                if self._peek() is not None and self._peek().kind == "word":
                    self._advance()
                    continue
                break
            self._advance()
            saw = True
            if name == PLATFORM_TARGET and getattr(self, "_event", "") == "timer":
                raise BlueSyntax("A timer has no PLATFORM to send to.")
            chosen = list(_CHAT_ORDER) if name == "all" else [name]
            if excluding:
                excluded.update(chosen)
            else:
                for item in chosen:
                    if item not in found:
                        found.append(item)
            excluding = False
        if not saw:
            raise BlueSyntax("That action needs a target.")
        if PLATFORM_TARGET in found and PLATFORM_TARGET in excluded:
            found.remove(PLATFORM_TARGET)
        kept = [item for item in found if item not in excluded]
        if PLATFORM_TARGET in kept:
            # PLATFORM resolves later. A named exclusion still applies to it.
            return [(item, frozenset(excluded) if item == PLATFORM_TARGET else frozenset()) for item in kept]
        marker = frozenset({PLATFORM_TARGET}) if PLATFORM_TARGET in excluded else frozenset()
        return [(item, marker) for item in kept]

    def _target_name(self, chat: bool) -> str | None:
        token = self._peek()
        if token is None or token.kind == "payload":
            return None
        text = token.text.casefold()
        if chat and text == "all":
            return "all"
        if chat and text == PLATFORM_TARGET and token.kind == "keyword":
            return PLATFORM_TARGET
        allowed = set(_CHAT_ORDER) if chat else _NOTICE_TARGETS
        if text in allowed:
            return text
        return None

    def _payload(self) -> str:
        return " ".join(getattr(self, "_payloads", []))

    def _skip_words(self, stop_at_percent: bool = False) -> None:
        """A word that is not a keyword is filler, unless it is a comparison value."""
        while self._has() and self._peek().kind == "word":
            if stop_at_percent and self._percent_modulo():
                return
            self._advance()

    def _modulo(self) -> float:
        negative = False
        while self._has() and self._peek().kind == "word":
            if self._advance().text == _MINUS:
                negative = True
        if not self._has() or self._peek().kind not in {"value", "word"}:
            raise BlueSyntax("The modulus needs a number.")
        number = _parse_number(self._advance().text)
        if number is not None and negative:
            number = -number
        if number is None:
            raise BlueSyntax("The modulus needs a number.")
        if number == 0:
            raise BlueSyntax("The modulus is zero.")
        if number < 0:
            raise BlueSyntax("The modulus must be above zero.")
        return number

    def _value(self) -> str:
        negative = self._signed()
        if not self._has():
            raise BlueSyntax("That comparison needs a value.")
        token = self._peek()
        if token.kind == "payload" or token.text.casefold() in _STRUCTURAL:
            raise BlueSyntax("That comparison needs a value.")
        text = self._advance().text
        if negative and token.kind == "value" and _NUMBER.fullmatch(text):
            return _MINUS + text
        return text

    def _is_field(self) -> bool:
        return self._has() and self._peek().text.casefold() in _FIELDS

    def _is(self, name: str) -> bool:
        return self._has() and self._peek().kind == "keyword" and self._peek().text.casefold() == name

    def _has(self) -> bool:
        return self.index < len(self.tokens)

    def _peek(self) -> _Token | None:
        if not self._has():
            return None
        return self.tokens[self.index]

    def _advance(self) -> _Token:
        token = self.tokens[self.index]
        self.index += 1
        return token


def _matches(groups: tuple, event: BlueEvent) -> bool:
    if not groups:
        return True
    return any(all(_comparison_holds(item, event) for item in group) for group in groups)


def _comparison_holds(item: _Comparison, event: BlueEvent) -> bool:
    result = _compare(item, event)
    return (not result) if item.negate else result


def _compare(item: _Comparison, event: BlueEvent) -> bool:
    if item.field_name == "time" and item.modulo is not None:
        # Minutes since local midnight on this event's clock.
        if event.clock is None:
            return False
        minutes = event.clock.hour * 60 + event.clock.minute
        return _number_cmp(minutes % item.modulo, item.value, item.op)
    if item.field_name == "time":
        left = event.clock
        right = _parse_clock(item.value)
        if left is None or right is None:
            return False
        return _clock_cmp(left, right, item.op, item.value)
    if item.modulo is not None or item.field_name in _NUMBER_FIELDS:
        number = event.numbers.get(item.field_name)
        if item.modulo is not None:
            if number is None:
                return False
            number = float(number) % item.modulo
        return _number_cmp(number, item.value, item.op)
    left = str(event.values.get(item.field_name, ""))
    right = item.value
    if item.field_name == "donor":
        left = display_donor(left).casefold()
        right = display_donor(right).casefold()
    elif item.field_name == "platform":
        left = _platform_key(left)
        right = _platform_key(right)
    elif item.field_name == "type":
        left = _type_key(left)
        right = _type_key(right)
    elif item.field_name == "currency":
        if not left.strip():
            return False
        left = _currency_key(left)
        right = _currency_key(right)
    else:
        left = left.casefold()
        right = right.casefold()
    if item.op != "equal":
        return False
    return left == right


_LABELS = {
    "time": "TIME", "amount": "AMOUNT", "rawamount": "RAW AMOUNT", "minutes": "MINUTES", "tier": "TIER",
    "total": "TOTAL", "currency": "CURRENCY",
}


def _check_comparison(item: _Comparison) -> None:
    """Refuse a comparison that can never be read or never match."""
    label = _LABELS.get(item.field_name, item.field_name.upper())
    if item.modulo is not None and item.field_name not in _MODULO_FIELDS:
        raise BlueSyntax("MODULO works on AMOUNT, RAW AMOUNT, TOTAL, MINUTES, TIER, and TIME.")
    if item.field_name == "time" and item.modulo is None:
        if _parse_clock(item.value) is None:
            raise BlueSyntax(
                "TIME needs a clock time such as 14:30 or 9:00 PM. "
                "TIME MODULO takes minutes since midnight."
            )
        return
    if item.field_name not in _MODULO_FIELDS:
        return
    if item.field_name == "time" and ":" in item.value:
        raise BlueSyntax("TIME MODULO counts minutes since midnight, so it needs a number, not a clock time.")
    value = _parse_number(item.value)
    if value is None:
        raise BlueSyntax(f"{label} needs a number.")
    if item.modulo is None:
        return
    n = item.modulo
    whole = item.field_name in _WHOLE_FIELDS and float(n).is_integer()
    largest = n - 1 if whole else n
    never = (
        (item.op == "equal" and (value < 0 or value >= n or (whole and not float(value).is_integer())))
        or (item.op == "above" and value >= largest)
        or (item.op == "below" and value <= 0)
    )
    if never:
        span = f"0 through {_plain(n - 1)}" if whole else f"from 0 up to {_plain(n)}"
        raise BlueSyntax(f"That can never match. {label} MODULO {_plain(n)} is {span}.")


def _plain(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else str(number)


def _number_cmp(number: object, raw: str, op: str) -> bool:
    if number is None:
        return False
    other = _parse_number(raw)
    if other is None:
        return False
    left = float(number)
    if op == "equal":
        return abs(left - other) < 0.001
    if op == "above":
        return left > other
    if op == "below":
        return left < other
    return False


def _clock_cmp(left: clock_time, right: clock_time, op: str, raw: str) -> bool:
    if raw.count(":") >= 2:
        left_key = left.hour * 3600 + left.minute * 60 + left.second
        right_key = right.hour * 3600 + right.minute * 60 + right.second
    else:
        left_key = left.hour * 60 + left.minute
        right_key = right.hour * 60 + right.minute
    if op == "equal":
        return left_key == right_key
    if op == "above":
        return left_key > right_key
    if op == "below":
        return left_key < right_key
    return False


def placeholder_key(name: str) -> str:
    key = name.casefold().replace("_", "")
    return _PLACEHOLDER_ALIASES.get(key, key)


def _fill(payload: str, event: BlueEvent, target: str = "") -> str:
    """Fill placeholders. Target placeholders are filled last, knowing the rest."""
    lookup = event.lookup if isinstance(event.lookup, dict) else {}
    deferred: list[str] = []

    def replace(match: re.Match) -> str:
        name = placeholder_key(match.group(1))
        if name in event.placeholders:
            return str(event.placeholders[name]).replace("\x00", "")
        if name in lookup:
            deferred.append(name)
            return f"\x00{len(deferred) - 1}\x00"
        return match.group(0)

    text = _PLACEHOLDER.sub(replace, payload.replace("\x00", ""))
    if not deferred:
        return text
    others = len(" ".join(_DEFERRED.sub("", text).split()))
    values = [str(lookup[name](target, others, len(deferred)) or "") for name in deferred]
    return _DEFERRED.sub(lambda match: values[int(match.group(1))], text)


_DEFERRED = re.compile("\x00(\\d+)\x00")


def _parse_number(value: object) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if text.startswith("$"):
        text = text[1:].strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_clock(value: object) -> clock_time | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*([AaPp][Mm]))?", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    second = int(match.group(3) or 0)
    meridian = match.group(4)
    if minute > 59 or second > 59:
        return None
    if meridian:
        if hour < 1 or hour > 12:
            return None
        hour = hour % 12
        if meridian.casefold() == "pm":
            hour += 12
    elif hour > 23:
        return None
    return clock_time(hour, minute, second)


def _platform_key(value: object) -> str:
    text = " ".join(str(value or "").split()).casefold()
    return _PLATFORM_ALIASES.get(text, text)


def _currency_key(value: object) -> str:
    text = " ".join(str(value or "").split()).casefold()
    return _CURRENCY_ALIASES.get(text, text)


def _type_key(value: object) -> str:
    text = " ".join(str(value or "").split()).casefold()
    return _TYPE_ALIASES.get(text, text)
