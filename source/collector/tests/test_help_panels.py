"""Long explanations sit behind Help buttons, closed by default, with nothing lost."""

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "streamdonations" / "web"


class _Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.buttons = []
        self.panels = {}
        self.ids = []
        self.includes = []
        self.dialog = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "dialog":
            self.dialog = attrs.get("id", "")
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "button" and "data-help" in attrs:
            self.buttons.append((self.dialog, attrs))
        if tag == "div" and "help-block" in (attrs.get("class") or "").split():
            self.panels[attrs.get("id")] = attrs
        if "data-help-include" in attrs:
            self.includes.append((self.dialog, attrs["data-help-include"]))

    def handle_endtag(self, tag):
        if tag == "dialog":
            self.dialog = ""


def _text(fragment):
    return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())


class HelpPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (WEB / "index.html").read_text(encoding="utf-8")
        cls.parsed = _Collect()
        cls.parsed.feed(cls.page)

    def test_every_help_button_controls_a_closed_panel(self):
        names = [attrs["data-help"] for _dialog, attrs in self.parsed.buttons]
        self.assertEqual(sorted(names), sorted([
            "blue", "youtube-signin", "youtube-commands", "rumble-commands", "kick-commands",
            "kick-channel", "streamers", "giveaway", "settings-ledger", "membership-price",
        ]))
        self.assertEqual(len(names), len(set(names)))
        for _dialog, attrs in self.parsed.buttons:
            self.assertEqual(attrs.get("type"), "button")
            self.assertEqual(attrs.get("aria-expanded"), "false")
            panel = self.parsed.panels.get(attrs.get("aria-controls"))
            self.assertIsNotNone(panel, attrs)
            self.assertIn("hidden", panel)
            self.assertEqual(panel.get("role"), "region")
            self.assertTrue(panel.get("aria-label") or panel.get("aria-labelledby"))
        self.assertEqual(len(self.parsed.ids), len(set(self.parsed.ids)), "ids must be unique")
        self.assertNotIn("style=", self.page)

    def test_buttons_sit_in_their_dialogs(self):
        where = {attrs["data-help"]: dialog for dialog, attrs in self.parsed.buttons}
        self.assertEqual(where["youtube-signin"], "tab-youtube")
        self.assertEqual(where["youtube-commands"], "tab-youtube")
        self.assertEqual(where["rumble-commands"], "tab-rumble")
        self.assertEqual(where["kick-commands"], "tab-kick")
        self.assertEqual(where["kick-channel"], "tab-kick")
        self.assertEqual(where["streamers"], "tab-streamers")
        self.assertEqual(where["giveaway"], "tab-giveaway")
        self.assertEqual(where["settings-ledger"], "tab-settings")
        self.assertEqual(where["membership-price"], "tab-settings")
        self.assertEqual(where["blue"], "tab-blue")

    def test_chat_commands_text_is_written_once_and_used_in_three_dialogs(self):
        self.assertEqual(sorted(self.parsed.includes), [
            ("tab-kick", "chat-commands"), ("tab-rumble", "chat-commands"), ("tab-youtube", "chat-commands"),
        ])
        self.assertEqual(self.page.count('<template id="help-text-chat-commands">'), 1)
        self.assertEqual(self.page.count("A viewer who types !total or !top as the first word"), 1)
        template = self.page[self.page.index('<template id="help-text-chat-commands">'):]
        template = template[template.index(">") + 1:template.index("</template>")]
        self.assertNotIn(" id=", template)
        for sentence in (
            "One reply per chat every 2 minutes, and other commands in that time are ignored.",
            "Anonymous donors are left out of !top, names have no @, and !top is ignored while top donors are hidden.",
            "Off unless turned on here. This belongs to the open ledger.",
        ):
            self.assertIn(sentence, template)
        script = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('document.querySelectorAll("[data-help-include]")', script)
        self.assertIn('document.querySelectorAll("button[data-help]")', script)
        self.assertIn('const HELP_KEY_PREFIX = "stream-donations-help-";', script)

    def test_what_you_must_know_to_act_stays_visible(self):
        visible = _text(re.sub(r'<div id="(help-[\w-]+|blue-help)" class="help-block".*?\n( *)</div>\n', "",
                               self.page, flags=re.S))
        visible = re.sub(r"<template.*?</template>", "", visible, flags=re.S)
        for needle in (
            "Google shows “Access blocked”",
            "use 21 units of the Data API quota each",
            "with the same terms risk. Browser mode cannot answer on Rumble.",
            "Replies post as the signed-in Kick account.",
            "Click Connect again after this update so the same account can post into that channel’s chat.",
            "Nothing is typed into chat. People enter by sending the keyword themselves.",
            "Save before changing ledgers, or unsaved edits here are dropped.",
            "Leave it blank and those amounts stay empty.",
            "Rumble’s terms do not allow a script to type or send chat.",
        ):
            self.assertIn(needle, visible)

    def test_key_text_is_still_in_the_page(self):
        text = _text(self.page)
        for sentence in (
            "Posting one message costs 20.",
            "The day resets at midnight Pacific Time.",
            "Google Auth Platform → Audience → Test users",
            "Renewals still need the owner webhooks below.",
            "Apply to open ledger saves the preset, then writes its channels, goals, and BLUE script onto the open ledger",
            "Closing this prompt does not stop a drawing that is already running.",
            "API keys, exchange rates, and ledger page size stay shared.",
            "so five Rumble subs are $24.99 when this price is $4.99.",
            "Soft disconnect stops it, and connecting that ledger starts it over, including the minute timer.",
        ):
            self.assertIn(sentence, text)


if __name__ == "__main__":
    unittest.main()
