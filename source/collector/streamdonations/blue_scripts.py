"""Saved BLUE scripts. They are shared, like streamer presets, and are not a ledger."""

from __future__ import annotations

import json
import uuid

SCRIPTS_KEY = "blue_scripts"
MAX_SCRIPTS = 40
MAX_NAME = 80
MAX_SOURCE = 16000
MAX_LINES = 200
_ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"


def parse_scripts(raw: str) -> list[dict]:
    """Stored scripts. A broken document reads as none so the page still opens."""
    if not raw or not str(raw).strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    scripts: list[dict] = []
    seen: set[str] = set()
    for item in parsed:
        script = _stored_script(item)
        if script is None or script["id"] in seen:
            continue
        seen.add(script["id"])
        scripts.append(script)
        if len(scripts) >= MAX_SCRIPTS:
            break
    return scripts


def dump_scripts(scripts: list[dict]) -> str:
    return json.dumps(scripts, ensure_ascii=False, separators=(",", ":"))


def public_scripts(scripts: list[dict]) -> list[dict]:
    return sorted(scripts, key=lambda script: script["name"].casefold())


def upsert_script(scripts: list[dict], name: object, source: object) -> tuple[list[dict], dict]:
    """Add a script, or replace the source when that name is already saved."""
    fields = _script_fields(name, source)
    key = fields["name"].casefold()
    for script in scripts:
        if script["name"].casefold() == key:
            script.update(fields)
            return scripts, dict(script)
    if len(scripts) >= MAX_SCRIPTS:
        raise ValueError("You can save up to 40 BLUE scripts.")
    saved = {"id": uuid.uuid4().hex, **fields}
    scripts.append(saved)
    return scripts, dict(saved)


def remove_script(scripts: list[dict], script_id: object) -> tuple[list[dict], dict]:
    target = str(script_id or "").strip()
    match = next((script for script in scripts if script["id"] == target), None)
    if match is None:
        raise ValueError("That BLUE script is not saved.")
    return [script for script in scripts if script["id"] != target], dict(match)


def _script_fields(name: object, source: object) -> dict:
    cleaned = " ".join(str(name or "").split())
    if not cleaned:
        raise ValueError("Name the BLUE script.")
    if len(cleaned) > MAX_NAME:
        raise ValueError("BLUE script name is limited to 80 characters.")
    text = str(source or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise ValueError("Write a BLUE script first.")
    if len(text) > MAX_SOURCE or len(text.split("\n")) > MAX_LINES:
        raise ValueError("That BLUE script is too long.")
    return {"name": cleaned, "source": text}


def _stored_script(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    script_id = str(item.get("id") or "").strip()
    name = " ".join(str(item.get("name") or "").split())
    source = item.get("source")
    if (
        not script_id
        or not name
        or not isinstance(source, str)
        or not source.strip()
        or len(script_id) > 64
        or len(name) > MAX_NAME
        or len(source) > MAX_SOURCE
        or any(char not in _ID_CHARS for char in script_id)
    ):
        return None
    return {"id": script_id, "name": name, "source": source.replace("\r\n", "\n").replace("\r", "\n")}
