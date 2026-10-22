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
            "settings-kicks-export", "settings-file", "settings-rates", "browser-mode",
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
        for name in ("settings-kicks-export", "settings-file", "settings-rates", "browser-mode"):
            self.assertEqual(where[name], "tab-settings")
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
            "Off unless turned on here. It saves as soon as it changes. This belongs to the open ledger, a new ledger copies it, and a streamer preset can set it.",
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
            "Each change saves on its own: a box or list right away, a typed value when you leave the field.",
            "Leave it blank and those amounts stay empty.",
            "Rumble’s terms do not allow a script to type or send chat.",
            "Kick coins convert at 100 coins = $1.",
            "API keys, secrets, and platform addresses are not included. Export uses the saved values.",
            "Sending to Rumble chat and the chats embedded beside the stream need the SuperBerry desktop app.",
            "Rates By Exchange Rate API",
            "Exchange rates have not been downloaded yet.",
            "SuperBerry · Licensed GPL-3.0-or-later",
        ):
            self.assertIn(needle, visible)
        for hidden in (
            "The CSV columns are Date, Time, Donor, Amount, Platform, Type, Currency, Amount USD, and Message.",
            "A newer file still applies the settings this version understands.",
            "Their open endpoint allows about one request an hour",
            "Quit, in the menu, stops SuperBerry on this computer.",
        ):
            self.assertNotIn(hidden, visible)

    def test_settings_bottom_keeps_status_rates_link_and_version_outside_help(self):
        settings = self.page[self.page.index('<dialog id="tab-settings"'):]
        settings = settings[:settings.index("</dialog>")]
        order = [settings.index(marker) for marker in (
            'id="help-settings-kicks-export"',
            'id="settings-export"',
            'id="help-settings-file"',
            'id="fx-status"',
            'id="fx-refresh"',
            'data-help="settings-rates"',
            '<section id="browser-mode" hidden>',
            'id="about-line"',
        )]
        self.assertEqual(order, sorted(order))
        rates_lead = settings[settings.rindex('<div class="help-lead">', 0, settings.index('data-help="settings-rates"')):]
        self.assertIn('<a href="https://www.exchangerate-api.com" target="_blank" rel="noopener noreferrer">', rates_lead[:rates_lead.index("</div>")])
        browser = settings[settings.index('<section id="browser-mode" hidden>'):]
        browser = browser[:browser.index("</section>\n    <p")]
        self.assertIn('data-help="browser-mode"', browser)
        self.assertIn('id="browser-notify-row"', browser)

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
            "Under $5 is a Super Sticker.",
            "$5 and above is a Super Chat.",
            "Export times use this computer’s time zone.",
            "The CSV columns are Date, Time, Donor, Amount, Platform, Type, Currency, Amount USD, and Message.",
            "This ledger's membership price, extra chat note, and top donors, plus the shared ledger page size.",
            "A newer file still applies the settings this version understands.",
            "Settings the file leaves out stay as they are.",
            "The saved response from open.er-api.com is reused until the last check is more than an hour old.",
            "Their open endpoint allows about one request an hour, and the table itself changes about once a day.",
            "Collection, the ledger, and exports work the same.",
            "Sign-in and chat buttons open the platform in a new browser window.",
            "Quit, in the menu, stops SuperBerry on this computer.",
        ):
            self.assertIn(sentence, text)


if __name__ == "__main__":
    unittest.main()
