"""Writes blue_fixture.json: blue.py's grammar and how blue.py splits some lines.

The Node test checks the editor's tokenizer against it, and test_blue_editor.py
checks that it is still what blue.py says. Run after changing BLUE's words:

    python3 source/collector/tests/js/make_blue_fixture.py
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from streamdonations.blue import _tokenize, grammar  # noqa: E402

LINES = [
    'ON DONATION IF AMOUNT IS ABOVE 49.89 THEN SEND CHAT TO PLATFORM "Thank you {MENTION}!"',
    'on donation if raw amount % 100 equals 0 and currency is jpy then send chat to youtube and kick "{RAW_AMOUNT} {totalline}"',
    "ON TIMER IF TIME MODULO 15 EQUALS 0 AND TIME IS ABOVE 9:30 pm THEN SEND NOTICE TO APP \"It is {TIME}\"",
    'ON TIMER IF MINUTES MOD - 5 EQUALS -1 THEN SEND CHAT TO ALL AND NOT RUMBLE "x"',
    "ON GOAL IF NAME EQUALS 'First goal' THEN SEND CHAT TO PLATFORM \"Goal {TIER}: {NAME} by {DONOR}\"",
    'ON DONATION IF DONOR EQUALS Zoë-Ann_B OR TYPE IS Rant THEN SEND NOTICE TO SYSTEM "say \\"hi\\" {BOGUS}"',
    'ON DONATION IF TOTAL IS ABOVE $1,000.50 THEN SEND NOTICE TO LOG "50% off: {AMOUNT}"',
    "ON DONATION IF AMOUNT % THEN 12:05:30AM 7 # ; SEND",
]


def fixture() -> dict:
    lines = []
    for line in LINES:
        tokens = [[token.kind, token.text] for token in _tokenize(line)]
        lines.append({"line": line, "tokens": tokens})
    return {"grammar": grammar(), "lines": lines}


def text() -> str:
    return json.dumps(fixture(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    (HERE / "blue_fixture.json").write_text(text(), encoding="utf-8")
    print("wrote", HERE / "blue_fixture.json")
