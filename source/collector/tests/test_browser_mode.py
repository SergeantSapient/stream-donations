"""Browser mode: the same-origin check, Quit, notices, favicon, and `serve --open`.

The page in Electron loads http://127.0.0.1:PORT/ from this server and calls
the API with fetch on relative paths (main.js openWindow, app.js api()), so it
sends Origin http://127.0.0.1:PORT and Content-Type application/json with a
body. Those requests must keep working; a page on another port must not.
"""

import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from streamdonations import __main__ as cli
from streamdonations.normalize import Donation
from streamdonations.notify import NoticeFeed
from streamdonations.server import App, Handler, ThreadingServer
from streamdonations.store import Store

ROOT = Path(__file__).resolve().parents[2]
PRESET = {"action": "save", "name": "probe", "youtube_channel": "https://www.youtube.com/@probe"}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.token = "b" * 32
        self.app = App(self.store, self.token, 1)
        self.server = ThreadingServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.app.ui_port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.app.ui_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.stop_webhook()
        self.store.close()
        self.tmp.cleanup()

    def send(self, method, path, body=None, headers=None, cookie=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.app.ui_port, timeout=5)
        sent = dict(headers or {})
        if cookie:
            sent["Cookie"] = f"sd_session={self.token}"
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode("utf-8")
        connection.request(method, path, body=data, headers=sent)
        response = connection.getresponse()
        payload = response.read()
        headers_out = dict(response.getheaders())
        connection.close()
        return response.status, headers_out, payload

    def presets(self):
        return [item["name"] for item in self.store.streamer_presets()]


class SameOriginTests(_Base):
    def test_electron_and_browser_page_requests_still_work(self):
        # Exactly what Chromium sends for app.js api() with a body, in Electron or a browser.
        status, _h, body = self.send("POST", "/api/presets", PRESET, {
            "Origin": self.origin,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(self.presets(), ["probe"])
        status, _h, _b = self.send("PUT", "/api/settings", {"chat_extra": "hi"}, {
            "Origin": self.origin,
            "Content-Type": "application/json; charset=utf-8",
        })
        self.assertEqual(status, 200)

    def test_localhost_name_for_the_same_port_is_the_page_too(self):
        status, _h, _b = self.send("POST", "/api/presets", PRESET, {
            "Origin": f"http://localhost:{self.app.ui_port}",
            "Content-Type": "application/json",
        })
        self.assertEqual(status, 200)

    def test_a_page_on_another_port_cannot_post_text_plain(self):
        # The request reproduced from a page on 127.0.0.1:8088 in headless Chrome.
        status, _h, body = self.send("POST", "/api/presets", json.dumps(PRESET).encode(), {
            "Origin": "http://127.0.0.1:8088",
            "Content-Type": "text/plain;charset=UTF-8",
        })
        self.assertEqual(status, 403)
        self.assertIn("did not come from the SuperBerry page", json.loads(body)["error"])
        self.assertEqual(self.presets(), [])

    def test_other_origins_are_refused_even_with_json(self):
        for origin in ("http://127.0.0.1:8088", f"http://localhost:{self.app.ui_port + 1}",
                       "https://evil.example", "null", f"https://127.0.0.1:{self.app.ui_port}"):
            status, _h, _b = self.send("POST", "/api/presets", PRESET, {
                "Origin": origin,
                "Content-Type": "application/json",
            })
            self.assertEqual(status, 403, origin)
        self.assertEqual(self.presets(), [])

    def test_missing_origin_uses_referer_then_refuses(self):
        status, _h, _b = self.send("POST", "/api/presets", PRESET, {"Content-Type": "application/json"})
        self.assertEqual(status, 403)
        status, _h, _b = self.send("POST", "/api/presets", PRESET, {
            "Referer": "http://127.0.0.1:8088/attack.html",
            "Content-Type": "application/json",
        })
        self.assertEqual(status, 403)
        status, _h, _b = self.send("POST", "/api/presets", PRESET, {
            "Referer": self.origin + "/",
            "Content-Type": "application/json",
        })
        self.assertEqual(status, 200)

    def test_own_origin_must_send_json(self):
        status, _h, _b = self.send("POST", "/api/presets", json.dumps(PRESET).encode(), {
            "Origin": self.origin,
            "Content-Type": "text/plain",
        })
        self.assertEqual(status, 415)
        self.assertEqual(self.presets(), [])

    def test_an_empty_body_needs_no_content_type(self):
        # api("/api/ledger/reset", { method: "POST" }) sends no body and no Content-Type.
        status, _h, _b = self.send("POST", "/api/shutdown", None, {"Origin": self.origin})
        self.assertEqual(status, 409)
        status, _h, _b = self.send("POST", "/api/shutdown", None, {"Origin": "http://127.0.0.1:8088"})
        self.assertEqual(status, 403)

    def test_bearer_token_clients_are_not_browser_pages(self):
        status, _h, _b = self.send("POST", "/api/presets", PRESET, {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }, cookie=False)
        self.assertEqual(status, 200)

    def test_reads_and_sign_in_links_are_unchanged(self):
        status, _h, _b = self.send("GET", "/api/presets", None, {"Origin": "http://127.0.0.1:8088"})
        self.assertEqual(status, 200)
        status, _h, _b = self.send("POST", "/api/presets", PRESET, {
            "Origin": self.origin,
            "Content-Type": "application/json",
        }, cookie=False)
        self.assertEqual(status, 401)
        status, headers, _b = self.send("GET", f"/boot?token={self.token}", cookie=False)
        self.assertEqual(status, 302)
        self.assertIn("SameSite=Strict", headers.get("Set-Cookie", ""))
        status, _h, _b = self.send("GET", "/oauth/kick/callback?error=access_denied", cookie=False)
        self.assertEqual(status, 200)

    def test_favicon_is_served_without_sign_in(self):
        status, headers, body = self.send("GET", "/favicon.ico", cookie=False)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertTrue(body.startswith(b"\x89PNG"))


class QuitAndNoticeTests(_Base):
    def test_quit_only_where_serve_allows_it(self):
        status, _h, body = self.send("GET", "/api/meta")
        self.assertFalse(json.loads(body)["can_quit"])
        called = []
        self.app.shutdown_hook = lambda: called.append(True)
        status, _h, body = self.send("GET", "/api/meta")
        self.assertTrue(json.loads(body)["can_quit"])
        status, _h, _b = self.send("POST", "/api/shutdown", None, {"Origin": "http://127.0.0.1:8088"})
        self.assertEqual(status, 403)
        self.assertEqual(called, [])
        status, _h, body = self.send("POST", "/api/shutdown", None, {"Origin": self.origin})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"stopping": True})
        self.assertEqual(called, [True])

    def test_notices_follow_stored_automatic_rows(self):
        self.store.notices = NoticeFeed()
        status, _h, body = self.send("GET", "/api/notices")
        self.assertEqual(json.loads(body), {"notices": [], "last": 0})
        self.store._notifier = object.__new__(type("Quiet", (), {"donations_added": lambda *a, **k: None}))
        donation = Donation(
            dedupe_key="yt:feed-1", platform="youtube", kind="super_chat", external_id="feed-1",
            channel="", author_name="Ada", author_id="", message="hi", amount_value=5.0,
            amount_micros=5_000_000, currency="USD", created_at="2026-10-17T12:00:00Z", raw={},
        )
        self.store.add_many([donation])
        status, _h, body = self.send("GET", "/api/notices")
        self.assertEqual(json.loads(body), {"notices": [], "last": 1})
        status, _h, body = self.send("GET", "/api/notices?after=0")
        notices = json.loads(body)["notices"]
        self.assertEqual(len(notices), 1)
        self.assertIn("Ada", notices[0]["title"])
        status, _h, body = self.send("GET", "/api/notices?after=1")
        self.assertEqual(json.loads(body)["notices"], [])

    def test_feed_keeps_only_the_newest(self):
        feed = NoticeFeed(limit=2)
        for number in range(3):
            feed.post(f"n{number}")
        self.assertEqual([item["title"] for item in feed.since(0)["notices"]], ["n1", "n2"])
        self.assertEqual(feed.since(None), {"notices": [], "last": 3})


class _Other(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(401)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        return


class RunningCopyTests(_Base):
    def test_probe_tells_superberry_from_other_servers(self):
        port = self.app.ui_port
        self.assertEqual(cli.running_superberry(port, self.token), "session")
        self.assertEqual(cli.running_superberry(port, "c" * 32), "running")
        self.assertEqual(cli.running_superberry(port, None), "running")
        other = HTTPServer(("127.0.0.1", 0), _Other)
        threading.Thread(target=other.serve_forever, daemon=True).start()
        try:
            self.assertIsNone(cli.running_superberry(other.server_address[1], None))
        finally:
            other.shutdown()
            other.server_close()
        free = HTTPServer(("127.0.0.1", 0), _Other)
        free_port = free.server_address[1]
        free.server_close()
        self.assertIsNone(cli.running_superberry(free_port, None, timeout=0.5))

    def test_open_reuses_the_session_when_the_token_matches(self):
        opened = []
        original = cli.open_in_browser
        cli.open_in_browser = lambda url: opened.append(url) or True
        try:
            self.assertEqual(cli._open_running_copy(self.app.ui_port, self.token, "session"), 0)
            self.assertEqual(cli._open_running_copy(self.app.ui_port, "x" * 32, "running"), 0)
        finally:
            cli.open_in_browser = original
        self.assertEqual(opened, [
            f"http://127.0.0.1:{self.app.ui_port}/boot?token={self.token}",
            f"http://127.0.0.1:{self.app.ui_port}/",
        ])


def _fake_browser(directory: Path) -> str:
    script = directory / "browser.py"
    script.write_text(
        "import sys\nopen(sys.argv[1], 'a').write(sys.argv[2] + '\\n')\n", encoding="utf-8"
    )
    return f"{sys.executable} {script} {directory / 'opened.txt'} %s"


@unittest.skipIf(os.name == "nt", "the fake BROWSER command is POSIX shell syntax")
class ServeOpenTests(unittest.TestCase):
    def _env(self, scratch: Path) -> dict:
        env = dict(os.environ)
        env.pop("STREAM_DONATIONS_STOP_ON_STDIN", None)
        env.update({
            "PYTHONPATH": str(ROOT / "collector"),
            "BROWSER": _fake_browser(scratch),
            "STREAM_DONATIONS_NOTIFY": "shell",
        })
        return env

    def test_open_then_quit_from_the_page(self):
        home = Path(os.path.expanduser("~"))
        data = Path(tempfile.mkdtemp(prefix="sd-browser-", dir=str(home)))
        scratch = Path(tempfile.mkdtemp())
        try:
            env = self._env(scratch)
            child = subprocess.Popen(
                [sys.executable, "-m", "streamdonations", "serve", "--port", "0", "--open", "--data-dir", str(data)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True,
            )
            try:
                ready = child.stdout.readline()
                self.assertTrue(ready.startswith("STREAM_DONATIONS_READY port="), ready)
                port = int(ready.strip().split("=", 1)[1])
                deadline = time.time() + 10
                opened = scratch / "opened.txt"
                while time.time() < deadline and not opened.exists():
                    time.sleep(0.1)
                url = opened.read_text(encoding="utf-8").strip()
                self.assertTrue(url.startswith(f"http://127.0.0.1:{port}/boot?token="), url)
                token = url.split("token=", 1)[1]
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/api/meta", headers={"Cookie": f"sd_session={token}"})
                meta = json.loads(connection.getresponse().read())
                self.assertTrue(meta["can_quit"])
                connection.request("POST", "/api/shutdown", headers={
                    "Cookie": f"sd_session={token}",
                    "Origin": f"http://127.0.0.1:{port}",
                })
                reply = connection.getresponse()
                self.assertEqual(reply.status, 200)
                reply.read()
                connection.close()
                self.assertEqual(child.wait(timeout=15), 0)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
                child.stdout.close()
        finally:
            shutil.rmtree(data, ignore_errors=True)
            shutil.rmtree(scratch, ignore_errors=True)

    def test_desktop_shell_mode_has_no_quit(self):
        home = Path(os.path.expanduser("~"))
        data = Path(tempfile.mkdtemp(prefix="sd-browser-", dir=str(home)))
        scratch = Path(tempfile.mkdtemp())
        try:
            env = self._env(scratch)
            # What Electron passes (main.js startCollector).
            env.update({"STREAM_DONATIONS_STOP_ON_STDIN": "1", "STREAM_DONATIONS_TOKEN": "d" * 32})
            child = subprocess.Popen(
                [sys.executable, "-m", "streamdonations", "serve", "--port", "0", "--data-dir", str(data)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True,
            )
            try:
                ready = child.stdout.readline()
                port = int(ready.strip().split("=", 1)[1])
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/api/meta", headers={"Cookie": "sd_session=" + "d" * 32})
                self.assertFalse(json.loads(connection.getresponse().read())["can_quit"])
                connection.request("POST", "/api/shutdown", headers={
                    "Cookie": "sd_session=" + "d" * 32,
                    "Origin": f"http://127.0.0.1:{port}",
                })
                reply = connection.getresponse()
                self.assertEqual(reply.status, 409)
                reply.read()
                connection.close()
                self.assertFalse((scratch / "opened.txt").exists())
                child.stdin.write("stop\n")
                child.stdin.close()
                self.assertEqual(child.wait(timeout=15), 0)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
                child.stdout.close()
        finally:
            shutil.rmtree(data, ignore_errors=True)
            shutil.rmtree(scratch, ignore_errors=True)

    def test_open_on_a_busy_port_opens_the_running_copy(self):
        home = Path(os.path.expanduser("~"))
        data = Path(tempfile.mkdtemp(prefix="sd-browser-", dir=str(home)))
        scratch = Path(tempfile.mkdtemp())
        first = None
        try:
            env = self._env(scratch)
            env["STREAM_DONATIONS_TOKEN"] = "e" * 32
            first = subprocess.Popen(
                [sys.executable, "-m", "streamdonations", "serve", "--port", "0", "--data-dir", str(data)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True,
            )
            port = int(first.stdout.readline().strip().split("=", 1)[1])
            second = subprocess.run(
                [sys.executable, "-m", "streamdonations", "serve", "--port", str(port), "--open", "--data-dir", str(data)],
                stdin=subprocess.DEVNULL, capture_output=True, env=env, text=True, timeout=30,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn(f"already running on port {port}", second.stdout)
            self.assertNotIn("STREAM_DONATIONS_READY", second.stdout)
            opened = (scratch / "opened.txt").read_text(encoding="utf-8").strip()
            self.assertEqual(opened, f"http://127.0.0.1:{port}/boot?token=" + "e" * 32)
        finally:
            if first is not None:
                first.terminate()
                first.wait(timeout=15)
                first.stdout.close()
            shutil.rmtree(data, ignore_errors=True)
            shutil.rmtree(scratch, ignore_errors=True)


class PageContractTests(unittest.TestCase):
    def setUp(self):
        self.script = (ROOT / "collector/streamdonations/web/app.js").read_text(encoding="utf-8")
        self.page = (ROOT / "collector/streamdonations/web/index.html").read_text(encoding="utf-8")
        self.main = (ROOT / "electron/main.js").read_text(encoding="utf-8")

    def test_electron_requests_match_the_check(self):
        # Electron's page origin and the headers api() sets are what the server accepts.
        self.assertIn("const origin = `http://127.0.0.1:${port}`;", self.main)
        self.assertIn('await window.loadURL(origin + "/");', self.main)
        api = self.script.split("async function api(", 1)[1].split("\nasync function ", 1)[0]
        self.assertIn('if (request.body) headers["Content-Type"] = "application/json";', api)
        self.assertIn("const response = await fetch(path, {", api)
        self.assertNotIn("http://", api)

    def test_browser_mode_ui(self):
        self.assertIn('id="quit-app"', self.page)
        quit_button = self.page.split('id="quit-app"', 1)[1].split(">", 1)[0]
        self.assertIn("hidden", quit_button)
        self.assertIn('<section id="browser-mode" hidden>', self.page)
        setup = self.script.split("function setupBrowserMode(", 1)[1].split("\nasync function ", 1)[0]
        self.assertIn("if (!browserMode()) return;", setup)
        self.assertIn("meta.can_quit", setup)
        self.assertIn("!meta.system_notifications", setup)
        self.assertIn("return !window.desktop;", self.script)
        toggle = self.script.split("function toggleBrowserNotify(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("requestPermission()", toggle)
        self.assertNotIn("requestPermission", self.script.split("function setupBrowserMode(", 1)[1].split("\nasync function ", 1)[0])
        for name in ("Youtube", "Kick", "Rumble"):
            sign_in = self.script.split(f"function open{name}SignIn(", 1)[1].split("\nfunction ", 1)[0]
            self.assertIn("openSiteInBrowser(", sign_in)
            chat = self.script.split(f"function open{name}Chat(", 1)[1].split("\nfunction ", 1)[0]
            self.assertIn("openChatInBrowser(", chat)
        popup = self.script.split("function openChatInBrowser(", 1)[1].split("\nfunction ", 1)[0]
        self.assertLess(popup.index("window.open("), popup.index("api(path)"))
        self.assertIn("popup.opener = null", popup)
        self.assertIn("Sending to Rumble chat needs the SuperBerry desktop app.", self.script)
        self.assertIn("embedded only in the SuperBerry desktop app", self.script)


if __name__ == "__main__":
    unittest.main()
