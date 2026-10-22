"""1.12.0: presets carry !total and !top, settings save one field at a time,
and website sign-in lives in each platform's dialog."""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from streamdonations.presets import COMMAND_FIELDS, ledger_settings, parse_presets, upsert_preset
from streamdonations.service import Service
from streamdonations.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_commands import FakeTransport  # noqa: E402

YOUTUBE = "https://www.youtube.com/@presetstreamer"
KICK = "https://kick.com/presetstreamer"
WEB = Path(__file__).resolve().parents[1] / "streamdonations/web"


class PresetCommandTests(unittest.TestCase):
    def test_switches_round_trip_and_default_off(self):
        presets, saved = upsert_preset(
            [], "Ada", YOUTUBE, "", KICK, commands={"youtube_chat_commands": True, "kick_chat_commands": "1"}
        )
        self.assertIs(saved["youtube_chat_commands"], True)
        self.assertIs(saved["rumble_chat_commands"], False)
        self.assertIs(saved["kick_chat_commands"], True)
        again = parse_presets(json.dumps(presets))
        self.assertEqual({key: again[0][key] for key in COMMAND_FIELDS},
                         {"youtube_chat_commands": True, "rumble_chat_commands": False, "kick_chat_commands": True})
        # A replace without switches turns them off, the same as goals.
        presets, replaced = upsert_preset(presets, "ada", YOUTUBE, "", KICK)
        self.assertEqual(replaced["id"], saved["id"])
        self.assertFalse(any(replaced[key] for key in COMMAND_FIELDS))
        # "0" is off. bool("0") would be on.
        _presets, zero = upsert_preset([], "Bee", YOUTUBE, "", "", commands={"youtube_chat_commands": "0"})
        self.assertIs(zero["youtube_chat_commands"], False)

    def test_an_older_preset_without_switches_still_loads(self):
        older = parse_presets(json.dumps([{"id": "ok-1", "name": "Ada", "youtube_channel": YOUTUBE}]))
        self.assertEqual(len(older), 1)
        for key in COMMAND_FIELDS:
            self.assertIs(older[0][key], False)
        body = ledger_settings(older[0])
        for key in COMMAND_FIELDS:
            self.assertIs(body[key], False)

    def test_ledger_settings_carry_the_switches(self):
        _presets, saved = upsert_preset([], "Ada", YOUTUBE, "", "", commands={"rumble_chat_commands": True})
        body = ledger_settings(saved)
        self.assertIs(body["rumble_chat_commands"], True)
        self.assertIs(body["youtube_chat_commands"], False)
        self.assertIs(body["kick_chat_commands"], False)


class PresetApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.service = Service(self.store, FakeTransport())
        self.store.stream_tabs()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_applying_a_preset_sets_the_ledger_switches(self):
        self.service.apply_settings({"rumble_chat_commands": True})
        saved = self.store.save_streamer_preset(
            "Ada", YOUTUBE, "", KICK, commands={"youtube_chat_commands": True, "kick_chat_commands": True}
        )
        self.assertNotEqual(self.store.get_setting("youtube_chat_commands"), "1")
        self.service.apply_preset(saved["id"])
        self.assertEqual(self.store.get_setting("youtube_chat_commands"), "1")
        self.assertEqual(self.store.get_setting("kick_chat_commands"), "1")
        # The preset says Rumble is off, so the ledger's Rumble switch goes off.
        self.assertEqual(self.store.get_setting("rumble_chat_commands"), "0")
        tab = next(item for item in self.store.ledger_snapshots() if item["id"] == self.store._active_stream_id())
        self.assertEqual(tab["youtube_chat_commands"], "1")
        self.assertEqual(tab["rumble_chat_commands"], "0")

    def test_the_save_route_passes_the_switches_through(self):
        from streamdonations.server import App
        app = App(self.store, "t" * 32, 47321, service=self.service)
        body = json.dumps({
            "action": "save", "name": "Ada", "youtube_channel": YOUTUBE,
            "kick_chat_commands": True,
        }).encode()
        response = app.handle("POST", "/api/presets", {}, {"Authorization": "Bearer " + "t" * 32}, body)
        self.assertEqual(response.status, 200, response.body)
        preset = json.loads(response.body)["preset"]
        self.assertIs(preset["kick_chat_commands"], True)
        self.assertIs(preset["youtube_chat_commands"], False)


class OneFieldSaveTests(unittest.TestCase):
    """The dialogs save each field on its own. Saving must not keep restarting things."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.service = Service(self.store, FakeTransport())
        self.store.stream_tabs()
        self.restarts = 0
        self.service.on_listen_change = self._restart

    def _restart(self):
        self.restarts += 1

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _arm(self):
        self.service.youtube._next_super = 99.0
        self.service.youtube._next_broadcast = 99.0
        self.service.rumble._next_poll = 99.0
        self.service.kick._next_status = 99.0

    def _timers(self):
        return (
            self.service.youtube._next_super,
            self.service.youtube._next_broadcast,
            self.service.rumble._next_poll,
            self.service.kick._next_status,
        )

    def test_a_command_switch_does_not_move_any_poll(self):
        self._arm()
        self.service.apply_settings({"youtube_chat_commands": True})
        self.service.apply_settings({"kick_chat_commands": True})
        self.assertEqual(self._timers(), (99.0, 99.0, 99.0, 99.0))

    def test_only_the_changed_platform_polls_soon(self):
        self._arm()
        self.service.apply_settings({"rumble_poll_seconds": 20})
        self.assertEqual(self._timers(), (99.0, 99.0, 0, 99.0))
        self._arm()
        self.service.apply_settings({"rumble_poll_seconds": 20})
        self.assertEqual(self._timers(), (99.0, 99.0, 99.0, 99.0))
        self.service.apply_settings({"youtube_client_id": "client"})
        self.assertEqual(self._timers()[:2], (0, 0))
        self.assertEqual(self._timers()[2:], (99.0, 99.0))

    def test_settings_tab_fields_move_no_poll(self):
        self._arm()
        self.service.apply_settings({"membership_price": "4.99"})
        self.service.apply_settings({"show_top_donors": False})
        self.service.apply_settings({"chat_extra": "hi"})
        self.service.apply_settings({"ledger_page_size": 25})
        self.assertEqual(self._timers(), (99.0, 99.0, 99.0, 99.0))

    def test_the_webhook_listener_restarts_only_when_host_or_port_changes(self):
        self.service.apply_settings({"kick_webhook_port": 47333})
        self.assertEqual(self.restarts, 1)
        self.service.apply_settings({"kick_webhook_port": 47333})
        self.service.apply_settings({"kick_watch": KICK})
        self.assertEqual(self.restarts, 1)
        self.service.apply_settings({"kick_webhook_host": "127.0.0.1"})
        self.service.apply_settings({"kick_webhook_host": "127.0.0.1"})
        self.assertEqual(self.restarts, 2)

    def test_an_invalid_value_saves_nothing(self):
        self.service.apply_settings({"rumble_poll_seconds": 20})
        with self.assertRaisesRegex(ValueError, "Rumble poll seconds"):
            self.service.apply_settings({"rumble_poll_seconds": "999"})
        self.assertEqual(self.store.get_setting("rumble_poll_seconds"), "20")


class PageTests(unittest.TestCase):
    def setUp(self):
        self.page = (WEB / "index.html").read_text(encoding="utf-8")
        self.script = (WEB / "app.js").read_text(encoding="utf-8")
        self.styles = (WEB / "styles.css").read_text(encoding="utf-8")

    def _block(self, start, end):
        part = self.page[self.page.index(start):]
        return part[:part.index(end)]

    def test_sign_in_is_in_each_dialog_and_pop_outs_stay_in_the_stream_panel(self):
        panel = self._block('<aside id="watch-panel"', "</aside>")
        for platform in ("youtube", "kick", "rumble"):
            self.assertNotIn(f'id="{platform}-sign-in"', panel)
            self.assertIn(f'id="{platform}-popout"', panel)
            dialog = self._block(f'<dialog id="tab-{platform}"', "</dialog>")
            self.assertIn(f'id="{platform}-sign-in"', dialog)
            self.assertIn("Website sign-in", dialog)
            self.assertNotIn("under the chat", dialog)
            self.assertIn(f'$("{platform}-sign-in")', self.script)
        self.assertIn('class="watch-chat-popouts"', panel)
        self.assertNotIn("watch-chat-site", self.page + self.styles)
        self.assertNotIn(".watch-chat-rumble", self.styles)
        # Connect stays an explicit button next to website sign-in.
        youtube = self._block('<dialog id="tab-youtube"', "</dialog>")
        self.assertLess(youtube.index('id="youtube-connect"'), youtube.index('id="youtube-sign-in"'))
        kick = self._block('<dialog id="tab-kick"', "</dialog>")
        self.assertLess(kick.index('id="kick-connect"'), kick.index('id="kick-sign-in"'))

    def test_settings_dialogs_have_no_save_button_and_a_saved_note(self):
        for dialog_id in ("tab-youtube", "tab-rumble", "tab-kick", "tab-settings"):
            dialog = self._block(f'<dialog id="{dialog_id}"', "</dialog>")
            self.assertNotIn('<button type="submit"', dialog)
            self.assertIn("data-autosave-note", dialog)
        # A named record and one-shot actions stay explicit.
        presets = self._block('<dialog id="tab-streamers"', "</dialog>")
        self.assertIn('<button type="submit" class="primary">Save preset</button>', presets)
        for explicit in ('id="youtube-connect"', 'id="kick-disconnect"', 'id="rumble-clear"',
                         'id="settings-import"', 'id="settings-export"', 'id="fx-refresh"'):
            self.assertIn(explicit, self.page)

    def test_every_autosave_field_exists_and_has_a_known_key(self):
        from streamdonations.service import _ALLOWED_SETTINGS
        fields = re.findall(r'\{ id: "([a-z-]+)", key: "([a-z_]+)", kind: "([a-z-]+)"', self.script)
        self.assertGreaterEqual(len(fields), 25)
        for element_id, key, kind in fields:
            self.assertIn(f'id="{element_id}"', self.page, element_id)
            self.assertIn(key, _ALLOWED_SETTINGS | {"youtube_client_secret", "kick_client_secret"}, key)
            self.assertIn(kind, {"text", "raw", "number", "check", "secret", "page-size"})
        keys = {key for _id, key, _kind in fields}
        for key in ("youtube_chat_commands", "rumble_chat_commands", "kick_chat_commands",
                    "kick_webhook_port", "membership_price", "show_top_donors", "ledger_page_size"):
            self.assertIn(key, keys)

    def test_autosave_waits_for_a_commit_and_flushes_on_close(self):
        self.assertIn("const AUTOSAVE_DELAY_MS = 500;", self.script)
        bind = self.script[self.script.index("function bindAutosave()"):]
        bind = bind[:bind.index("\n}\n")]
        self.assertIn('addEventListener("change"', bind)
        self.assertIn('addEventListener("blur"', bind)
        self.assertNotIn('addEventListener("input"', bind)
        self.assertIn('dialog.addEventListener("close"', bind)
        self.assertIn("closing: true", bind)
        # Ledger actions wait for a save from a dialog that just closed.
        self.assertIn("const run = streamChain.then(settle, settle).then(task, task);", self.script)

    def test_preset_form_has_the_three_switches(self):
        presets = self._block('<form id="preset-form">', "</form>")
        for platform in ("youtube", "rumble", "kick"):
            self.assertIn(f'id="preset-{platform}-commands"', presets)
        self.assertIn("...presetCommandSwitches()", self.script)
        self.assertIn('["kick_chat_commands", "preset-kick-commands"]', self.script)


if __name__ == "__main__":
    unittest.main()
