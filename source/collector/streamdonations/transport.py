"""HTTP calls limited to the hosts each collector names."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .util import ApiError, host_allowed

DEFAULT_UA = "StreamDonations/1.0"
# Rumble's livestream endpoint rejects requests that arrive without a browser-like
# User-Agent. The app name stays on the string so the request is identifiable.
RUMBLE_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 StreamDonations/1.0"
)


@dataclass
class HttpResult:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str

    def json(self) -> object:
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(f"response from {self.url} was not JSON") from exc


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_hosts: set[str]):
        super().__init__()
        self.allow_hosts = allow_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlparse(newurl).hostname or ""
        if not host_allowed(host, self.allow_hosts):
            raise urllib.error.HTTPError(req.full_url, code, "redirect host refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Transport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: object | None = None,
        form: dict[str, str] | None = None,
        timeout: float = 20,
        allow_hosts: set[str],
        user_agent: str = DEFAULT_UA,
    ) -> HttpResult:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            raise ValueError(f"refusing non-https request to {parsed.hostname}")
        if not host_allowed(parsed.hostname or "", allow_hosts):
            raise ValueError(f"refusing request to {parsed.hostname}")
        payload: bytes | None = None
        request_headers = {"User-Agent": user_agent, "Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        if json_body is not None and form is not None:
            raise ValueError("pass json_body or form, not both")
        if json_body is not None:
            payload = json.dumps(json_body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        elif form is not None:
            payload = urllib.parse.urlencode(form).encode("utf-8")
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        opener = urllib.request.build_opener(_GuardedRedirect(allow_hosts))
        request = urllib.request.Request(url, data=payload, headers=request_headers, method=method.upper())
        try:
            with opener.open(request, timeout=timeout) as response:
                body = response.read()
                status = int(response.status)
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                final_url = response.geturl()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            status = int(exc.code)
            response_headers = {key.lower(): value for key, value in exc.headers.items()} if exc.headers else {}
            final_url = exc.geturl() or url
        final_host = urllib.parse.urlparse(final_url).hostname or ""
        if not host_allowed(final_host, allow_hosts):
            raise ValueError(f"refusing response from {final_host}")
        return HttpResult(status=status, headers=response_headers, body=body, url=final_url)


def api_error_message(result: HttpResult) -> str:
    payload: object
    try:
        payload = result.json()
    except ApiError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("status") or str(error)
            return f"HTTP {result.status}: {message}"
        if isinstance(error, str):
            description = payload.get("error_description")
            text = f"{error}: {description}" if description else error
            return f"HTTP {result.status}: {text}"
        if payload.get("message"):
            return f"HTTP {result.status}: {payload.get('message')}"
    text = result.body.decode("utf-8", errors="replace")[:300]
    return f"HTTP {result.status}: {text}"
