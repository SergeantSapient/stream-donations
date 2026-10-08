# TODO

Organic list. It will grow and shrink. This file is the shared focus for what to build next. Do not treat it as a commitment to start every item in one pass.

Current app version is `1.0.0` (`source/collector/streamdonations/__init__.py` and `source/electron/package.json`). Change it with `python3 scripts/version.py`. A peripheral fix does not raise the number. Wishlist work raises the middle number unless the request says it is major.

The 2026-09-30 resume reminder has fired. Do not put that timer back.

## Done

1. **Donor names.** Names are shown without `@` in the ledger, the top-donors list, thank-yous, notifications, and exports. Copying the top-donors line, or a thank-you under that section, puts `@` back on each username. Placeholders stay bare. The search prefix `@donor` still works.

2. **Ledger page size.** Settings chooses 10, 25, 50, or 100, or a custom whole number from 1 to 500. One size is shared by every ledger. Unset or invalid stays 100. Saving returns to page 1. The settings export includes it.

3. **UI cleanup.** The ledger follows the Live Till look. YouTube, Rumble, Kick, Streamers, and Settings are dialogs, not pages. The header has Export (CSV and JSONL for the open ledger), Log, and the menu. Reset ledger is in that menu. The long ledger note is behind a help icon, which can grow more sections later.

4. **Duplicate warning for manual entry.** Adding a gift that matches one already on the open ledger asks first. A match is the same donor, platform, type, amount, currency, and message, whether that row was typed or collected. The user can save the second copy. Collection from a platform does not ask. An edit that would match a different gift asks the same question.

5. **Read-only import.** Open archive, in the header menu, reads a JSONL export, the same file Export already writes, one donation per line, and shows it. The open ledger is not changed and the file is not written back. CSV is a spreadsheet view, so it cannot be opened as a ledger. A settings file is not a ledger.

6. **In-line edit.** Hover a manual row and Edit appears beside Delete. The manual-entry form fills in, + becomes Save, and Clear becomes Cancel. Donor, platform, type, amount, currency, message, and time can change. An empty time keeps the stored one. The same id stays a manual row. An automatic row cannot be edited. Saving a row unchanged does not ask about a duplicate. An edit that matches a different gift on this ledger asks first.

7. **Version classification.** A shipped change is recorded with `python3 scripts/version.py`. The number is major.considerable.minor. Minor raises the last number, considerable raises the middle number, and major raises the first. A peripheral fix does not change the number. Wishlist work is considerable unless the request says it is major. The collector and Electron keep the same number. See Version rules.

## To do

Nothing is queued. The next work is the wishlist. Each wishlist item is considerable unless the request says it is a major update.

## Wishlist

Do not implement these without an understanding of the complications, and do not implement them until explicitly asked. Research only until then.

Each item is a **considerable** version change unless the request says it is major. If several are requested at once, they can share one bump. Gently remind that packing several wishlist items into one release is probably a bad idea.

1. **Chat injection.** Option to inject "Stream Total", "Thank You", and "Top Donor" blurbs into chat. Session handling per platform will likely mean simulating a logged-in user. That session material must not be hard-coded into the app.

2. **Database viewer and editor.** View and edit archived ledgers, and generate data points from them. Possibly deterministic algorithms that suggest what could be improved for donations based on trends.

3. **Cloud or remote export.** Export the ledger to cloud or other remote storage.

4. **Settings export without secrets.** Done. The Settings dialog exports and imports a versioned JSON file: membership price, the extra chat note, top donors, and the shared ledger page size. Goal tiers are not in that file. They live on a streamer preset and are written onto a ledger only when that preset is saved with the ledger's addresses. API keys and secrets are not in the file. A newer file still applies the settings this version understands, and settings the file leaves out stay as they are. A full JSON ledger export is still not part of this.

5. **PWA instead of Electron.** Convert to a progressive web app. Account for what a browser will limit, including system resources.

6. **Update in place.** Check a list of repos for a newer version and update only after the user allows it. Tell them with a non-disruptive notification. Needs a reliable repo (most likely GitHub, if that stays inside its terms of service).

7. **Other platform binaries.** Windows and macOS first, then ARM and Android. Mobile is the less familiar target. Before a port, say which features survive cleanly and which have to be cut or compromised.

8. **Headless mode.** A terminate-and-stay-resident, daemon, or headless option, perhaps a CLI flag. No UI. It keeps recording donations. The CLI should have a watch mode that shows the log as rows are added, plus a total, the last donation, and health.

9. **Local endpoint for other apps.** A way to watch the ledger from a browser window, or to connect another application, or to use an OS communication protocol (that last path may be faster and harder to diagnose). The intended later shape is a plugin for OBS and similar tools. Not near that yet. Lay the bones only when this item is actually requested.

## Version rules

- Wishlist items are considerable unless the request says the update is major.
- To-do items are classified when they are done: minor, considerable, or major.
- A peripheral fix is a changelog note under the current version. It does not bump the number.
- One requested batch of wishlist work may share a single bump. Say so, and say that combining them is probably a bad idea, before doing it.
