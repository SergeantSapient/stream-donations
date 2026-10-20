"""YouTube chat emoji: standard emoji are text, custom emoji are pictures with a text fallback.

The run shapes below are copied from a public live chat captured on
2026-10-19 (Lofi Girl, get_live_chat). Names and ids are made up.
"""

import re
import unittest
from pathlib import Path

from streamdonations.chat_feed import ChatFeed, clean_chat_parts, clean_chat_text, emoji_image_ok
from streamdonations.commands import parse_command
from streamdonations.youtube_public import (
    chat_lines_from_live_chat_actions,
    donations_from_live_chat_actions,
    yt_parts,
    yt_text,
)

WAVE_SRC_24 = "https://yt3.ggpht.com/KOxdr_z3A5h1Gb7kqnxqOCnbZrBmxI2B_tRQ453BhTWUhYAlpg5ZP8IKEBkcvRoY8grY91Q=w24-h24-c-k-nd"
WAVE_SRC_48 = "https://yt3.ggpht.com/KOxdr_z3A5h1Gb7kqnxqOCnbZrBmxI2B_tRQ453BhTWUhYAlpg5ZP8IKEBkcvRoY8grY91Q=w48-h48-c-k-nd"


def _standard(char, *shortcuts):
    code = "-".join(f"{ord(c):x}" for c in char)
    return {"emoji": {
        "emojiId": char,
        "shortcuts": list(shortcuts),
        "searchTerms": [item.strip(":") for item in shortcuts],
        "image": {
            "thumbnails": [{"url": f"https://fonts.gstatic.com/s/e/notoemoji/15.1/{code}/72.png"}],
            "accessibility": {"accessibilityData": {"label": char}},
        },
    }}


def _custom(shortcut, thumbnails=None, label=None):
    return {"emoji": {
        "emojiId": "UCkszU2WH9gy1mb0dV-11UJg/G8AfY6yWGuKuhL0PlbiA2AE",
        "shortcuts": [shortcut] if shortcut else [],
        "searchTerms": [shortcut.strip(":")] if shortcut else [],
        "image": {
            "thumbnails": thumbnails if thumbnails is not None else [
                {"url": WAVE_SRC_24, "width": 24, "height": 24},
                {"url": WAVE_SRC_48, "width": 48, "height": 48},
            ],
            "accessibility": {"accessibilityData": {"label": label or (shortcut or "").strip(":")}},
        },
        "isCustomEmoji": True,
    }}


def _action(message_id, runs, author="Viewer One", paid=None):
    renderer = {
        "message": {"runs": runs},
        "authorName": {"simpleText": author},
        "authorExternalChannelId": "UC" + "v" * 22,
        "id": message_id,
        "timestampUsec": "1760925263000000",
    }
    if paid:
        renderer.update({
            "purchaseAmountText": {"simpleText": paid},
            "headerBackgroundColor": 4278239141,
        })
        kind = "liveChatPaidMessageRenderer"
    else:
        kind = "liveChatTextMessageRenderer"
    return {"addChatItemAction": {"item": {kind: renderer}, "clientId": "c-" + message_id}}


MIXED = _action("m1", [
    {"text": "hlw "},
    _standard("🎧", ":headphone:", ":headphones:"),
    {"text": " nice"},
    _custom(":hand-pink-waving:"),
    _custom(":hand-pink-waving:"),
])
EMOJI_ONLY = _action("m2", [_standard("👀", ":eyes:")])
CUSTOM_ONLY = _action("m3", [_custom(":face-purple-sweating:")])
ZWJ = _action("m4", [{"text": "Gas it out "}, _standard("🎅", ":santa:"), _standard("🧑\u200d🎄", ":mx_claus:")])


class ParseTests(unittest.TestCase):
    def test_standard_and_custom_emoji_become_text(self):
        line = chat_lines_from_live_chat_actions([MIXED])[0]
        self.assertEqual(line["text"], "hlw 🎧 nice :hand-pink-waving: :hand-pink-waving:")
        self.assertEqual(line["parts"], [
            {"text": "hlw 🎧 nice"},
            {"emoji": ":hand-pink-waving:", "src": WAVE_SRC_48},
            {"emoji": ":hand-pink-waving:", "src": WAVE_SRC_48},
        ])

    def test_an_emoji_only_line_is_kept(self):
        lines = chat_lines_from_live_chat_actions([EMOJI_ONLY, CUSTOM_ONLY])
        self.assertEqual([line["text"] for line in lines], ["👀", ":face-purple-sweating:"])
        self.assertNotIn("parts", lines[0])
        self.assertEqual(lines[1]["parts"], [{"emoji": ":face-purple-sweating:", "src": WAVE_SRC_48}])

    def test_joined_emoji_survive(self):
        line = chat_lines_from_live_chat_actions([ZWJ])[0]
        self.assertEqual(line["text"], "Gas it out 🎅🧑\u200d🎄")
        feed = ChatFeed()
        feed.add("youtube", [line])
        self.assertEqual(feed.since(0, 0, False)["lines"][0]["text"], "Gas it out 🎅🧑\u200d🎄")

    def test_keycaps_flags_and_labels(self):
        self.assertEqual(yt_text({"runs": [_standard("1\ufe0f\u20e3", ":keycap_1:")]}), "1\ufe0f\u20e3")
        self.assertEqual(yt_text({"runs": [_standard("🇯🇵", ":flag_jp:")]}), "🇯🇵")
        england = "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"
        self.assertEqual(clean_chat_text("go " + england, 50), "go " + england)
        self.assertEqual(yt_text({"runs": [_custom("", label="yt-wave")]}), ":yt-wave:")
        self.assertEqual(yt_text({"runs": [{"emoji": {"emojiId": "", "shortcuts": [":x:"]}}]}), ":x:")
        self.assertEqual(yt_text({"runs": [{"emoji": "nope"}, {"text": "ok"}]}), "ok")

    def test_text_only_lines_have_no_parts(self):
        line = chat_lines_from_live_chat_actions([_action("t", [{"text": "hello"}])])[0]
        self.assertEqual(line, {"key": "t", "author": "Viewer One", "text": "hello", "time": line["time"]})
        self.assertEqual(yt_parts({"simpleText": "hi"}), [])

    def test_unsafe_custom_emoji_images_are_text(self):
        for url in (
            "http://yt3.ggpht.com/a=w48",
            "https://evil.example/a.png",
            "https://yt3.ggpht.com.evil.example/a.png",
            "https://ggpht.com/a.png",
            "https://user:pw@yt3.ggpht.com/a.png",
            "https://yt3.ggpht.com:8443/a.png",
            'https://yt3.ggpht.com/a"onerror="x',
            "javascript:alert(1)",
        ):
            emoji = _custom(":bad:", thumbnails=[{"url": url, "width": 24, "height": 24}])
            line = chat_lines_from_live_chat_actions([_action("u", [{"text": "a"}, emoji])])[0]
            self.assertEqual(line["text"], "a :bad:", url)
            self.assertNotIn("parts", line, url)
        self.assertTrue(emoji_image_ok("https://yt4.ggpht.com/abc=s48"))
        self.assertFalse(emoji_image_ok("https://lh3.googleusercontent.com/abc=s48"))

    def test_commands_and_giveaway_still_see_words(self):
        line = chat_lines_from_live_chat_actions([_action("c", [{"text": "!top "}, _custom(":hand-pink-waving:")])])[0]
        self.assertEqual(parse_command(line["text"]), "top")
        word = chat_lines_from_live_chat_actions([_action("g", [_standard("🎉", ":tada:")])])[0]
        self.assertEqual(word["text"], "🎉")

    def test_super_chat_message_keeps_emoji(self):
        paid = _action("p1", [{"text": "love this "}, _standard("❤", ":red_heart:"), _custom(":hand-pink-waving:")],
                       paid="$5.00")
        donation = donations_from_live_chat_actions([paid])[0]
        self.assertEqual(donation.message, "love this ❤ :hand-pink-waving:")
        line = chat_lines_from_live_chat_actions([paid])[0]
        self.assertEqual(line["text"], "love this ❤ :hand-pink-waving:")


class FeedTests(unittest.TestCase):
    def test_feed_keeps_safe_parts_and_drops_bad_ones(self):
        feed = ChatFeed()
        feed.add("youtube", chat_lines_from_live_chat_actions([MIXED]))
        feed.add("youtube", [{
            "key": "x",
            "author": "A",
            "text": "hi :evil:",
            "parts": [{"text": "hi "}, {"emoji": ":evil:", "src": "https://evil.example/e.png"}],
        }])
        feed.add("kick", [{"key": "k", "author": "B", "text": "yo", "parts": [{"emoji": ":x:", "src": WAVE_SRC_24}]}])
        lines = {line["text"]: line for line in feed.since(0, 0, False)["lines"]}
        self.assertEqual(len(lines["hlw 🎧 nice :hand-pink-waving: :hand-pink-waving:"]["parts"]), 3)
        self.assertNotIn("parts", lines["hi :evil:"])
        self.assertEqual(lines["yo"]["parts"], [{"emoji": ":x:", "src": WAVE_SRC_24}])

    def test_parts_are_cleaned_and_capped(self):
        parts = clean_chat_parts([
            {"text": "  a\n\tb  "},
            {"emoji": ":w:", "src": WAVE_SRC_24},
            {"text": "x" * 600},
        ], 20)
        self.assertEqual(parts[0], {"text": "a b "})
        self.assertEqual(parts[1], {"emoji": ":w:", "src": WAVE_SRC_24})
        self.assertLessEqual(sum(len(p.get("text") or p.get("emoji")) for p in parts), 20)
        self.assertEqual(clean_chat_parts("nope", 20), [])
        self.assertEqual(clean_chat_parts([{"text": "only text"}], 20), [])


class PageTests(unittest.TestCase):
    def test_page_allows_only_emoji_image_hosts(self):
        from streamdonations.server import _SECURITY_HEADERS

        policy = dict(_SECURITY_HEADERS)["Content-Security-Policy"]
        image = re.search(r"img-src ([^;]+);", policy).group(1).split()
        self.assertEqual(image, ["'self'", "https://yt3.ggpht.com", "https://yt4.ggpht.com"])

    def test_chat_panel_builds_nodes_not_html(self):
        app = (Path(__file__).resolve().parents[1] / "streamdonations/web/app.js").read_text(encoding="utf-8")
        body = app.split("function fillWatchChatText(node, line) {", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn("innerHTML", body)
        self.assertIn("createTextNode", body)
        self.assertIn("chatEmojiSrc(part.src)", body)
        self.assertIn('image.alt = shortcut', body)


if __name__ == "__main__":
    unittest.main()
