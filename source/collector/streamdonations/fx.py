"""US dollar rates from Exchange Rate API's open endpoint.

GET https://open.er-api.com/v6/latest/USD

Their open-access docs (https://www.exchangerate-api.com/docs/free) say the
table updates once a day, caching is allowed, and one request an hour stays
under the rate limit. A 429 means wait 20 minutes. The whole response is
stored; only that cached copy is used until the next allowed check.
Kick coins are not in this table. Those convert at 100 coins = $1.
"""

from __future__ import annotations

import json
import threading
import time

from .store import Store
from .transport import Transport

FX_URL = "https://open.er-api.com/v6/latest/USD"
FX_HOSTS = {"open.er-api.com"}
_TTL_SECONDS = 60 * 60
_BACKOFF_SECONDS = 20 * 60
_MANUAL_GAP_SECONDS = 15
_cache_lock = threading.Lock()
_memory: dict | None = None


def reset_rate_cache() -> None:
    global _memory
    with _cache_lock:
        _memory = None


def usd_rates(
    store: Store,
    transport: Transport | None = None,
    now: float | None = None,
    *,
    force: bool = False,
) -> dict[str, float]:
    rates, _outcome = _load(store, transport, now, force=force)
    return rates


def cached_usd_rates(store: Store) -> dict[str, float]:
    """Rates already saved. This does not ask the network."""
    with _cache_lock:
        state = _remember(store)
        rates = state.get("rates")
    if isinstance(rates, dict) and rates:
        return {str(code): float(value) for code, value in rates.items() if _rate_ok(value)}
    return {"USD": 1.0}


def _rate_ok(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) > 0


def refresh_rates(store: Store, transport: Transport | None = None, now: float | None = None) -> dict:
    """One manual check. Automatic callers should use usd_rates instead."""
    _rates, outcome = _load(store, transport, now, force=True)
    status = rate_status(store, now)
    status["outcome"] = outcome
    return status


def rate_status(store: Store, now: float | None = None) -> dict:
    moment = time.time() if now is None else now
    with _cache_lock:
        state = _remember(store)
    checked = state.get("checked_at") or 0.0
    payload = state.get("payload") if isinstance(state.get("payload"), dict) else {}
    rates = state.get("rates") if isinstance(state.get("rates"), dict) else {"USD": 1.0}
    backoff = float(state.get("backoff_until") or 0.0)
    return {
        "endpoint": FX_URL,
        "checked_at": checked or None,
        "provider_updated_at": _unix(payload.get("time_last_update_unix")),
        "next_update_at": _unix(payload.get("time_next_update_unix")),
        "currencies": len(rates),
        "backoff_until": backoff or None,
        "error": str(state.get("error") or ""),
        "stale": moment - float(state.get("attempt_at") or 0.0) >= _TTL_SECONDS,
    }


def _load(
    store: Store,
    transport: Transport | None,
    now: float | None,
    *,
    force: bool,
) -> tuple[dict[str, float], str]:
    global _memory
    moment = time.time() if now is None else now
    with _cache_lock:
        state = _remember(store)
        rates = dict(state.get("rates") or {"USD": 1.0})
        attempt_at = float(state.get("attempt_at") or 0.0)
        backoff_until = float(state.get("backoff_until") or 0.0)
        if backoff_until > moment:
            return rates, "limited"
        if not force and attempt_at and moment - attempt_at < _TTL_SECONDS:
            return rates, "cached"
        if force and attempt_at and moment - attempt_at < _MANUAL_GAP_SECONDS:
            return rates, "cached"
        state["attempt_at"] = moment
        state["error"] = ""
        _memory = state
        _write_state(store, state)
        try:
            body, payload, fresh = _fetch(transport or Transport())
        except RateLimited as exc:
            state["backoff_until"] = moment + _BACKOFF_SECONDS
            state["error"] = str(exc)
            _memory = state
            _write_state(store, state)
            return rates, "limited"
        except (OSError, ValueError, TypeError) as exc:
            state["error"] = str(exc)
            _memory = state
            _write_state(store, state)
            return rates, "failed"
        state["checked_at"] = moment
        state["backoff_until"] = 0.0
        state["error"] = ""
        state["response"] = body
        state["payload"] = payload
        state["rates"] = fresh
        _memory = state
        _write_state(store, state)
        return dict(fresh), "fetched"


def _remember(store: Store) -> dict:
    global _memory
    if _memory is not None:
        return _memory
    _memory = _read_state(store)
    return _memory


def _read_state(store: Store) -> dict:
    response = store.get_setting("fx_response_json")
    payload: dict = {}
    if response:
        try:
            parsed = json.loads(response)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
    elif store.get_setting("fx_rates_json"):
        # Older builds stored only the rates object.
        try:
            parsed = json.loads(store.get_setting("fx_rates_json"))
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict) and "rates" not in parsed and "USD" in parsed:
            payload = {"result": "success", "base_code": "USD", "rates": parsed}
        elif isinstance(parsed, dict):
            payload = parsed
    rates = _rates_of(payload)
    checked = _float_setting(store, "fx_checked_at") or _float_setting(store, "fx_rates_at")
    return {
        "attempt_at": _float_setting(store, "fx_attempt_at") or checked,
        "checked_at": checked,
        "backoff_until": _float_setting(store, "fx_backoff_until"),
        "error": store.get_setting("fx_last_error"),
        "response": response,
        "payload": payload,
        "rates": rates or {"USD": 1.0},
    }


def _write_state(store: Store, state: dict) -> None:
    store.set_setting("fx_attempt_at", str(state.get("attempt_at") or 0))
    store.set_setting("fx_checked_at", str(state.get("checked_at") or 0))
    store.set_setting("fx_backoff_until", str(state.get("backoff_until") or 0))
    store.set_setting("fx_last_error", str(state.get("error") or ""))
    response = state.get("response")
    if isinstance(response, str) and response:
        store.set_setting("fx_response_json", response)


def _fetch(transport: Transport) -> tuple[str, dict, dict[str, float]]:
    result = transport.request("GET", FX_URL, allow_hosts=FX_HOSTS, timeout=8)
    if result.status == 429:
        raise RateLimited("Exchange Rate API rate limit. Waiting 20 minutes before another check.")
    if result.status >= 400:
        raise ValueError(f"exchange rates returned HTTP {result.status}")
    try:
        body = result.body.decode("utf-8")
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("exchange rates were not JSON") from exc
    if not isinstance(payload, dict) or payload.get("result") != "success":
        raise ValueError("exchange rates were not a success response")
    if str(payload.get("base_code") or "").upper() != "USD":
        raise ValueError("exchange rates were not based on USD")
    rates = _rates_of(payload)
    if "USD" not in rates:
        raise ValueError("exchange rates did not include USD")
    return body, payload, rates


def _rates_of(payload: dict) -> dict[str, float]:
    raw = payload.get("rates") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, float] = {}
    for code, value in raw.items():
        name = str(code or "").upper()
        if len(name) < 3 or len(name) > 8:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        cleaned[name] = number
    if cleaned:
        cleaned["USD"] = 1.0
    return cleaned


def _float_setting(store: Store, key: str) -> float:
    raw = store.get_setting(key)
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _unix(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


class RateLimited(Exception):
    pass
