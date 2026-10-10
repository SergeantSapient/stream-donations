# Changelog

The version is three numbers: major, considerable, and minor. A minor change raises the last number. A considerable change raises the middle number. A major change raises the first number and resets the others. A peripheral fix does not change the number; the note is added under the version that is current.

Wishlist work is considerable unless the request says it is major. Record a change with `python3 scripts/version.py`.

## 1.6.1 — 2026-10-09

- 2026-10-09 — Minor. Kiosk view sends the stream total and the top three from the button beside each copy button.

## 1.6.0 — 2026-10-09

- 2026-10-09 — Considerable. BLUE can read TOTAL and TIER on every line. TOTAL is the ledger total, and TIER is how many goals that total has reached.

## 1.5.2 — 2026-10-09

- 2026-10-09 — Peripheral. Rumble chat shows its window again. A hidden window left the page blank, so send could not find the button and the window could not be closed.

- 2026-10-09 — Peripheral. Rumble chat sends from a hidden window. Open Rumble chat still shows that window.

- 2026-10-09 — Peripheral. Rumble chat waits for a newly opened chat window to connect before pressing send, and tries once more if Rumble reports a problem submitting.

- 2026-10-09 — Peripheral. Rumble chat presses the send button on the row under the message box and does not press the rant, gift, or subscribe controls.

- 2026-10-09 — Peripheral. Rumble chat submits the message from the text box and does not press the rant control that opens the subscribe window.

- 2026-10-09 — Peripheral. Rumble chat presses the send button after the message is in the box, and it counts as sent only after that text has left.

- 2026-10-09 — Minor. BLUE ignores words that are not keywords, so a comparison can say amount is above. ON GOAL also carries PLATFORM and AMOUNT from the donation that reached the tier.

## 1.5.1 — 2026-10-09

- 2026-10-09 — Minor. BLUE can run ON GOAL when a tier is met, and once at the start of a script so tier 0 can run. DONOR is who reached that tier, and NAME is the name set on it.

## 1.5.0 — 2026-10-09

- 2026-10-09 — Considerable. BLUE scripts can be saved, attached to a streamer preset, and run while that ledger is connected.

## 1.4.0 — 2026-10-09

- 2026-10-09 — Considerable. Rumble combined chat can post as the signed-in user after Continue on the Rumble page. Rumble's terms do not allow a script to send chat, and that choice stays on this computer.

## 1.3.3 — 2026-10-09

- 2026-10-09 — Minor. Kiosk view hides the video bar until hover, adds playback beside reload, shows USD from earliest to latest, and follows the newest row. A Kick subscription celebration is a complimentary $0.00 Super Chat, and the chats run YouTube, Rumble, then Kick.

## 1.3.2 — 2026-10-08

- 2026-10-08 — Minor. The collection ledger sits beside the video, and the total and top three are one-line headers above it.

## 1.3.1 — 2026-10-08

- 2026-10-08 — Minor. The collection view video is smaller, and the margins around it are tighter, so the chats have more room.

## 1.3.0 — 2026-10-08

- 2026-10-08 — Considerable. Collection view shows the video, the total and top three with copy buttons, a short ledger, and the three chats.

## 1.2.1 — 2026-10-08

- 2026-10-08 — Minor. The stream source is a dropdown beside reload, and the pop-out chat buttons use the platform icons.

## 1.2.0 — 2026-10-08

- 2026-10-08 — Considerable. YouTube and Kick open their own pop-out chat after a website sign-in, the same way Rumble does.

## 1.1.0 — 2026-10-08

- 2026-10-08 — Considerable. YouTube and Kick chat can be sent from the combined chat as the signed-in account, and Rumble opens its own pop-out chat after a website sign-in.

## 1.0.0 — 2026-10-08

Starting point for numbered changes. This version is the local ledger for YouTube, Rumble, and Kick, including donor names, ledger page size, the dialog UI, the manual-entry duplicate warning, a read-only JSONL archive, and in-line edit of a manual row. Fixes before this point did not change the number.
