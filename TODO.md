# TODO

Organic list. It will grow and shrink. This file is the shared focus for what to build next. Do not treat it as a commitment to start every item in one pass.

Current app version is `1.0.0` (`source/collector/streamdonations/__init__.py` and `source/electron/package.json`). Wishlist item 4 (settings file) is in the app. The version number was not raised. Nothing else on this list has been started.

## Reminder

Desktop notification is set for **2026-09-30 22:00 America/New_York** (10:00 PM Eastern, which is EDT that day). After that time, start the to-do list below. Wishlist items stay blocked until explicitly requested.

The timer is the user unit `stream-donations-resume.timer`. If the machine is asleep or the session is logged out at that minute, it fires on the next login instead. Cancel with `systemctl --user disable --now stream-donations-resume.timer`.

## To do

Implement these as soon as possible. They are not wishlist items.

1. **Donor names.** Correct how donor names are presented in the app and in exports. `@` is only for "Thank You" and "Top Donor", because those strings are pasted or injected into chat. Everywhere else, show the name without `@`.

2. **Ledger page size.** Add a setting for page size: 10, 25, 50, or 100. A custom size is worth considering.

3. **UI cleanup.** Bring the look as close as practical to the current Live Till app, and keep the newer features integrated cleanly. Settings should not be a separate page. Use a virtual window or dialogue for adjustments.

4. **Read-only import.** Open an archived ledger for viewing only. Do not write back into it.

5. **In-line edit and delete.** Edit or delete a donation from the ledger row. The duplicate warning stays for manual entry, checked against other manual rows and against automated rows, and the user can dismiss it. Automated entry ignores that warning.

6. **Automated version control.** Let the LLM decide whether a change is minor, considerable, or major. A bug fix inside a small window, before any reasonable deployment can happen, does not tick the version. Note those in the changelog as peripheral fixes.

## Wishlist

Do not implement these without an understanding of the complications, and do not implement them until the project manager explicitly asks. Research only until then.

Each item below is a **major** version change on its own. If several are requested at once, they can ship as one major bump. Gently remind the project manager that packing multiple majors into one release is probably a bad idea.

1. **Chat injection.** Option to inject "Stream Total", "Thank You", and "Top Donor" blurbs into chat. Session handling per platform will likely mean simulating a logged-in user. That session material must not be hard-coded into the app.

2. **Database viewer and editor.** View and edit archived ledgers, and generate data points from them. Possibly deterministic algorithms that suggest what could be improved for donations based on trends.

3. **Cloud or remote export.** Export the ledger to cloud or other remote storage.

4. **Settings export without secrets.** Done. The Settings tab exports and imports a versioned JSON file of that tab only (membership price, goal tiers, the extra chat note, and top donors). API keys and secrets are not in the file. A newer file still applies the settings this version understands, and settings the file leaves out stay as they are. A full JSON ledger export is still not part of this.

5. **PWA instead of Electron.** Convert to a progressive web app. Account for what a browser will limit, including system resources.

6. **Update in place.** Check a list of repos for a newer version and update only after the user allows it. Tell them with a non-disruptive notification. Needs a reliable repo (most likely GitHub, if that stays inside its terms of service).

7. **Other platform binaries.** Windows and macOS first, then ARM and Android. Mobile is the less familiar target. Before a port, say which features survive cleanly and which have to be cut or compromised.

8. **Headless mode.** A terminate-and-stay-resident, daemon, or headless option, perhaps a CLI flag. No UI. It keeps recording donations. The CLI should have a watch mode that shows the log as rows are added, plus a total, the last donation, and health.

9. **Local endpoint for other apps.** A way to watch the ledger from a browser window, or to connect another application, or to use an OS communication protocol (that last path may be faster and harder to diagnose). The intended later shape is a plugin for OBS and similar tools. Not near that yet. Lay the bones only when this item is actually requested.

## Version rules

- Wishlist items are major. To-do items are classified when they are done, using the rule in to-do item 6.
- Peripheral fixes in the pre-deployment window are changelog notes only. They do not bump the version.
- One requested batch of wishlist work may share a single major bump. Say so, and say that combining them is probably a bad idea, before doing it.
