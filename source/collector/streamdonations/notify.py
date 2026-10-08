"""System notifications for newly stored donations.

Linux talks to the FreeDesktop notification service. KDE, GNOME, and the
other desktops that implement org.freedesktop.Notifications all receive the
same call. Windows and macOS do not have a backend yet; system_notifier()
is the place those belong.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence

from .goals import format_usd, one_line
from .util import display_donor
from .kinds import PLATFORM_LABELS
from .money import quote
from .normalize import Donation

APP_NAME = "SuperBerry"
_ICON = "stream-donations"
_DESKTOP_ENTRY = "stream-donations"
_QUEUE_LIMIT = 200
Log = Callable[[str], None]


class NullNotifier:
    def donations_added(self, donations: Sequence[Donation], *, membership_price: float | None) -> None:
        return


class UnavailableNotifier(NullNotifier):
    """Keeps the call site stable until a platform backend exists."""

    def __init__(self, platform: str, log: Log | None = None) -> None:
        self._platform = platform
        self._log = log
        self._said = False

    def donations_added(self, donations: Sequence[Donation], *, membership_price: float | None) -> None:
        if self._said or not donations:
            return
        self._said = True
        message = f"System notifications are not implemented on {self._platform} yet."
        if self._log is None:
            return
        try:
            self._log(message)
        except Exception:
            return


class FreedesktopNotifier:
    """Notify through whichever FreeDesktop client this machine has.

    notify-send (libnotify), dbus-send, and busctl all reach the same
    session service. The first one that works is reused.
    """

    def __init__(
        self,
        log: Log | None = None,
        *,
        run: Callable[..., subprocess.CompletedProcess] | None = None,
        which: Callable[[str], str | None] | None = None,
        synchronous: bool = False,
    ) -> None:
        self._log = log
        self._run = run or subprocess.run
        self._which = which or shutil.which
        self._synchronous = synchronous
        self._preferred: tuple[str, str] | None = None
        self._warned = False
        self._warn_lock = threading.Lock()
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=_QUEUE_LIMIT)
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()

    def donations_added(self, donations: Sequence[Donation], *, membership_price: float | None) -> None:
        notes = [notification_text(donation, membership_price) for donation in donations]
        notes = [(summary, body) for summary, body in notes if summary]
        if not notes:
            return
        if self._synchronous:
            for summary, body in notes:
                self._send(summary, body)
            return
        self._ensure_worker()
        for note in notes:
            try:
                self._queue.put_nowait(note)
            except queue.Full:
                self._note_failure("System notifications are backed up.")
                return

    def _ensure_worker(self) -> None:
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name="stream-donations-notify", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while True:
            summary, body = self._queue.get()
            try:
                self._send(summary, body)
            except Exception:
                self._note_failure("System notifications could not be sent.")
            finally:
                self._queue.task_done()

    def _send(self, summary: str, body: str) -> None:
        tools = self._candidates()
        if self._preferred is not None:
            tools = [self._preferred] + [tool for tool in tools if tool != self._preferred]
        if not tools:
            self._note_failure("System notifications need notify-send, dbus-send, or busctl.")
            return
        for tool in tools:
            if self._invoke(tool, summary, body):
                self._preferred = tool
                return
        self._preferred = None
        self._note_failure("System notifications could not be sent.")

    def _candidates(self) -> list[tuple[str, str]]:
        found = []
        for name in ("notify-send", "dbus-send", "busctl"):
            path = self._which(name)
            if path:
                found.append((name, path))
        return found

    def _invoke(self, tool: tuple[str, str], summary: str, body: str) -> bool:
        kind, path = tool
        try:
            completed = self._run(
                list(_argv(kind, path, summary, body)),
                timeout=3,
                check=False,
                capture_output=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return int(getattr(completed, "returncode", 1)) == 0

    def _note_failure(self, message: str) -> None:
        with self._warn_lock:
            if self._warned:
                return
            self._warned = True
        if self._log is None:
            return
        try:
            self._log(message)
        except Exception:
            return


def system_notifier(log: Log | None = None):
    """The backend for this operating system.

    Linux uses FreeDesktop notifications. A Windows toast backend and a
    macOS UserNotifications backend should replace the unavailable notifier
    here, leaving donations_added() unchanged.
    """
    if sys.platform.startswith("linux"):
        return FreedesktopNotifier(log=log)
    return UnavailableNotifier(sys.platform, log=log)


def notification_text(donation: Donation, membership_price: float | None) -> tuple[str, str]:
    """Summary and body for one stored donation. No raw platform payload."""
    who = _plain(display_donor(donation.author_name), 80) or "Someone"
    platform = PLATFORM_LABELS.get(donation.platform, donation.platform or "a platform")
    priced = quote(_quote_row(donation), membership_price, {"USD": 1.0})
    amount = _amount_text(priced.amount, priced.currency)
    summary = f"{who} sent {amount}" if amount else who
    body = f"{priced.label} on {platform}"
    message = _plain(donation.message, 140)
    if message and message.casefold() != str(priced.label or "").casefold():
        body = f"{body}\n{message}"
    return _plain(summary, 180), _plain(body, 240)


def _quote_row(donation: Donation) -> dict:
    raw = donation.raw if isinstance(donation.raw, dict) else {}
    return {
        "kind": donation.kind,
        "amount_value": donation.amount_value,
        "currency": donation.currency,
        "platform": donation.platform,
        "message": donation.message,
        "raw": raw,
    }


def _amount_text(amount: object, currency: object) -> str:
    if not isinstance(amount, (int, float)):
        return ""
    code = str(currency or "").upper()
    if code == "USD":
        return format_usd(float(amount))
    if code:
        return f"{float(amount):.2f} {code}"
    return ""


def _plain(value: object, limit: int) -> str:
    lines = []
    for line in str(value or "").splitlines():
        text = "".join(ch for ch in one_line(line) if ch.isprintable())
        if text:
            lines.append(text)
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _argv(kind: str, path: str, summary: str, body: str) -> list[str]:
    if kind == "notify-send":
        command = [
            path,
            f"--app-name={APP_NAME}",
            f"--icon={_ICON}",
            "--urgency=normal",
            f"--hint=string:desktop-entry:{_DESKTOP_ENTRY}",
            summary,
        ]
        if body:
            command.append(body)
        return command
    if kind == "busctl":
        return [
            path,
            "--user",
            "call",
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
            "Notify",
            "susssasa{sv}i",
            APP_NAME,
            "0",
            _ICON,
            summary,
            body,
            "0",
            "1",
            "desktop-entry",
            "s",
            _DESKTOP_ENTRY,
            "-1",
        ]
    if kind == "dbus-send":
        return [
            path,
            "--session",
            "--dest=org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications.Notify",
            _dbus_string(APP_NAME),
            "uint32:0",
            _dbus_string(_ICON),
            _dbus_string(summary),
            _dbus_string(body),
            "array:string:",
            f"dict:string:variant:desktop-entry,string:{_DESKTOP_ENTRY}",
            "int32:-1",
        ]
    raise ValueError(f"unknown notification tool {kind}")


def _dbus_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("\r", " ").replace("\n", " ")
    return "string:" + escaped
