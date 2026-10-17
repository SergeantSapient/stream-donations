"""Small helpers shared by the collectors."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone, tzinfo
from urllib.parse import quote

_SECRET_RE = re.compile(
    r"(key|client_secret|code|access_token|refresh_token|code_verifier)=([^&\s]+)",
    re.IGNORECASE,
)


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class AuthError(ApiError):
    pass


def redact(text: str, limit: int = 800) -> str:
    cleaned = _SECRET_RE.sub(r"\1=REDACTED", text or "")
    cleaned = cleaned.replace("\n", " ").strip()
    if len(cleaned) > limit:
        return cleaned[: limit - 3] + "..."
    return cleaned


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now_iso() -> str:
    return format_utc(utc_now())


def parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_utc_iso(value: object) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return ""
    return format_utc(parsed)


def stable_key(prefix: str, parts: list[object]) -> str:
    raw = "\x1f".join("" if part is None else str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return prefix + digest


def as_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_micros(value: object) -> tuple[int | None, float | None]:
    micros = as_int(value)
    if micros is None:
        return None, None
    return micros, round(micros / 1_000_000, 6)


def amount_from_cents(cents: object, dollars: object) -> tuple[int | None, float | None]:
    """Return (micros of major unit, major-unit amount)."""
    whole_cents = as_int(cents)
    if whole_cents is not None:
        return whole_cents * 10_000, round(whole_cents / 100, 2)
    if dollars is None or dollars == "":
        return None, None
    try:
        major = round(float(dollars), 2)
    except (TypeError, ValueError):
        return None, None
    return int(round(major * 1_000_000)), major


def channel_id_value(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("value") or "")
    if value is None:
        return ""
    return str(value)


def display_donor(value: object) -> str:
    """Donor name as the app shows it. A leading @ is added only when copying into chat."""
    text = " ".join(str(value or "").split())
    while text.startswith("@"):
        text = text[1:].lstrip()
    return text


def mention_donor(value: object) -> str:
    """@name for a copied top-donor or thank-you line. Placeholders stay bare."""
    name = display_donor(value)
    if not name or name in {"—", "Someone"}:
        return name
    return f"@{name}"


def ledger_title(value: object) -> str:
    """One line for a ledger tab. Blank stays blank. The tab stops at 80 characters."""
    return " ".join(str(value or "").split())[:80]


def collapsed_chat_text(value: object, limit: int = 2000) -> str:
    """One line for a chat post. Blank text is refused. The limit is characters, not bytes."""
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError("Type a message.")
    if len(text) > limit:
        raise ValueError("That message is too long.")
    return text


# Windows refuses <>:"/\|?* and control characters. Linux refuses / and NUL.
# macOS refuses / and treats : as a path separator. A name cannot end with a
# space or a period, and CON, PRN, AUX, NUL, COM1–COM9, and LPT1–LPT9 are reserved.
_FILENAME_INVALID = re.compile(r"[\x00-\x1f\x7f<>:\"/\\|?*]")
_WINDOWS_DEVICE = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE)


def download_filename(
    title: str,
    first_at: str,
    extension: str,
    zone: tzinfo | None = None,
) -> str:
    """MMDDYYYY-Ledger name, safe on Windows, Linux, and macOS.

    The date is the oldest exported donation in local time. No donation leaves
    the date off. A blank ledger name is Stream.
    """
    if extension not in {"csv", "jsonl"}:
        raise ValueError("Export file must be csv or jsonl.")
    label = _FILENAME_INVALID.sub("_", str(title or "")).strip(" ._")
    if not label or set(label) <= {"_"}:
        label = "Stream"
    date = _mmddyyyy(first_at, zone)
    stem = f"{date}-{label}" if date else label
    if _WINDOWS_DEVICE.fullmatch(stem):
        stem += "_"
    suffix = "." + extension
    while len(stem + suffix) > 255 or len((stem + suffix).encode("utf-8")) > 255:
        stem = stem[:-1].rstrip(" .")
        if not stem:
            stem = "Stream"
            break
    return stem + suffix


def content_disposition(filename: str) -> str:
    """Attachment header. filename* keeps a ledger name that is not ASCII."""
    ascii_name = "".join(ch if 32 <= ord(ch) < 127 else "_" for ch in filename).strip(" .") or "download"
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def _mmddyyyy(iso: str, zone: tzinfo | None) -> str:
    parsed = parse_datetime(iso)
    if parsed is None:
        return ""
    if zone is None:
        zone = datetime.now().astimezone().tzinfo
    return parsed.astimezone(zone).strftime("%m%d%Y")


def csv_safe(value: object) -> str:
    """Stop spreadsheet apps from treating chat text as a formula."""
    if value is None:
        return ""
    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def host_allowed(host: str, allow_hosts: set[str]) -> bool:
    lowered = (host or "").lower().rstrip(".")
    if not lowered:
        return False
    for allowed in allow_hosts:
        name = allowed.lower().rstrip(".")
        if lowered == name or lowered.endswith("." + name):
            return True
    return False
