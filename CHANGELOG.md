# Changelog

The version is three numbers: major, considerable, and minor. A minor change raises the last number. A considerable change raises the middle number. A major change raises the first number and resets the others. A peripheral fix does not change the number; the note is added under the version that is current.

Wishlist work is considerable unless the request says it is major. Record a change with `python3 scripts/version.py`.

## 1.11.1 — 2026-10-20

- 2026-10-20 — Minor. Long explanations in the YouTube, Rumble, Kick, Streamers, Giveaway, Settings, and BLUE dialogs are behind Help buttons, closed by default, as short sections and lists. What you need to know to act stays visible as one or two lines: the Google test-user and quota note, the cost and sign-in account of a !total or !top reply, the Rumble terms risk, reconnecting Kick so it can post, saving Settings before changing ledgers, and leaving the membership price blank. The !total and !top explanation is written once and shown in all three platform dialogs. Each Help panel remembers on this computer whether it was left open. No text was dropped.

## 1.11.0 — 2026-10-20

- 2026-10-20 — Considerable. The BLUE editor colors each word the way BLUE reads it: events and IF, THEN, AND, OR, NOT; fields; EQUALS, ABOVE, BELOW, IS, MODULO, MOD, and %; numbers and clock times; chat and notice targets in their platform colors; the message; and placeholders, with one BLUE does not fill in shown in red. The words come from BLUE itself, so the colors cannot drift from what runs. The editor has line numbers and does not wrap. While you type, a line that will be skipped gets a red mark in the margin with the reason on hover, a red underline, and the reason below the editor. Typing, undo, paste, input methods, import, and export work as before. New Test a line box, a dry run: type one line, or test the whole script, pick a donation, timer minute, or goal, and the ledger to test on, with its total and tier filled in from that ledger unless you type others. It shows whether the line matches and each condition with the value it saw, every chat after PLATFORM and ALL are worked out with the final cut-down message, and which guard would stop it: the 2-minute age guard, the 2-minute repeat thanks, 6 chat lines a minute, and for PLATFORM a chat that cannot be posted to or an Other-platform gift. A test gift has no real source, so the result says what a real gift from that platform would need. Nothing is sent, queued, logged, or saved, and the guards are only read. The long BLUE explanation under the editor is now behind a Help button beside the Script label, closed by default, in short sections: events, conditions and fields, MODULO and TIME, targets, placeholders, limits and guards, editor colors, and the test box. Whether it was left open is remembered on this computer.

## 1.10.1 — 2026-10-21

- 2026-10-21 — Minor. Kick public chat reconnects after a network change. A Kick chat socket that went silent without closing, such as after switching networks, was read forever while the status said connected, and only a soft disconnect and reconnect brought it back; YouTube and Rumble were not affected because each read has its own timeout. The app now sends a Pusher ping after Kick's activity_timeout and drops the socket 30 seconds after an unanswered ping, drops a Centrifugo socket 10 seconds after a missed server ping, and drops any socket silent for 150 seconds, then reconnects. Retries wait about 2 seconds, doubling to 60 with jitter, and reset after a connection stays up 30 seconds. TCP keepalive is on for that socket on Windows, Linux, and macOS. A frame cut across a read timeout no longer throws the next read out of step. The status says "Kick chat stopped responding; reconnecting." while it is quiet, the live check no longer reports ok over a quiet socket, and the webhook status shows it too.

## 1.10.0 — 2026-10-20

- 2026-10-20 — Considerable. BLUE can thank a gift in the chat it came from. SEND CHAT TO PLATFORM sends to the platform of that donation, or on ON GOAL the donation that reached the tier; it combines with AND and NOT, is a syntax error on ON TIMER, and skips manual and pasted entries, Other-platform rows, a chat that cannot be posted to right now, and a signed-in YouTube, Kick webhook, or Rumble owner-API gift that is not this ledger's chat (each logged once per script start). New RAW AMOUNT (RAWAMOUNT, RAW_AMOUNT) is the amount as sent in its own currency, with Kicks as the coin count, and {RAWAMOUNT} prints it with ISO 4217 decimals. New CURRENCY condition (CURRENCY IS JPY; KICKS for Kicks) and {CURRENCY}. {MENTION} is @name, empty for anonymous. {TOTALLINE} and {TOPLINE} are the !total and !top lines, fitted to each chat; {TOPLINE} is empty while top donors are hidden. Placeholders ignore underscores. IS with one value at the end of a comparison means EQUALS, and it works with MODULO, MOD, %, and a minus sign, such as minutes mod 20 is 0. RAW AMOUNT takes MODULO; CURRENCY does not. BLUE chat lines are cut to 200 characters on YouTube and Rumble and 500 on Kick, the same gift is thanked once per line and chat in 2 minutes, and at most 6 BLUE chat lines go to one chat per minute. Those two limits are saved with the ledger until they expire, so a restart does not thank the same gift again or allow another burst; they are not in the settings export. Behaviour change: ON DONATION no longer runs for a gift whose platform time is more than 2 minutes old or from before the script started, so a signed-in YouTube backfill or recent history after a reconnect no longer floods chat; those rows are still stored and counted.

## 1.9.2 — 2026-10-20

- 2026-10-20 — Minor. BLUE TIME MODULO counts minutes since local midnight, so time modulo 20 equals 1 is true at 00:01, 00:21, and so on. MOD and % mean MODULO. ON TIMER lines that use TIME now run once for each new clock minute, about a second after it starts, so a clock check lands every time even when the connect was mid-minute. Other ON TIMER lines still run once per completed minute since the connect. A value that cannot work is reported in the editor and the log and that line is skipped: a TIME that is not a clock time, a clock time after TIME MODULO, a word where a number is needed, a modulus of zero or below, and a modulo check that can never be true. The last clock minute is saved with the ledger, so a restart does not run it again. A % anywhere else is still ignored, and a - before a number, even with a space, makes it negative.

## 1.9.1 — 2026-10-19

- 2026-10-19 — Minor. YouTube chat shows emoji. Standard emoji in YouTube chat were dropped, and a line that was only emoji did not show at all; they now appear as the emoji. Channel and member emoji show as YouTube's picture in the chat panel and kiosk, with the shortcut such as :hand-pink-waving: as hover text and as the text in the ledger, chat commands, the giveaway, and copies. Emoji joined with a zero-width joiner and subdivision flags stay whole on every platform.

## 1.9.0 — 2026-10-18

- 2026-10-18 — Considerable. Chat commands. With Answer !total and !top turned on for a platform in a ledger, a viewer who starts a chat line with !total gets that ledger's stream total line, and !top gets its top three named donors, posted back into the same chat. One reply per chat every 2 minutes, shared by both commands and by every ledger reading that chat; other commands in that time are ignored. History, repeats, the signed-in account's own lines, and the app's own replies never answer. Anonymous donors are skipped and nobody is pinged. Replies fit 200 characters on YouTube and Rumble and 500 on Kick. YouTube and Kick post as the signed-in account; Rumble is typed by the desktop window and is not available in browser mode. Every switch is off by default.

## 1.8.5 — 2026-10-18

- 2026-10-18 — Minor. Signed-in YouTube calls are counted at Google's published quota cost. Reading the live chat is 5 units, not 1. That read now waits at least 30 seconds, or longer when YouTube asks, and takes up to 2,000 messages at a time. One live broadcast uses about 634 units an hour instead of about 3,600, so the 10,000 daily quota lasts about 15 hours of streaming instead of under 3. The pause at the quota limit is unchanged.

## 1.8.4 — 2026-10-17

- 2026-10-17 — Peripheral. A saved ledger stays one row when its donations fall on more than one day. The arrow opens those dates, and each date opens that day's donations.

- 2026-10-17 — Minor. Open archive shows the ledgers saved in archive.db. Each row is a stream name and a date, and the arrow lists that day's donations. Nothing in that dialog can be edited.

## 1.8.3 — 2026-10-17

- 2026-10-17 — Minor. CSV and JSONL downloads are named with the oldest donation's date, then the ledger name.

## 1.8.2 — 2026-10-17

- 2026-10-17 — Minor. Closing a ledger that has donations asks whether to save it to the archive first. An empty ledger does not ask.

## 1.8.1 — 2026-10-17

- 2026-10-17 — Peripheral. The manual entry dialog's close button sits in from the corner.

- 2026-10-17 — Minor. Kiosk view has a message box under the chats. Enter sends that message to the same chats as the combined chat, and Manual entry on that bar opens the manual entry form.

## 1.8.0 — 2026-10-17

- 2026-10-17 — Considerable. Browser mode. serve --open, or SuperBerry --browser on Linux, runs the same collector and ledger with a normal web browser as the window. Quit is in the menu there, sign-ins and pop-out chats open in the browser, and browser notifications are offered where the computer has none. The page and API accept changes only from the SuperBerry page itself, so a web page on another local port cannot change the ledger. The desktop app, the AppImage, and the Windows exe and zip are unchanged.

## 1.7.0 — 2026-10-16

- 2026-10-16 — Peripheral. A self-contained SuperBerry.exe unpacks on its first launch and starts from that copy afterward.

- 2026-10-16 — Peripheral. Closing SuperBerry closes every window it opened, and the main window stays on the app instead of a raw sign-in page.

- 2026-10-16 — Peripheral. The Windows package includes a certificate bundle, so a new Windows machine can reach YouTube, Kick, and Rumble.

- 2026-10-16 — Considerable. Windows build. A portable Windows x64 zip is built by GitHub Actions with Electron, a private Python, and the collector. Kick signatures are checked without OpenSSL, archive paths with odd characters open read-only, the collector closes its database when the app quits, and notifications use the system on Windows. Battery stays hidden off Linux.

## 1.6.10 — 2026-10-16

- 2026-10-16 — Peripheral. Closing the last ledger opens a new blank one, and that blank ledger does not ask for addresses.

- 2026-10-16 — Minor. Archive ledger, under Export, saves the open ledger to archive.db and closes it.

## 1.6.9 — 2026-10-11

- 2026-10-16 — Peripheral. A pasted Kick coin gift counts when the copy is a name, the word sent, and the coin count.

- 2026-10-16 — Peripheral. Public Kick chat is collected again, including subs, Kicks, and the omnichat reader.

- 2026-10-11 — Peripheral. Double-clicking a ledger tab opens the name dialog again.

- 2026-10-11 — Minor. The ledger name dialog can use the title of a stream it can see.

## 1.6.8 — 2026-10-11

- 2026-10-11 — Minor. A kiosk chat with no address on the open ledger collapses on its own.

## 1.6.7 — 2026-10-11

- 2026-10-11 — Minor. A collapsed kiosk chat shows a bubble when a new message arrives.

## 1.6.6 — 2026-10-11

- 2026-10-11 — Minor. Kiosk chat text has a button that returns it to the normal size.

## 1.6.5 — 2026-10-11

- 2026-10-11 — Minor. Kiosk chats reload, resize their text, and collapse to a strip.

## 1.6.4 — 2026-10-11

- 2026-10-11 — Peripheral. Kick chat stays on the channel it was opened for when that channel hosts another.

- 2026-10-11 — Minor. Ledger tabs keep Addresses and the plug in a menu. Always show puts those buttons back on the tab.

## 1.6.3 — 2026-10-10

- 2026-10-10 — Minor. Streamers has Apply to open ledger beside Save. It saves the preset, then writes its channels, goals, and BLUE script onto the open ledger the same way the ledger's addresses do. Save alone still leaves the open ledger as it is.

## 1.6.2 — 2026-10-10

- 2026-10-10 — Minor. Kiosk view shows the computer battery, as an icon and a percent, above the header buttons while that computer is running on battery.

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
