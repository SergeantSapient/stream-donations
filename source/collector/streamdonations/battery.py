"""Host battery for the kiosk header.

A mouse or keyboard that reports its own cell is not the computer.
The line is shown only while a system battery is discharging and no
mains or USB supply is online.
"""

from __future__ import annotations

from pathlib import Path

_SUPPLY = Path("/sys/class/power_supply")


def host_battery(root: Path | None = None) -> dict[str, int | bool] | None:
    """Return the charge while this computer is running on its battery.

    ``root`` is the power-supply directory. The live path is used when it
    is omitted. A device-scoped supply, such as a Logitech mouse, is
    ignored. Wall power, a battery that is not discharging, or a charge
    that cannot be read returns None.
    """
    base = _SUPPLY if root is None else root
    try:
        if not base.is_dir():
            return None
        entries = sorted(base.iterdir(), key=lambda path: path.name)
    except OSError:
        return None
    batteries: list[Path] = []
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        if _text(entry / "scope").casefold() == "device":
            continue
        kind = _text(entry / "type").casefold()
        if kind in {"mains", "usb"}:
            if _text(entry / "online") == "1":
                return None
            continue
        if kind != "battery":
            continue
        batteries.append(entry)
    if not batteries:
        return None
    for entry in batteries:
        if _text(entry / "status").casefold() != "discharging":
            return None
    percent = _combined_percent(batteries)
    if percent is None:
        return None
    return {"on_battery": True, "percent": percent}


def _combined_percent(entries: list[Path]) -> int | None:
    percents: list[int] = []
    for entry in entries:
        percent = _percent(entry)
        if percent is None:
            return None
        percents.append(percent)
    if len(entries) == 1:
        return percents[0]
    nows: list[float] = []
    fulls: list[float] = []
    for entry in entries:
        now = _number(entry / "energy_now")
        full = _number(entry / "energy_full")
        if now is None or full is None or full <= 0:
            return _clamp(sum(percents) / len(percents))
        nows.append(now)
        fulls.append(full)
    total_full = sum(fulls)
    if total_full <= 0:
        return _clamp(sum(percents) / len(percents))
    return _clamp(sum(nows) / total_full * 100)


def _percent(entry: Path) -> int | None:
    capacity = _number(entry / "capacity")
    if capacity is not None:
        return _clamp(capacity)
    energy = _ratio(entry, "energy_now", "energy_full")
    if energy is not None:
        return _clamp(energy)
    charge = _ratio(entry, "charge_now", "charge_full")
    if charge is not None:
        return _clamp(charge)
    return None


def _ratio(entry: Path, now_name: str, full_name: str) -> float | None:
    now = _number(entry / now_name)
    full = _number(entry / full_name)
    if now is None or full is None or full <= 0:
        return None
    return now / full * 100


def _clamp(value: float) -> int:
    if value < 0:
        value = 0
    if value > 100:
        value = 100
    return int(round(value))


def _number(path: Path) -> float | None:
    text = _text(path)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
