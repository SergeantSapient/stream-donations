# TODO

Organic list. It will grow and shrink. This file is the shared focus for what to build next. Do not treat it as a commitment to start every item in one pass.

Current app version is `1.10.0` (`source/collector/streamdonations/__init__.py` and `source/electron/package.json`). Change it with `python3 scripts/version.py`. A peripheral fix does not raise the number. Wishlist work raises the middle number unless the request says it is major.

The 2026-09-30 resume reminder has fired. Do not put that timer back.

## Done

1. **Donor names.** Names are shown without `@` in the ledger, the top-donors list, thank-yous, notifications, and exports. Copying the top-donors line, or a thank-you under that section, puts `@` back on each username. Placeholders stay bare. The search prefix `@donor` still works.

2. **Ledger page size.** Settings chooses 10, 25, 50, or 100, or a custom whole number from 1 to 500. One size is shared by every ledger. Unset or invalid stays 100. Saving returns to page 1. The settings export includes it.

3. **UI cleanup.** The ledger follows the Live Till look. YouTube, Rumble, Kick, Streamers, and Settings are dialogs, not pages. The header has Export (CSV and JSONL for the open ledger), Log, and the menu. Reset ledger is in that menu. The long ledger note is behind a help icon, which can grow more sections later.

4. **Duplicate warning for manual entry.** Adding a gift that matches one already on the open ledger asks first. A match is the same donor, platform, type, amount, currency, and message, whether that row was typed or collected. The user can save the second copy. Collection from a platform does not ask. An edit that would match a different gift asks the same question.

5. **Read-only import.** Open archive, in the header menu, reads a JSONL export, the same file Export already writes, one donation per line, and shows it. The open ledger is not changed and the file is not written back. CSV is a spreadsheet view, so it cannot be opened as a ledger. A settings file is not a ledger.

6. **In-line edit.** Hover a manual row and Edit appears beside Delete. The manual-entry form fills in, + becomes Save, and Clear becomes Cancel. Donor, platform, type, amount, currency, message, and time can change. An empty time keeps the stored one. The same id stays a manual row. An automatic row cannot be edited. Saving a row unchanged does not ask about a duplicate. An edit that matches a different gift on this ledger asks first.

7. **Version classification.** A shipped change is recorded with `python3 scripts/version.py`. The number is major.considerable.minor. Minor raises the last number, considerable raises the middle number, and major raises the first. A peripheral fix does not change the number. Wishlist work is considerable unless the request says it is major. The collector and Electron keep the same number. See Version rules.

8. **BLUE scripts.** Berry Lexical User Events. BLUE in the menu saves, imports, and exports a script. A streamer preset can select one. The script runs while a ledger that uses it is connected, and a soft disconnect stops it. Connecting that ledger starts it over. Each line is its own command. A line that cannot be read is skipped. An action that cannot be sent is written to the log. ON GOAL runs when the script starts, including tier 0, and again when a goal tier from 1 to 5 is reached. DONOR is who reached that tier, NAME is the name set on it, and PLATFORM and AMOUNT are that donation. Those are empty at tier 0. TOTAL is the ledger total on every line. TIER is how many goals that total has reached, also on every line. On a goal line, TIER is the tier that line is about. A word that is not a keyword, such as is, is ignored. TIME MODULO n counts minutes since local midnight, and MOD and % mean MODULO. ON TIMER lines with TIME run once per new clock minute; the other ON TIMER lines run once per completed minute since the connect. A value that cannot work for its field is reported and the line is skipped.

9. **Chat commands.** `!total` and `!top`, per ledger and per platform, off by default. The first word only, any case. One 2-minute cooldown per chat, shared by both commands and every ledger reading that chat, in memory, started when a reply is attempted. History, repeats, the signed-in account, and the app's own replies never answer. `!top` skips anonymous donors, has no `@`, and is off while top donors are hidden. YouTube and Kick post as the signed-in account (YouTube 21 units each); Rumble is typed by the desktop window and is not available in browser mode. See README, Chat commands. Not tried on a real live stream yet.

10. **BLUE PLATFORM, raw amount, and chat lines.** `SEND CHAT TO PLATFORM` answers in the chat the gift came from (on a goal, the gift that reached the tier; a syntax error on a timer). It skips manual and pasted entries, Other rows, a chat that cannot post right now, and a signed-in YouTube, Kick webhook, or Rumble owner-API gift that is not this ledger's chat, each logged once per script start. `RAW AMOUNT` and `{RAWAMOUNT}` are the amount as sent (Kicks as coins, ISO 4217 decimals), `CURRENCY` and `{CURRENCY}` the code (`KICKS` for Kicks), `{MENTION}` is `@name` or empty, and `{TOTALLINE}`/`{TOPLINE}` are the chat-command lines fitted to each chat. `ON DONATION` skips gifts over 2 minutes old or from before the script started. One thank-you per gift per line and chat in 2 minutes, and at most 6 BLUE chat lines per chat per minute. See README, BLUE. Not tried on a real live stream yet; signed-in YouTube Super Chats polled every 15 minutes are mostly past the 2-minute window, so the public reader is what thanks them.

## To do

1. **BLUE missed timer minutes.** Possible follow-up, not started. A minute the timer misses, because the app was busy or asleep for over a minute, is not run later. Replaying it could send old chat late, so it needs a limit and a decision first.

After that, the next work is the wishlist. Each wishlist item is considerable unless the request says it is a major update.

## Wishlist

Do not implement these without an understanding of the complications, and do not implement them until explicitly asked. Research only until then.

Each item is a **considerable** version change unless the request says it is major. If several are requested at once, they can share one bump. Gently remind that packing several wishlist items into one release is probably a bad idea.

1. **Chat send.** Done. YouTube and Kick messages are posted as the signed-in account from the combined chat. Toggles choose which of those chats, and Rumble after Continue on the Rumble page. Stream total, thank-you, and top-donor lines use those same toggles. A sign-in from before this change can still read and cannot post until Connect is used again. YouTube, Kick, and Rumble each open their own pop-out chat after a website sign-in on this computer. Those windows are separate from Connect. The password is not stored. SuperBerry does not type into the YouTube or Kick windows. Rumble combined chat types into Rumble's own chat box and presses the send button on the row under that box as the signed-in user. A chat window that just opened is given a moment to connect before that button is pressed, and a refused submit is tried once more. It does not press the rant, gift, or subscribe controls. It counts as sent only after the line leaves the box. Rumble's terms do not allow that, and Continue on the Rumble page is the choice to take that risk. The choice stays on this computer and is not part of the settings export.

2. **Database viewer and editor.** View and edit archived ledgers, and generate data points from them. Possibly deterministic algorithms that suggest what could be improved for donations based on trends.

3. **Cloud or remote export.** Export the ledger to cloud or other remote storage.

4. **Settings export without secrets.** Done. The Settings dialog exports and imports a versioned JSON file: membership price, the extra chat note, top donors, and the shared ledger page size. Goal tiers are not in that file. They live on a streamer preset and are written onto a ledger only when that preset is saved with the ledger's addresses. API keys and secrets are not in the file. A newer file still applies the settings this version understands, and settings the file leaves out stay as they are. A full JSON ledger export is still not part of this.

5. **PWA instead of Electron.** Convert to a progressive web app. Account for what a browser will limit, including system resources. Browser mode (1.8.0) is a step toward it: the collector stays on this computer and a normal browser is the window. Rumble chat sending and embedded chats still need Electron. A collector-free PWA is not started.

6. **Update in place.** Check a list of repos for a newer version and update only after the user allows it. Tell them with a non-disruptive notification. Needs a reliable repo (most likely GitHub, if that stays inside its terms of service).

7. **Other platform binaries.** Windows and macOS first, then ARM and Android. Windows started in 1.7.0: GitHub Actions builds a portable x64 zip (see README, Windows). Battery is hidden there and file modes are not enforced. It has not been run on a real Windows machine yet. macOS, ARM, and Android are not started. Mobile is the less familiar target. Before a port, say which features survive cleanly and which have to be cut or compromised.

8. **Headless mode.** A terminate-and-stay-resident, daemon, or headless option, perhaps a CLI flag. No UI. It keeps recording donations. The CLI should have a watch mode that shows the log as rows are added, plus a total, the last donation, and health.

9. **Local endpoint for other apps.** A way to watch the ledger from a browser window, or to connect another application, or to use an OS communication protocol (that last path may be faster and harder to diagnose). The intended later shape is a plugin for OBS and similar tools. Not near that yet. Lay the bones only when this item is actually requested.

## Version rules

- Wishlist items are considerable unless the request says the update is major.
- To-do items are classified when they are done: minor, considerable, or major.
- A peripheral fix is a changelog note under the current version. It does not bump the number.
- One requested batch of wishlist work may share a single bump. Say so, and say that combining them is probably a bad idea, before doing it.
