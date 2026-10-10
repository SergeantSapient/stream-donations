"""YouTube and Kick chat send, and the website pop-out chats."""

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from streamdonations.kick_public import KICK_LOGIN_URL, kick_popup_chat_url
from streamdonations.rumble import RUMBLE_LOGIN_URL, _CHAT_ID_IN_URL_RE, rumble_popup_chat_url
from streamdonations.youtube_public import YOUTUBE_LOGIN_URL, youtube_popup_chat_url
from streamdonations.server import App, Handler, ThreadingServer
from streamdonations.service import Service
from streamdonations.settings_file import export_settings
from streamdonations.store import Store
from streamdonations.transport import HttpResult
from streamdonations.util import ApiError
from streamdonations.youtube import scope_can_send


SECRET_EMAIL = "secret@example.com"
PHRASE = "purple-hedgehog-line"


class RecordingTransport:
    def __init__(self):
        self.calls = []

    def request(self, method, url, headers=None, json_body=None, form=None, **_kwargs):
        call = {"method": method, "url": url, "headers": headers or {}, "json_body": json_body, "form": form}
        self.calls.append(call)
        return self.respond(call)

    def respond(self, call):
        raise AssertionError(call["method"] + " " + call["url"])


def _result(url: str, status: int, payload: object) -> HttpResult:
    body = json.dumps(payload).encode("utf-8")
    return HttpResult(status, {}, body, url)


def _connect_youtube(store: Store, scope: str = "https://www.googleapis.com/auth/youtube.force-ssl") -> None:
    store.set_setting("youtube_client_id", "client")
    store.set_setting("youtube_client_secret", "secret")
    store.set_setting("youtube_refresh_token", "refresh")
    store.set_setting("youtube_access_token", "access")
    store.set_setting("youtube_access_expiry", str(time.time() + 3600))
    if scope:
        store.set_setting("youtube_granted_scope", scope)


def _connect_kick(store: Store, scope: str = "user:read channel:read events:subscribe kicks:read chat:write") -> None:
    store.set_setting("kick_client_id", "client")
    store.set_setting("kick_client_secret", "secret")
    store.set_setting("kick_refresh_token", "refresh")
    store.set_setting("kick_access_token", "access")
    store.set_setting("kick_access_expiry", str(time.time() + 3600))
    if scope:
        store.set_setting("kick_granted_scope", scope)


class ChatSendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.transport = RecordingTransport()
        self.service = Service(self.store, self.transport)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_scope_can_send_is_exact(self):
        self.assertTrue(scope_can_send("https://www.googleapis.com/auth/youtube.force-ssl"))
        self.assertTrue(scope_can_send("https://www.googleapis.com/auth/youtube"))
        self.assertFalse(scope_can_send("https://www.googleapis.com/auth/youtube.readonly"))
        self.assertFalse(scope_can_send("https://www.googleapis.com/auth/youtube.force-ssl.extra"))

    def test_youtube_posts_the_collapsed_line_into_the_watched_video(self):
        _connect_youtube(self.store)
        self.store.set_setting("youtube_watch", "abcdefghijk")

        def respond(call):
            if call["method"] == "GET" and "/videos?" in call["url"]:
                self.assertIn("id=abcdefghijk", call["url"])
                return _result(call["url"], 200, {"items": [{"liveStreamingDetails": {"activeLiveChatId": "LIVECHATID"}}]})
            if call["method"] == "POST" and "/liveChat/messages?" in call["url"]:
                return _result(call["url"], 200, {})
            raise AssertionError(call["method"] + " " + call["url"])

        self.transport.respond = respond
        self.service.youtube.send_message("  hello   there ")
        posts = [call for call in self.transport.calls if call["method"] == "POST"]
        self.assertEqual(len(posts), 1)
        snippet = posts[0]["json_body"]["snippet"]
        self.assertEqual(snippet["liveChatId"], "LIVECHATID")
        self.assertEqual(snippet["type"], "textMessageEvent")
        self.assertEqual(snippet["textMessageDetails"]["messageText"], "hello there")
        self.assertEqual(self.service.youtube._quota.units_used(), 21)

    def test_youtube_without_a_send_scope_makes_no_request(self):
        _connect_youtube(self.store, "https://www.googleapis.com/auth/youtube.readonly")
        self.store.set_setting("youtube_watch", "abcdefghijk")
        with self.assertRaises(ApiError) as caught:
            self.service.youtube.send_message("hello")
        self.assertIn("only read", str(caught.exception))
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.service.youtube._quota.units_used(), 0)

        self.store.delete_setting("youtube_granted_scope")
        with self.assertRaises(ApiError):
            self.service.youtube.send_message("hello")
        self.assertEqual(self.transport.calls, [])

    def test_youtube_waiting_room_has_no_chat_id(self):
        _connect_youtube(self.store)
        self.store.set_setting("youtube_watch", "abcdefghijk")

        def respond(call):
            if call["method"] == "GET" and "/videos?" in call["url"]:
                return _result(call["url"], 200, {"items": [{"liveStreamingDetails": {}}]})
            raise AssertionError(call["method"] + " " + call["url"])

        self.transport.respond = respond
        with self.assertRaises(ApiError) as caught:
            self.service.youtube.send_message("hello")
        self.assertIn("waiting room", str(caught.exception))
        self.assertEqual([call["method"] for call in self.transport.calls], ["GET"])
        self.assertEqual(self.service.youtube._quota.units_used(), 1)

    def test_youtube_refusal_does_not_echo_the_message(self):
        _connect_youtube(self.store)
        self.store.set_setting("youtube_watch", "abcdefghijk")

        def respond(call):
            if call["method"] == "GET" and "/videos?" in call["url"]:
                return _result(call["url"], 200, {"items": [{"liveStreamingDetails": {"activeLiveChatId": "LIVECHATID"}}]})
            if call["method"] == "POST":
                return _result(call["url"], 400, {
                    "error": {
                        "message": PHRASE,
                        "errors": [{"reason": "messageTextInvalid", "message": PHRASE}],
                    }
                })
            raise AssertionError(call["url"])

        self.transport.respond = respond
        with self.assertRaises(ApiError) as caught:
            self.service.youtube.send_message(PHRASE)
        self.assertNotIn(PHRASE, str(caught.exception))
        self.assertIn("rejected", str(caught.exception))

    def test_kick_posts_as_the_user_into_the_channel(self):
        _connect_kick(self.store)
        self.store.set_setting("kick_watch", "creator")

        def respond(call):
            if call["method"] == "GET" and "/public/v1/channels?" in call["url"]:
                self.assertIn("slug=creator", call["url"])
                return _result(call["url"], 200, {"data": [{"broadcaster_user_id": 4242, "slug": "creator"}]})
            if call["method"] == "POST" and call["url"].endswith("/public/v1/chat"):
                return _result(call["url"], 200, {"data": {"is_sent": True, "message_id": "m1"}})
            raise AssertionError(call["method"] + " " + call["url"])

        self.transport.respond = respond
        self.service.kick.send_message("  hello   there ")
        posts = [call for call in self.transport.calls if call["method"] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["json_body"]["type"], "user")
        self.assertEqual(posts[0]["json_body"]["content"], "hello there")
        self.assertEqual(posts[0]["json_body"]["broadcaster_user_id"], 4242)
        self.assertIsInstance(posts[0]["json_body"]["broadcaster_user_id"], int)

    def test_kick_without_chat_write_makes_no_request(self):
        _connect_kick(self.store, "user:read channel:read events:subscribe kicks:read")
        self.store.set_setting("kick_watch", "creator")
        with self.assertRaises(ApiError) as caught:
            self.service.kick.send_message("hello")
        self.assertIn("cannot post", str(caught.exception))
        self.assertEqual(self.transport.calls, [])

    def test_kick_refuses_over_500_graphemes_and_over_2048_bytes(self):
        _connect_kick(self.store)
        self.store.set_setting("kick_watch", "creator")
        with self.assertRaises(ValueError) as caught:
            self.service.kick.send_message("a" * 501)
        self.assertEqual(str(caught.exception), "Kick chat allows 500 characters.")
        wide = "a" + ("\u0301" * 1200)
        from streamdonations.kick import _grapheme_count
        self.assertLessEqual(_grapheme_count(wide), 500)
        self.assertGreater(len(wide.encode("utf-8")), 2048)
        with self.assertRaises(ValueError) as caught:
            self.service.kick.send_message(wide)
        self.assertEqual(str(caught.exception), "Kick chat allows 500 characters.")
        self.assertEqual(self.transport.calls, [])

    def test_kick_sender_name_does_not_store_email(self):
        _connect_kick(self.store)

        def respond(call):
            if call["method"] == "GET" and call["url"].endswith("/public/v1/users"):
                return _result(call["url"], 200, {"data": [{
                    "email": SECRET_EMAIL,
                    "name": "Berry",
                    "user_id": 42,
                    "profile_picture": "https://example.com/a.png",
                }]})
            raise AssertionError(call["url"])

        self.transport.respond = respond
        self.assertEqual(self.service.kick.sender_name(), "Berry")
        self.assertEqual(self.service.kick.sender_name(), "Berry")
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(self.store.get_setting("kick_sender_name"), "Berry")
        self.assertEqual(self.store.get_setting("kick_sender_id"), "42")
        stored = " ".join(
            str(row["key"]) + "=" + str(row["value"])
            for row in self.store._conn.execute("SELECT key, value FROM settings")
        )
        self.assertNotIn(SECRET_EMAIL, stored)
        exported = export_settings({
            "membership_price": "4.99",
            "kick_sender_name": "Berry",
            "youtube_sender_name": "Berry",
            "youtube_granted_scope": "https://www.googleapis.com/auth/youtube.force-ssl",
            "kick_granted_scope": "chat:write",
        }).decode("utf-8")
        self.assertNotIn("Berry", exported)
        self.assertNotIn("force-ssl", exported)
        self.assertNotIn("chat:write", exported)

    def test_one_platform_failing_still_tries_the_other(self):
        _connect_youtube(self.store)
        _connect_kick(self.store)
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("kick_watch", "creator")

        def respond(call):
            if "/videos?" in call["url"]:
                return _result(call["url"], 200, {"items": [{"liveStreamingDetails": {"activeLiveChatId": "LIVECHATID"}}]})
            if "/liveChat/messages?" in call["url"]:
                return _result(call["url"], 200, {})
            if "/public/v1/channels?" in call["url"]:
                return _result(call["url"], 200, {"data": [{"broadcaster_user_id": 7, "slug": "creator"}]})
            if call["url"].endswith("/public/v1/chat"):
                return _result(call["url"], 403, {"message": PHRASE})
            raise AssertionError(call["url"])

        self.transport.respond = respond
        payload = self.service.send_chat(PHRASE, ["youtube", "kick", "youtube"])
        self.assertEqual(
            [(row["platform"], row["ok"]) for row in payload["results"]],
            [("youtube", True), ("kick", False)],
        )
        self.assertEqual(sum(call["method"] == "POST" and "/liveChat/messages" in call["url"] for call in self.transport.calls), 1)
        self.assertEqual(sum(call["url"].endswith("/public/v1/chat") for call in self.transport.calls), 1)
        logged = "\n".join(row["message"] for row in self.store.recent_log())
        self.assertIn("Sent a chat message.", logged)
        self.assertIn("Chat was not sent.", logged)
        self.assertNotIn(PHRASE, logged)

    def test_send_refuses_rumble_empty_and_overlong_text(self):
        _connect_youtube(self.store)
        with self.assertRaises(ValueError) as caught:
            self.service.send_chat("hello", ["youtube", "rumble"])
        self.assertEqual(str(caught.exception), "Rumble chat opens in its own window.")
        with self.assertRaises(ValueError) as caught:
            self.service.send_chat("hello", [])
        self.assertEqual(str(caught.exception), "Choose YouTube, Kick, or both.")
        with self.assertRaises(ValueError) as caught:
            self.service.send_chat("   ", ["youtube"])
        self.assertEqual(str(caught.exception), "Type a message.")
        with self.assertRaises(ValueError) as caught:
            self.service.send_chat("a" * 2001, ["youtube"])
        self.assertEqual(str(caught.exception), "That message is too long.")
        self.assertEqual(self.transport.calls, [])

    def test_destinations_do_not_resolve_rumble(self):
        self.store.set_setting("rumble_watch", "446413572")
        self.store.set_setting("youtube_follow_live", "1")
        self.store.set_setting("youtube_channel", "https://www.youtube.com/@example")
        self.store.set_setting("kick_watch", "creator")
        payload = self.service.chat_destinations()
        self.assertEqual(set(payload), {"youtube", "kick"})
        self.assertFalse(payload["youtube"]["can_send"])
        self.assertIn("Connect a YouTube", payload["youtube"]["reason"])
        self.assertEqual(self.transport.calls, [])

    def test_rumble_popup_uses_the_numeric_chat_id(self):
        self.assertEqual(RUMBLE_LOGIN_URL, "https://rumble.com/login.php")
        url = rumble_popup_chat_url("446413572")
        self.assertEqual(url, "https://rumble.com/chat/popup/446413572")
        match = _CHAT_ID_IN_URL_RE.search(urlparse(url).path)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "446413572")
        with self.assertRaises(ValueError):
            rumble_popup_chat_url("v7ds6x0")
        self.store.set_setting("rumble_watch", "446413572")
        self.assertEqual(self.service.rumble_popup(), {"url": url})
        self.store.set_setting("rumble_follow_live", "1")
        self.store.set_setting("rumble_channel", "https://rumble.com/c/example")
        self.service.rumble.resolve_live_channel = lambda _channel: "12"
        self.assertEqual(self.service.rumble_popup()["url"], "https://rumble.com/chat/popup/12")

        def explode(_target):
            raise ApiError("https://rumble.com/vsecret-leaked")

        self.store.set_setting("rumble_follow_live", "0")
        self.service.rumble.resolve_chat_id = explode
        with self.assertRaises(ApiError) as caught:
            self.service.rumble_popup()
        self.assertEqual(str(caught.exception), "That Rumble chat could not be opened.")
        self.assertNotIn("leaked", str(caught.exception))

    def test_youtube_and_kick_popups_use_the_site_chat(self):
        self.assertEqual(YOUTUBE_LOGIN_URL, "https://www.youtube.com/signin")
        self.assertEqual(KICK_LOGIN_URL, "https://kick.com/login")
        youtube = youtube_popup_chat_url("abcdefghijk")
        self.assertEqual(youtube, "https://www.youtube.com/live_chat?is_popout=1&v=abcdefghijk")
        with self.assertRaises(ValueError):
            youtube_popup_chat_url("short")
        with self.assertRaises(ValueError):
            youtube_popup_chat_url("abcdefghijk&extra=1")
        kick = kick_popup_chat_url("Creator")
        self.assertEqual(kick, "https://kick.com/popout/creator/chat")
        self.assertEqual(kick_popup_chat_url("12345"), "https://kick.com/popout/12345/chat")
        with self.assertRaises(ValueError):
            kick_popup_chat_url("bad/slug")
        with self.assertRaises(ValueError):
            kick_popup_chat_url("")

        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.assertEqual(self.service.youtube_popup(), {"url": youtube})
        self.assertEqual(self.transport.calls, [])
        self.store.set_setting("youtube_follow_live", "1")
        self.store.set_setting("youtube_channel", "https://www.youtube.com/@example")
        self.service.youtube.public_live_video_id = lambda _channel: "abcdefghijk"
        self.assertEqual(self.service.youtube_popup()["url"], youtube)
        self.service.youtube.public_live_video_id = lambda _channel: None
        with self.assertRaises(ValueError) as caught:
            self.service.youtube_popup()
        self.assertEqual(str(caught.exception), "That YouTube channel has no chat open.")

        def explode(_target):
            raise ApiError("https://www.youtube.com/watch?v=secret-leaked")

        self.store.set_setting("youtube_follow_live", "0")
        self.service.youtube.public_video_id = explode
        with self.assertRaises(ApiError) as caught:
            self.service.youtube_popup()
        self.assertEqual(str(caught.exception), "That YouTube chat could not be opened.")
        self.assertNotIn("leaked", str(caught.exception))

        self.store.set_setting("kick_watch", "https://kick.com/Creator")
        self.assertEqual(self.service.kick_popup(), {"url": kick})
        self.assertEqual(self.transport.calls, [])
        self.store.set_setting("kick_watch", "")
        with self.assertRaises(ValueError) as caught:
            self.service.kick_popup()
        self.assertEqual(str(caught.exception), "This ledger has no Kick channel.")


class ChatSendApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.transport = RecordingTransport()
        self.service = Service(self.store, self.transport)
        self.token = "b" * 32
        self.app = App(self.store, self.token, 1, service=self.service)
        self.server = ThreadingServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.app.ui_port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.app.ui_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.stop_webhook()
        self.store.close()
        self.tmp.cleanup()

    def _request(self, path, method="GET", body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Cookie": f"sd_session={self.token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read()
            finally:
                exc.close()

    def test_partial_send_is_http_200_and_meta_scopes_match(self):
        _connect_youtube(self.store)
        _connect_kick(self.store)
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("kick_watch", "creator")

        def respond(call):
            if "/videos?" in call["url"]:
                return _result(call["url"], 200, {"items": [{"liveStreamingDetails": {"activeLiveChatId": "LIVECHATID"}}]})
            if "/liveChat/messages?" in call["url"]:
                return _result(call["url"], 200, {})
            if "/public/v1/channels?" in call["url"]:
                return _result(call["url"], 200, {"data": [{"broadcaster_user_id": 7, "slug": "creator"}]})
            if call["url"].endswith("/public/v1/chat"):
                return _result(call["url"], 403, {"message": PHRASE})
            raise AssertionError(call["url"])

        self.transport.respond = respond
        status, body = self._request("/api/chat/send", "POST", {"text": PHRASE, "platforms": ["kick", "youtube"]})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual([(row["platform"], row["ok"]) for row in payload["results"]], [("kick", False), ("youtube", True)])
        logged = "\n".join(row["message"] for row in self.store.recent_log())
        self.assertNotIn(PHRASE, logged)
        status, body = self._request("/api/chat/send", "POST", {"text": "hello", "platforms": ["rumble"]})
        self.assertEqual(status, 400)
        self.assertIn(b"own window", body)
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("kick_watch", "creator")
        status, body = self._request("/api/chat/youtube")
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body)["url"],
            "https://www.youtube.com/live_chat?is_popout=1&v=abcdefghijk",
        )
        status, body = self._request("/api/chat/kick")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["url"], "https://kick.com/popout/creator/chat")
        self.store.set_setting("youtube_watch", "")
        status, body = self._request("/api/chat/youtube")
        self.assertEqual(status, 400)
        self.assertIn(b"no YouTube video", body)
        status, meta = self._request("/api/meta")
        self.assertEqual(status, 200)
        scopes = json.loads(meta)["scopes"]
        self.assertEqual(scopes["youtube"], "https://www.googleapis.com/auth/youtube.force-ssl")
        self.assertIn("chat:write", scopes["kick"])
        self.assertNotIn("readonly", scopes["youtube"])


class ChatSendPageTests(unittest.TestCase):
    def test_composer_and_rumble_window_contract(self):
        root = Path(__file__).resolve().parents[2]
        page = (root / "collector/streamdonations/web/index.html").read_text(encoding="utf-8")
        script = (root / "collector/streamdonations/web/app.js").read_text(encoding="utf-8")
        styles = (root / "collector/streamdonations/web/styles.css").read_text(encoding="utf-8")
        main = (root / "electron/main.js").read_text(encoding="utf-8")
        preload = (root / "electron/preload.js").read_text(encoding="utf-8")
        self.assertIn('id="chat-compose"', page)
        self.assertIn('id="chat-target-youtube"', page)
        self.assertIn('id="chat-target-kick"', page)
        self.assertIn('id="chat-send"', page)
        self.assertIn('id="youtube-sign-in"', page)
        self.assertIn('id="youtube-popout"', page)
        self.assertIn('id="kick-sign-in"', page)
        self.assertIn('id="kick-popout"', page)
        self.assertIn('id="rumble-sign-in"', page)
        self.assertIn('id="rumble-popout"', page)
        self.assertIn('aria-label="YouTube sign-in"', page)
        self.assertIn('title="Open YouTube chat"', page)
        self.assertIn('aria-label="Kick sign-in"', page)
        self.assertIn('title="Open Kick chat"', page)
        self.assertIn('aria-label="Rumble sign-in"', page)
        self.assertIn('title="Open Rumble chat"', page)
        self.assertIn('src="/icons/youtube.svg"', page)
        self.assertIn('src="/icons/kick.svg"', page)
        self.assertIn('src="/icons/rumble.svg"', page)
        self.assertIn('<select id="watch-platforms"', page)
        self.assertLess(page.index('id="watch-platforms"'), page.index('id="watch-reload"'))
        toolbar = styles.split(".watch-toolbar {", 1)[1].split("}", 1)[0]
        self.assertIn("nowrap", toolbar)
        self.assertNotIn("#watch-platforms .pill", script)
        self.assertIn('id="send-chat-line"', page)
        self.assertIn('id="send-donor-bar"', page)
        self.assertIn('id="status-send-total"', page)
        self.assertIn('id="status-send-donors"', page)
        self.assertNotIn('id="watch-chat-input"', page)
        self.assertIn("Nothing is typed into chat", page)
        self.assertIn('setAttribute("aria-label", "Send thank you")', script)
        self.assertIn("stream-donations-chat-targets", script)
        self.assertIn("requestSubmit()", script)
        self.assertIn("Open SuperBerry to use the Rumble window.", script)
        self.assertIn("Open SuperBerry to use the YouTube window.", script)
        self.assertIn("Open SuperBerry to use the Kick window.", script)
        self.assertIn(".chat-target[aria-pressed=\"true\"]", styles)
        refresh = script.split("async function refresh(", 1)[1].split("\nasync function ", 1)[0]
        self.assertNotIn("/api/chat", refresh)
        self.assertIn("loadChatDestinations()", refresh)
        self.assertIn("https://rumble.com/login.php", main)
        self.assertIn("persist:stream-donations-rumble", main)
        self.assertIn("stream-donations-rumble", main)
        self.assertIn("[0-9]{1,20}", main)
        prefs = main.split("function rumbleWebPreferences()", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn("preload", prefs)
        self.assertIn("sandbox: true", prefs)
        login = main.split("function openRumbleLogin()", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("RUMBLE_LOGIN_URL", login)
        self.assertNotIn("payload", login)
        self.assertIn("closeRumbleWindows()", main)
        self.assertIn("rumbleSignIn()", preload)
        self.assertIn("rumbleChat(url)", preload)
        self.assertIn("rumbleSend(text, url)", preload)
        send_bridge = preload.split("rumbleSend(text, url)", 1)[1].split("youtubeSignIn()", 1)[0]
        self.assertIn('action: "send"', send_bridge)
        self.assertNotIn("script", send_bridge)
        self.assertIn('id="chat-target-rumble"', page)
        targets = page.split('class="watch-chat-targets"', 1)[1].split('id="chat-target-note"', 1)[0]
        self.assertLess(targets.index("chat-target-youtube"), targets.index("chat-target-rumble"))
        self.assertLess(targets.index("chat-target-rumble"), targets.index("chat-target-kick"))
        rumble_target = page.split('id="chat-target-rumble"', 1)[1].split(">", 1)[0]
        self.assertIn("disabled", rumble_target)
        self.assertIn('aria-pressed="false"', rumble_target)
        self.assertIn("Rumble’s terms do not allow a script to type or send chat.", page)
        self.assertIn('id="rumble-chat-continue"', page)
        self.assertIn('aria-describedby="rumble-chat-warning"', page)
        self.assertNotIn("SuperBerry does not post into Rumble chat.", page)
        self.assertIn("stream-donations-rumble-chat", script)
        self.assertIn("localStorage.getItem(RUMBLE_CHAT_KEY)", script)
        self.assertIn("Continue on the Rumble page before chat can be posted there.", script)
        self.assertIn("rumbleSend(message, url)", script)
        posted = script.split("function postRumbleChat(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("rumbleChatAllowed()", posted)
        self.assertNotIn("executeJavaScript", posted)
        composer = script.split("function sendChatText(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('name !== "rumble"', composer)
        self.assertIn("platforms: apiPlatforms", composer)
        self.assertIn("postRumbleChat(", composer)
        self.assertNotIn("executeJavaScript", composer)
        settings = (root / "collector/streamdonations/settings_file.py").read_text(encoding="utf-8")
        self.assertNotIn("stream-donations-rumble-chat", settings)
        injection = main.split("function sendRumbleChat(", 1)[1].split('ipcMain.handle("stream-donations-rumble"', 1)[0]
        self.assertIn("executeJavaScript", injection)
        self.assertIn("rumbleSendCode(classified.text)", injection)
        self.assertIn("deliverRumbleSend(", injection)
        self.assertIn("waitForFreshRumbleChat(", injection)
        sender = main.split("function rumbleSendContents(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("openRumbleChat(", sender)
        self.assertNotIn("openHiddenRumbleChat(", sender)
        self.assertNotIn("focusable:", main)
        browser = main.split("function openRumbleBrowserWindow(", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn("show:", browser)
        self.assertNotIn("skipTaskbar", browser)
        self.assertNotIn("preload", browser)
        self.assertNotIn("payload", injection)
        self.assertNotIn("cookie", injection.lower())
        code = main.split("function rumbleSendCode(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("JSON.stringify(text)", code)
        self.assertIn("#chat-message-form", code)
        self.assertIn("form.chat-message-form", code)
        self.assertIn("#chat-message-text-input", code)
        self.assertIn(".chat--signin-container", code)
        self.assertIn('"click:"', code)
        self.assertIn("subscribe", code)
        self.assertIn('"overlay"', code)
        self.assertIn("fieldRect.bottom", code)
        self.assertIn("parentElement", code)
        self.assertNotIn('return "sent"', code)
        self.assertNotIn("payload", code)
        click = main.split("function clickRumbleSend(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("Input.dispatchMouseEvent", click)
        self.assertIn("mousePressed", click)
        self.assertIn("mouseReleased", click)
        self.assertNotIn("payload", click)
        cleared = main.split("function rumbleFieldCleared(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("JSON.stringify(text)", cleared)
        self.assertIn("#chat-message-text-input", cleared)
        self.assertIn('"sent"', cleared)
        self.assertIn('"failed"', cleared)
        self.assertIn('"rejected"', cleared)
        self.assertIn("problem submitting", cleared)
        self.assertNotIn("payload", cleared)
        handler = main.split('ipcMain.handle("stream-donations-rumble"', 1)[1].split("\nconst YOUTUBE_LOGIN_URL", 1)[0]
        self.assertIn('action === "send"', handler)
        self.assertIn("sendRumbleChat(payload && payload.text, payload && payload.url)", handler)
        self.assertNotIn("payload.script", handler)
        self.assertNotIn("executeJavaScript", handler)
        self.assertIn("youtubeSignIn()", preload)
        self.assertIn("youtubeChat(url)", preload)
        self.assertIn("kickSignIn()", preload)
        self.assertIn("kickChat(url)", preload)
        self.assertNotIn("preload.js", prefs)
        self.assertIn(YOUTUBE_LOGIN_URL, main)
        self.assertIn(KICK_LOGIN_URL, main)
        self.assertIn("persist:stream-donations-youtube", main)
        self.assertIn("persist:stream-donations-kick", main)
        self.assertIn("accounts.google.com", main)
        self.assertIn("id.kick.com", main)
        self.assertIn("is_popout", main)
        self.assertIn("/popout/", main)
        site = main.split("function openSiteBrowserWindow(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("sandbox: true", site)
        self.assertNotIn("preload", site)
        for name in ("openYoutubeLogin()", "openKickLogin()"):
            body = main.split("function " + name, 1)[1].split("\nfunction ", 1)[0]
            self.assertNotIn("payload", body)
            self.assertNotIn("http", body)
        self.assertIn("closeYoutubeWindows()", main)
        self.assertIn("closeKickWindows()", main)
        self.assertIn('id="utility-toggle"', page)
        self.assertLess(page.index('id="utility-toggle"'), page.index('id="export-button"'))
        self.assertIn('aria-label="Kiosk view"', page)
        self.assertIn('title="Kiosk view"', page)
        utility = page.split('id="utility-view"', 1)[1].split(">", 1)[0]
        self.assertIn("hidden", utility)
        ledger = page.split('class="utility-ledger"', 1)[1].split('id="utility-rows"', 1)[0]
        self.assertLess(ledger.index("<th>Time</th>"), ledger.index("<th>Donor</th>"))
        self.assertLess(ledger.index("<th>Donor</th>"), ledger.index("<th>Amount</th>"))
        self.assertLess(ledger.index("<th>Amount</th>"), ledger.index("<th>Platform</th>"))
        self.assertLess(ledger.index("<th>Platform</th>"), ledger.index("<th>Type</th>"))
        self.assertIn('id="utility-frame"', page)
        self.assertIn('id="utility-source"', page)
        self.assertLess(page.index('id="utility-source"'), page.index('id="utility-play"'))
        self.assertLess(page.index('id="utility-play"'), page.index('id="utility-live"'))
        self.assertLess(page.index('id="utility-live"'), page.index('id="utility-mute"'))
        self.assertLess(page.index('id="utility-mute"'), page.index('id="utility-reload"'))
        self.assertIn('id="utility-copy-total"', page)
        self.assertIn('id="utility-copy-donors"', page)
        blurbs = page.split('class="utility-blurbs"', 1)[1].split('class="utility-ledger"', 1)[0]
        self.assertLess(blurbs.index('id="utility-copy-total"'), blurbs.index('id="utility-send-total"'))
        self.assertLess(blurbs.index('id="utility-send-total"'), blurbs.index('id="utility-copy-donors"'))
        self.assertLess(blurbs.index('id="utility-copy-donors"'), blurbs.index('id="utility-send-donors"'))
        self.assertIn('aria-label="Send stream total"', blurbs)
        self.assertIn('aria-label="Send top 3 donors"', blurbs)
        utility_bind = script.split("function bindUtility(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('sendChatText($("chat-line").value)', utility_bind)
        self.assertIn("sendChatText(topDonorCopyText())", utility_bind)
        self.assertIn('id="utility-rows"', page)
        self.assertIn('id="utility-chat-youtube"', page)
        self.assertIn('id="utility-chat-kick"', page)
        self.assertIn('id="utility-chat-rumble"', page)
        chats = page.split('class="utility-chats"', 1)[1].split('id="stream-banner"', 1)[0]
        self.assertLess(chats.index("utility-chat-youtube"), chats.index("utility-chat-rumble"))
        self.assertLess(chats.index("utility-chat-rumble"), chats.index("utility-chat-kick"))
        self.assertIn("<th>Time</th>", page)
        self.assertIn("<th>Donor</th>", page)
        self.assertIn("<th>Amount</th>", page)
        self.assertIn("<th>Platform</th>", page)
        self.assertIn("<th>Type</th>", page)
        self.assertIn("stream-donations-utility", script)
        self.assertIn('createElement("webview")', script)
        self.assertIn("persist:stream-donations-youtube", script)
        self.assertIn("persist:stream-donations-kick", script)
        self.assertIn("persist:stream-donations-rumble", script)
        self.assertNotIn("executeJavaScript", script)
        shown = script.split("function showWatchFrame(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("utility-frame", shown)
        self.assertIn("watch-frame", shown)
        self.assertIn("blankWatchFrame(", shown)
        loading = script.split("function loadWatch(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("watchSurfaceOpen()", loading)
        painted = script.split("function renderStream(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("syncUtilityChats()", painted)
        self.assertNotIn("/api/chat", painted)
        self.assertNotIn("syncUtilityChats", refresh)
        self.assertIn(".app-shell.is-utility", styles)
        self.assertIn("grid-template-areas", styles)
        layout = styles.split(".app-shell.is-utility #utility-view {", 1)[1].split("}", 1)[0]
        self.assertIn('"video side"', layout)
        self.assertIn('"chats chats"', layout)
        self.assertIn("minmax(18rem, 36rem)", layout)
        self.assertIn("20.25rem", layout)
        self.assertNotIn("ledger blurbs", layout)
        stage = styles.split(".utility-stage {", 1)[1].split("}", 1)[0]
        self.assertIn("aspect-ratio: 16 / 9", stage)
        self.assertNotIn("max-height: 10.125rem", stage)
        self.assertIn(".utility-stage:hover .utility-overlay", styles)
        kiosk_rows = script.split("function renderUtilityRows(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("utilityUsd(", kiosk_rows)
        self.assertIn(".slice().reverse()", kiosk_rows)
        self.assertIn("scrollTop", kiosk_rows)
        usd = script.split("function utilityUsd(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("formatStreamUsd(", usd)
        self.assertIn("amount_usd", usd)
        self.assertIn("webviewTag: true", main)
        self.assertIn("will-attach-webview", main)
        self.assertIn("did-attach-webview", main)
        guest = main.split("function attachChatWebviews(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("delete webPreferences.preload", guest)
        self.assertIn("nodeIntegration = false", guest)
        self.assertIn("contextIsolation = true", guest)
        self.assertIn("sandbox = true", guest)
        self.assertIn("event.preventDefault()", guest)
        self.assertNotIn("executeJavaScript", guest)
        self.assertNotIn("webviewTag", site)


if __name__ == "__main__":
    unittest.main()
