# SuperBerry

SuperBerry is a local Electron app that records donation events from your own YouTube, Rumble, and Kick channels and keeps them in a SQLite ledger you can filter, annotate, and export. The project directory and repository stay `stream-donations`.

The window is only a front end. A Python collector talks to each service and writes the ledger in the home directory (`~/.local/share/stream-donations/donations.db` on Linux). The application folder holds no API keys, tokens, or database.

The published version is `1.0.0`. Record a shipped change with `python3 scripts/version.py considerable "What changed"`. The classes are `minor`, `considerable`, `major`, and `peripheral`. Wishlist work uses `considerable` unless the request says it is major. `peripheral` writes a changelog note and does not change the number. The collector version and the Electron version are the same number.

## Build

From this directory:

```sh
scripts/build.sh
```

The script uses what is already on this machine:

- `python3`, or `pyenv` when a Python is selected, to run the tests
- `pip` to install the collector into `.venv`
- `podman` to build the Node image in `Containerfile`
- `distrobox` box `stream-donations`, made from that image, to assemble `bin/` and install Electron

Re-enter the box with `distrobox enter stream-donations`. Remove it with `distrobox rm stream-donations` if you want the next build to create it again.

Nothing is installed with rpm-ostree, and the app does not ask for root. After a successful build, start it with:

```sh
bin/stream-donations
```

A copy you can move to another Linux machine, with Electron and Python beside the launcher, is built with `scripts/package-linux.sh`. That writes `dist/SuperBerry-linux-x64/` and, when the AppImage tool is available, `dist/SuperBerry-x86_64.AppImage`. The program in that folder is `SuperBerry`. macOS is not built yet.

### Windows

GitHub Actions builds the Windows copy on every push and pull request (workflow `Windows build`). Open the run under the repository's Actions tab, download the `SuperBerry-windows-x64` artifact, and unzip it. GitHub wraps the artifact in its own zip, so unzip twice. `SuperBerry.exe` at the root of that zip is the whole app. The first launch unpacks it under `%LOCALAPPDATA%\SuperBerry\runtime` and starts SuperBerry. Later launches use that copy, so the exe can sit anywhere by itself. The `SuperBerry-windows-x64` folder is the same files unpacked, and `SuperBerry.cmd` there starts them in place. Keep that folder whole if you use it: `electron`, `app`, `collector`, and `python` sit beside the command. If `python\python.exe` is missing, SuperBerry says so and stops. The same zip is built locally with `python3 scripts/package-windows.py`, which writes `dist/SuperBerry-windows-x64.zip` on any OS and, on Windows, `dist/SuperBerry.exe`.

The build is not signed, so SmartScreen may warn the first time. On Windows:

- Notifications use Windows notifications through Electron instead of `notify-send`.
- Battery is hidden. It reads `/sys` and is Linux only.
- The ledger and log files are not set to mode 0600. Windows uses the folder's own permissions under `%APPDATA%`.
- Quitting asks the collector to stop and waits up to five seconds so the ledger is closed cleanly.
- Kick webhook signatures are checked in Python, so OpenSSL is not needed.
- Windows has no time zone database, so the zip carries the `tzdata` package in `collector` for the YouTube quota reset at midnight Pacific Time.
- A new Windows install does not have a full certificate store. The zip carries `collector/cacert.pem`, and the collector uses it for HTTPS unless `SSL_CERT_FILE` or `SSL_CERT_DIR` is already set.
- The log file is not written if it has been replaced by a link.

The ledger is created at `~/.local/share/stream-donations/donations.db` (mode 0600). On macOS that is `~/Library/Application Support/stream-donations`. On Windows it is `%APPDATA%\stream-donations`. A database left in the old `data/` folder next to this checkout is moved there on the next start. Export commands and any SQLite client read that same file.

To run the collector without Electron, from this directory:

```sh
PYTHONPATH=source/collector python3 -m streamdonations serve
```

The process prints a one-time `http://127.0.0.1:47321/boot?token=...` link. Open that link in a browser on this machine. The UI and API stay on 127.0.0.1.

### Browser mode

SuperBerry can also run with a normal web browser as its window instead of Electron. The collector, the local server, and the ledger file are the same ones the desktop app uses. Starting SuperBerry with no flag still opens the desktop app, and the AppImage and the Windows exe and zip are built the same way.

- From a checkout: `PYTHONPATH=source/collector python3 -m streamdonations serve --open`
- From the Linux portable folder, the AppImage, or `bin/`: `SuperBerry --browser` (or `bin/stream-donations --browser`). `--browser` has to be the first argument. Other arguments after it go to `serve`, for example `--port 47400`.
- From the Windows folder (`SuperBerry-windows-x64`, or `%LOCALAPPDATA%\SuperBerry\runtime\current` after the exe has run once), in a command prompt: `python\python.exe -m streamdonations serve --open`. `SuperBerry.cmd` and `SuperBerry.exe` still start the desktop app.

`--open` opens the one-time sign-in link in the default browser. If SuperBerry is already running on that port, `--open` opens that copy instead of starting a second one. It signs in again when the running copy has the same `STREAM_DONATIONS_TOKEN`. Otherwise the page may ask to sign in, and the terminal says to use the link the running copy printed or to quit it first.

In browser mode:

- Quit, in the menu, stops SuperBerry. The ledger is closed cleanly and the tab says SuperBerry has stopped. Ctrl+C in the terminal does the same. The desktop app has no Quit item because closing its window already does this.
- YouTube, Kick, and Rumble sign-in open the platform's own login in a new browser tab, and the browser keeps that session. Open chat opens the platform's pop-out chat in a new window. Allow pop-ups for the page if the browser blocks it.
- Sending to Rumble chat, and the chats embedded beside the stream, need the desktop app. YouTube and Kick chat sending work the same, through their APIs.
- On Linux the computer's own notifications still appear. Where the computer has none for SuperBerry (Windows and macOS without the desktop app), Settings → Browser mode has Turn on browser notifications. The browser asks once, from that click. New automatic donations then show a browser notification while the tab is open. Manual entries stay quiet, the same as the desktop notifications.
- Keep the tab open. A browser slows timers in a background tab, so notices can come late, but collection runs in the collector and does not wait for the tab.

The page and API only accept changes from the SuperBerry page itself. A POST, PUT, PATCH, or DELETE must carry the page's own origin (`http://127.0.0.1:PORT` or `http://localhost:PORT`) and, when it has a body, `Content-Type: application/json`. A web page on another port of this computer is a different origin, so it cannot change the ledger or post to chat even though the browser would otherwise send the sign-in cookie. Scripts that use `Authorization: Bearer <token>` are not affected. Reads, the sign-in link, and the YouTube and Kick sign-in callbacks are not affected.

## What is collected

| Service | Endpoint | Events |
| --- | --- | --- |
| YouTube | `POST https://www.youtube.com/youtubei/v1/live_chat/get_live_chat` for any public live video. `superChatEvents.list` is optional and only covers your own channel | Super Chats, Super Stickers, and memberships visible in that chat |
| YouTube | `GET https://www.googleapis.com/youtube/v3/liveBroadcasts` then `liveChat/messages` | New members, member milestones, and gifted memberships while you are live |
| Rumble | `GET https://web7.rumble.com/chat/api/chat/{video_id}/stream` for a public video. The owner-only URL `https://rumble.com/-livestream-api/get-data?key=...` is optional | Rants, new subscribers, and gifted subscriptions from chat. The owner URL also includes subscriber dollar amounts |
| Kick | Public chat on the connection Kick's website offers for any channel. Signed webhooks are optional and only for a channel you own | New subs, gifted subs, and Kicks in that chat. Renewals need the owner webhook |

Ordinary chat, follows, and bans are not stored. YouTube Super Chats seen in live chat are not stored a second time; the Super Chat endpoint is the record for those.

## Connect a channel

The YouTube, Rumble, Kick, Streamers, Giveaway, Settings, and BLUE dialogs keep a short line visible where you need it to act, and put the longer explanation behind **Help**. Each Help panel starts closed and remembers on this computer whether you left it open. The `!total` and `!top` explanation is the same in all three platform dialogs.

### YouTube

Paste a watch URL or 11-character video id into **Public video URL or id**. The app reads the public live chat for that broadcast. Super Chats, Super Stickers, new members, and gifted memberships that appear in chat are stored. You do not need to own the channel, and Google’s “Access blocked” screen does not apply to this path. Chat closes when the broadcast ends, so leave the app open while the stream is live.

Turn on **Follow the live stream** and paste a channel URL, such as `https://www.youtube.com/@channel`, instead of a video. While that is on, the app checks the channel's public live page and reads chat only for the broadcast that is live now. The saved video URL is left as it is and is not used until the option is off. That check does not spend Data API quota. Opening a new ledger asks the same question: that switch changes the YouTube field from a video URL to a channel URL.

The Google sign-in below is only for your own channel’s Super Chat history from the last 30 days (`superChatEvents.list`). Skip it if you only want a public stream. If that account has never enabled live streaming, YouTube answers `liveBroadcasts` with `403 The user is not enabled for live streaming`. The app records that once and keeps collecting Super Chat history and any public chat. It does not mean the public video failed.

1. In Google Cloud, create a project and enable **YouTube Data API v3**.
2. On the OAuth consent screen, leave the app in **Testing** and add the Google account you will sign in with under **Audience → Test users**. If that address is missing, Google shows “Access blocked: [app name] has not completed the Google verification process” (`403 access_denied`). You do not need to submit the app for verification to read your own channel.
3. Create an OAuth client of type **Web application**.
4. Add the redirect URI shown in the app. With the default port it is `http://127.0.0.1:47321/oauth/google/callback`.
5. Paste the client id and secret into the YouTube card, save, and click Connect.

The app requests `https://www.googleapis.com/auth/youtube.force-ssl`. That scope reads your own Super Chat history and can post a chat message as the signed-in account. A grant from before this change can still read. Click Connect again before sending. The default Data API quota is 10,000 units per day and resets at midnight Pacific Time. Each call is counted at Google's published cost before it is sent: `channels.list`, `videos.list`, `superChatEvents.list`, and `liveBroadcasts.list` cost 1 unit, `liveChatMessages.list` costs 5, and posting one message (`liveChatMessages.insert`) costs 20 ([quota calculator](https://developers.google.com/youtube/v3/determine_quota_cost)). Super Chat history is one page per poll, at least 5 minutes apart, and an unfinished 30-day walk continues from the saved page instead of starting over. Own-channel live chat is polled only while a broadcast is active, at YouTube's `pollingIntervalMillis` and not more often than every 30 seconds, up to 2,000 messages per read. One live broadcast then costs about 634 units an hour (600 for chat, 30 for broadcast checks, 4 for Super Chats at the default 15 minutes), so the daily quota lasts about 15 hours of streaming. Memberships from that read can appear up to 30 seconds late; a public video or followed channel still reads them right away. Broadcasts are checked every 5 minutes while idle. A `quotaExceeded` response pauses signed-in calls until the next Pacific midnight. Public live chat uses the watch page, not this quota. Sending uses `videos.list` `liveStreamingDetails.activeLiveChatId` for the open ledger's video. That id is present while the broadcast is live. A waiting room can be read before YouTube will accept a message.

**YouTube sign-in**, under the combined chat, opens YouTube's own sign-in at `https://www.youtube.com/signin` and keeps that website session on this computer. The password is not stored. **Open YouTube chat** opens YouTube's pop-out chat for the open ledger. That window is separate from Connect. SuperBerry does not type into it.

### Rumble

The account API at [rumble.com/account/livestream-api](https://rumble.com/account/livestream-api) is only for a channel you own. The key in that URL will not read someone else's stream.

For any public stream, paste the video URL (or the numeric chat id from the watch page) into **Public video URL or chat id**. The app follows `https://web7.rumble.com/chat/api/chat/{video_id}/stream`, which is the chat the player shows to viewers. That includes the rant text and price, gifted-sub notices, and new-subscriber notices. It does not include the dollar amount of a subscription. Add the owner API URL only when you want those amounts for your own channel.

Turn on **Follow the live stream** and paste a channel URL such as `https://rumble.com/c/channel`. While that is on, chat is read only when that channel page shows one live video, using that video's chat id. The owner API URL is not a channel address. The saved video URL is not used until the option is off. The new-ledger prompt uses the same switch for Rumble.

Leave the app running while the stream is live. A rant stays in the chat for its paid duration, and the connection is read continuously so a short notice is not missed between polls.

Rumble does not document a chat post for this app. Rumble's terms do not allow a script to type or send chat, and using one can restrict the account. On the Rumble page, **Continue** only if that risk is acceptable. That choice stays on this computer and is not part of the settings export. After that, combined chat can post into the signed-in Rumble chat for the open stream. The line is typed into Rumble's own chat box, and the send button on the row under that box is pressed. A chat window that just opened is given a moment to connect before that button is pressed. If Rumble says there was a problem submitting, the app tries once more. The rant, gift, and subscribe controls on that row are not pressed. It counts as sent only after the line leaves the box. The password is not stored, and the collector does not see that website session. **Rumble sign-in**, under the combined chat, opens Rumble's own login at `https://rumble.com/login.php` and keeps that website session on this computer. **Open Rumble chat** opens Rumble's pop-out chat for the open ledger in a separate window. The kiosk uses that same signed-in chat.

### Kick

Paste a channel URL or slug into **Public channel**, for example `https://kick.com/channel`. The app joins that channel's public chat. New subscriptions, gifted subscriptions, and Kicks that show up in chat are stored. You do not need to own the channel. Subscription renewals are not announced in chat; those still need the owner webhooks below. Chat stays connected whether or not a broadcast is on. Turn on **Show whether this channel is live** to add live or offline to the status. That does not change which chat is read. A new ledger asks for that channel directly and does not add a follow switch, because the address is already a channel.

If the public chat goes quiet longer than Kick's connection promises, for example after the computer switches networks, the app drops that connection and opens a new one. On Pusher it sends a ping after Kick's `activity_timeout` and gives up 30 seconds after an unanswered ping; on Centrifugo it gives up 10 seconds after a missed server ping; any connection silent for 150 seconds is dropped. The status reads "Kick chat stopped responding; reconnecting." until the new connection is open, including in the webhook status. Retries wait about 2 seconds, doubling to at most 60, and start over once a connection has stayed up 30 seconds. The operating system's TCP keepalive is also turned on for that socket. A soft disconnect and reconnect is no longer needed.

1. Create an app at [kick.com/settings/developer](https://kick.com/settings/developer).
2. Register the redirect URI shown in the app (`http://127.0.0.1:47321/oauth/kick/callback` by default).
3. Save the client id and secret here, then click Connect. The requested scopes are `user:read`, `channel:read`, `events:subscribe`, `kicks:read`, and `chat:write`. Click Connect again if the account was connected before chat could be sent. The same account posts into the open ledger's channel. The account email is not saved.
4. Kick only delivers these events as webhooks. In the Kick app, enable webhooks and set the URL to the webhook value shown in the card. The path on this app is `/kick/webhook`.
5. By default the listener is `127.0.0.1:47322`, which Kick's servers cannot reach. Either set the listen host to `0.0.0.0` and forward that TCP port, or point a tunnel at it and put the public origin in **Public base** (for example `https://your-host:47322`).
6. Click Subscribe after the webhook URL answers. Subscribing before Kick can reach the listener gets the subscription disabled after about a day of failures.

**Kick sign-in**, under the combined chat, opens Kick's own login at `https://kick.com/login` and keeps that website session on this computer. The password is not stored. **Open Kick chat** opens Kick's pop-out chat for the open ledger. That window is separate from Connect. SuperBerry does not type into it. A host leaves that chat on the channel it was opened for.

Every webhook is checked with Kick's RSA public key (`GET https://api.kick.com/public/v1/public-key`, with the documented key as fallback) over `message id + "." + timestamp + "." + raw body`, using SHA-256 and PKCS#1 v1.5. Unsigned or stale requests are rejected. The check uses the `openssl` binary so the collector does not need extra Python packages.

## Streamer presets

Open **Streamers** from the menu to save a name, the channel URL for YouTube, Rumble, and Kick, that streamer's goal tiers, and an optional BLUE script. A preset needs at least one channel and can leave the others blank. Goals alone are not enough. Turn goal tiers on to name up to five goals. Saving the same name again replaces those channels, goals, and the script. Leaving the script on None clears it. **Edit** on a saved preset fills the form, so the goals can change without retyping the channels. Presets are shared by every ledger. Saving one does not change the ledger that is open. **Apply to open ledger**, beside Save, saves the form first and then writes that preset onto the open ledger, the same as choosing it in the ledger's addresses and saving: YouTube and Rumble follow that channel live, a blank YouTube or Rumble channel turns follow live off and clears that video, Kick is set to that channel, and the goal tiers and BLUE script are replaced. The app asks first. Donations stay. A connected ledger picks up the new channels on the next check, and a different BLUE script starts over. Other ledgers keep what they had. If the save is refused, nothing is applied. If the save works and the apply is refused, the preset stays saved and the message says why.

When you open a new ledger, or open a ledger's addresses again, choose a preset to fill the channel fields. YouTube and Rumble turn on **Follow the live stream**. Kick is filled as the channel. Saving that dialog writes the preset's goal tiers and BLUE script onto that ledger. **None**, then Save, leaves the ledger's goals and script as they are. You can edit the addresses before Save, or skip and leave them unchanged. Skip still saves nothing. A new ledger starts with goal tiers off, no BLUE script, and does not copy the previous ledger's goals. Presets, goal tiers, and BLUE scripts are not included in the settings export. Membership price, the extra chat note, and top donors stay in Settings and in that export. Ledger page size is in that export too, and it is the same for every ledger.

## BLUE

**BLUE** in the menu, between Streamers and Settings, edits Berry Lexical User Events. A script is a list of lines. Each line is its own command, and a line that cannot be read is skipped. The usual shape is `ON DONATION IF … THEN SEND CHAT TO YOUTUBE "Thanks @{DONOR}."` Punctuation outside the quotes is ignored, and so is a word that is not a keyword, so `amount is above 49.98` is the same as `amount above 49.98`. The message in double quotes is sent as written. `{TOTAL}`, `{TIER}`, `{DONOR}`, `{MENTION}`, `{AMOUNT}`, `{RAWAMOUNT}`, `{CURRENCY}`, `{PLATFORM}`, `{TYPE}`, `{TIME}`, `{MINUTES}`, `{NAME}`, `{TOTALLINE}`, and `{TOPLINE}` are filled in. Case and underscores do not matter, so `{RAW_AMOUNT}` is `{RAWAMOUNT}`. A name BLUE does not know stays as typed. `{DONOR}` does not add `@`; `{MENTION}` does. `{TOTAL}` is this ledger's total on every line. `{TIER}` is how many goals that total has reached, also on every line. `{NAME}` is filled in on `ON GOAL`.

`ON DONATION` runs for a new row on a ledger that is connected and has that script, when that gift's platform time is no more than 2 minutes old and not from before the script started. Older rows, such as a signed-in YouTube backfill or recent history after a reconnect, are still stored and counted but do not run `ON DONATION`. The log says so once each time the script starts. `ON TIMER` runs once per completed minute since that ledger was connected. Minute 0 does not run, so a check for every 20 minutes first runs at 20. `IF` can test `PLATFORM`, `AMOUNT` in US dollars, `RAW AMOUNT` in the gift's own currency, `CURRENCY`, `TOTAL` for this ledger, `DONOR`, `TYPE`, the computer's local `TIME`, `MINUTES`, `TIER`, and, on a goal, `NAME`. `THEN` can `SEND CHAT TO` YouTube, Rumble, Kick, all of them, or `PLATFORM`, or `NOTICE TO` the app, the log, or the system. Chat to more than one platform waits a random 2 to 6 seconds between those sends.

`TIME` compares with a clock time such as `14:30` or `9:00 PM`. `MODULO n` takes the remainder after dividing by n, and `MOD` and `%` mean the same thing. On `TIME`, `MODULO` counts minutes since local midnight, so it takes a plain number: `time modulo 20 equals 1` is true at 00:01, 00:21, 00:41, and so on through 23:41, and `time % 60 equals 30` is true at half past every hour. `MINUTES`, `AMOUNT`, `RAW AMOUNT`, `TOTAL`, and `TIER` take `MODULO` too. `CURRENCY` and the other text fields do not. A donation and a goal use the time the app saw them. An `ON TIMER` line that uses `TIME` runs once for each new clock minute after the ledger was connected, about a second after that minute starts, instead of on the minutes since the connect. The other `ON TIMER` lines still run once per completed minute since the connect. The last clock minute is saved with the ledger, so restarting the app does not run that minute again. If the app is busy for over a minute, a minute it missed is not run later.

A value that cannot work is reported in the editor and the log, and that line is skipped: a `TIME` that is not a clock time, a clock time after `TIME MODULO`, a word where `AMOUNT`, `RAW AMOUNT`, `TOTAL`, `MINUTES`, or `TIER` needs a number, MODULO on a text field such as `CURRENCY`, a modulus of zero or below, and a modulo check that can never be true, such as `minutes modulo 20 equals 20` or `above 19`, since the remainder is 0 through 19. A `%` anywhere else, such as `amount above 5%`, `amount above %5`, or inside the message, is ignored as before. A `-` before a number makes it negative, even with a space, so `time modulo 20 equals -1` is reported and `amount above - 10` means above minus 10.

`TOTAL` is the same USD total the ledger shows, on a donation, a timer minute, and a goal. `TIER` is on those lines too. It is 0 when no goal amount has been reached. It counts enabled goal amounts with a dollar value, lowest first, from 1 up to 5. A turned-off row, or a row with no amount, does not count. A cent short of a goal still counts. With the tiers turned off, `TIER` stays 0 and `TOTAL` still prints.

`ON GOAL` runs once when that script starts. If the total is already past a goal, that start runs once with the tier already reached, not once for every tier below it. `ON GOAL` runs again when a new donation reaches the next goal, once for each new tier, after that donation's other actions. On that goal line, `TIER` is the tier just reached, so one gift that passes two goals still runs both. `DONOR` is the person whose gift reached that tier. `NAME` is the name set on that goal. `PLATFORM` is where that gift came from, and `AMOUNT` is what was donated. At tier 0 those are empty. A goal with no name leaves `NAME` empty too. A donation or a timer minute uses the tier the ledger is on after that moment, and leaves `NAME` unfilled.

Save stores the script on this computer, shared by every ledger, up to 40 scripts. The same name replaces that script and keeps its id. Import reads a `.blue` or `.txt` file into the editor and does not save it. Export downloads the editor text as a `.blue` file. A streamer preset can name one saved script. Saving the ledger's addresses while that preset is chosen writes the script onto that ledger. Connecting the ledger starts the script. Soft disconnect stops it and drops anything still waiting to be sent. Editing the text does not restart the minute timer. Choosing a different script, or connecting again, does. A new ledger starts with no script.

An action that cannot be completed is skipped. The log names the script, the line, and what failed. The message itself is not written there unless the line is `NOTICE TO LOG`. Rumble chat still needs Continue on the Rumble page. YouTube and Kick use the signed-in account. Nothing is typed into the YouTube or Kick windows.

### PLATFORM, raw amounts, and chat lines

```
ON DONATION IF AMOUNT IS ABOVE 49.89 THEN SEND CHAT TO PLATFORM "Thank you {MENTION}!"
ON DONATION IF CURRENCY IS JPY AND RAW AMOUNT IS ABOVE 9999 THEN SEND CHAT TO PLATFORM "¥{RAWAMOUNT}! Thank you {MENTION}!"
ON DONATION IF CURRENCY IS KICKS THEN SEND CHAT TO PLATFORM "{RAWAMOUNT} Kicks, thanks {MENTION}"
ON TIMER IF MINUTES MODULO 30 EQUALS 0 THEN SEND CHAT TO ALL "{TOTALLINE}"
ON GOAL IF TIER ABOVE 0 THEN SEND CHAT TO PLATFORM AND NOT RUMBLE "{NAME} reached thanks to {MENTION}! {TOPLINE}"
```

- **`PLATFORM` as a chat target** is the platform the gift came from: YouTube, Rumble, or Kick. On `ON GOAL` it is the platform of the gift that reached that tier, and at tier 0 there is none, so nothing is sent. `ON TIMER` has no gift, so `SEND CHAT TO PLATFORM` on a timer line is a syntax error. It combines with the other targets: `TO PLATFORM AND KICK`, `TO ALL AND NOT PLATFORM`, or `TO PLATFORM AND NOT RUMBLE`. A platform named on the line and also reached through `PLATFORM` is sent once. `PLATFORM` as a condition (`IF PLATFORM IS KICK`) is unchanged.
- `PLATFORM` sends nothing for manual or pasted entries, or for rows on the Other platform. It also skips the gift when that chat cannot be posted to right now: YouTube not signed in for sending or paused at its quota, Kick not signed in, or no desktop window that can type into Rumble. A gift that only the signed-in YouTube API, the Kick webhook, or the Rumble owner API stored is answered only when it is this ledger's chat. That means the same YouTube channel id (a `UC…` id in the channel or video address, or the signed-in account's live broadcast for the saved video), the same Kick channel, or the same Rumble channel name. Each of these skips is written to the log once each time the script starts. A target named outright, such as `TO YOUTUBE`, works as before.
- **`RAW AMOUNT`** (also `RAWAMOUNT` or `RAW_AMOUNT`) is the number as it was sent, in its own currency, without conversion: 1000 for ¥1000, 5 for a $5 rant, and the coin count for Kicks (100 Kicks is 100, not $1). A gift with no paid amount, such as most memberships and subs, has none, so a `RAW AMOUNT` test is false and `{RAWAMOUNT}` is empty. No membership price is assumed. The comparison uses the exact number. `RAW` is a keyword that only goes before `AMOUNT`, and is an error anywhere else. `AMOUNT` stays US dollars.
- **`{RAWAMOUNT}`** prints just the number, with no symbol, code, or thousands separator, and with the currency's ISO 4217 decimals: 0 for JPY, KRW, VND, CLP, ISK, and the other zero-decimal currencies, 3 for BHD, IQD, JOD, KWD, LYD, OMR, and TND, and 2 otherwise. Kicks are whole numbers. It is filled on `ON DONATION` only. Goals and timers leave it empty.
- **`CURRENCY`** is the gift's currency code, such as `USD` or `JPY`, and `KICKS` for Kicks (`KICK` also matches). `IF CURRENCY IS JPY` and `IF CURRENCY EQUALS jpy` are the same, and case does not matter. A gift with no currency matches nothing. `{CURRENCY}` prints the code. A raw amount on its own mixes currencies, so `RAW AMOUNT ABOVE 1000` matches ¥1000 and €1000 alike. Pair it with `CURRENCY`.
- `IS` followed by one value at the end of a comparison means `EQUALS`, so `CURRENCY IS JPY` and `PLATFORM IS KICK` work. Before `ABOVE`, `BELOW`, or `EQUALS`, `IS` is still ignored.
- **`{MENTION}`** is `@` and the donor's name, or empty for anonymous and unnamed donors. On a goal it is the donor who reached the tier.
- **`{TOTALLINE}`** is the `!total` line and **`{TOPLINE}`** is the `!top` line, the same text the chat commands send, on any line, without turning the commands on. `{TOTAL_LINE}`, `{TOTAL_BLURB}`, `{TOP_LINE}`, and `{TOP_BLURB}` are the same. Each chat target gets its own copy, fitted to that chat with the rest of the message. `{TOPLINE}` is empty while top donors are hidden.
- Every BLUE chat line is cut to fit its chat: 200 characters on YouTube and Rumble, 500 on Kick. Notices get up to 500 characters of those lines.
- **Guards.** One line thanks the same gift once: the same donor, platform, and amount in the same currency on that line and chat within 2 minutes is sent once. Two sources often store one Super Chat or Kick gift twice, and this stops a double thank-you. Two real gifts of the same amount from one person inside 2 minutes are also thanked once. BLUE queues at most 6 chat lines per chat in any minute. Both are saved with the ledger, so restarting SuperBerry keeps them until they run out: a second copy of a gift in those 2 minutes is still not thanked, and the 6 lines still count for their minute. Each entry expires on its own and the list is kept short. Only a short code for each gift is saved, not the donor's name, and none of it is in the settings export. A soft disconnect and connect does not clear them, since they run out within 2 minutes anyway. If the computer's clock goes back, a saved time is pulled back to the new time, so nothing is held for longer than its own 2 minutes or 1 minute from then. More are dropped and logged. The `!total` and `!top` cooldown is separate.

### Editor colors and the test box

**Help**, beside the Script label, opens the BLUE reference under the editor in short sections: events, conditions and fields, MODULO and TIME, targets, placeholders, limits and guards, editor colors, and the test box. It starts closed, and whether you left it open is remembered on this computer.

The editor colors each word the way BLUE reads it: `ON`, `IF`, `THEN`, `AND`, `OR`, and `NOT`; the event; fields such as `AMOUNT` and `TIME`; `EQUALS`, `ABOVE`, `BELOW`, `IS`, and `MODULO` (`MOD`, and `%` when it is MODULO); numbers, clock times, and minus signs; chat targets in their platform colors; notice targets; the message; and placeholders. A placeholder BLUE does not fill in is red. The words come from BLUE itself (`GET /api/blue/grammar`), so the colors match what runs. The editor uses a fixed-width font, numbers its lines, and does not wrap long lines; it scrolls sideways instead. Typing, undo, paste, input methods, import, and export work as before, since the box you type in is still a plain text box with the colors drawn under it.

While you type, about a third of a second after you stop, the script is checked. A line that will be skipped gets a red mark beside its number, with the reason when you hover the mark, and a red underline, and the reasons are listed under the editor as before. Saving still keeps those lines.

**Test a line** under the editor is a dry run. Nothing is sent, queued, logged, or saved. Type one line, or press **Whole script** to test every line of the editor against one event. Pick the event: a donation (amount, currency, donor, platform, and type), a timer minute (minutes since connect and a clock time; blank is now), or a goal (the tier reached, and the donor, platform, and amount of the gift that reached it). Pick the ledger to test on. Its total and tier are used unless you type others; on a goal, a blank tier is the ledger's tier, so a fresh ledger tests tier 0. The event follows the line's `ON` word as you type. The result shows:

- whether the line matched, and each condition with the value it saw, such as `TIME MODULO 15 EQUALS 0` and `14:30 is minute 870 of the day`;
- every chat after `PLATFORM` and `ALL` are worked out, with the final message cut to that chat's length;
- which guard would stop it. For `PLATFORM`, a chat that cannot be posted to from here, an Other-platform gift, or a goal at tier 0. The 6-lines-a-minute count for that chat, read from the ledger. The 2-minute repeat thanks is checked when the line is one of the lines of the script that ledger runs, since that is where its history is kept; any other line says it was not checked. Within one test, chats that would already be sent count too: with **Whole script**, a later line to the same chat sees the slots the earlier lines would use, the same as a real gift. When the script has the same line more than once, the Nth copy in the test is checked against the Nth copy in the saved script.

A test gift is from now and has no real source. The 2-minute age guard always lets it through, and the result says so. For `PLATFORM`, the result shows what would happen for the platform you picked, and notes that a real signed-in YouTube, Kick webhook, or Rumble owner-API gift would also need to come from that ledger's chat, and a manual or pasted entry would be skipped. `{TOTALLINE}` and `{TOPLINE}` use the ledger's real rows, not a typed total. The test only reads the guards, so it never uses up a repeat or one of the 6 lines.

## Chat commands

Viewers can ask for the stream total or the top donors in chat. Turn on **Answer !total and !top in this chat** in the YouTube, Rumble, or Kick dialog. Each ledger has its own switch for each platform, and every switch is off until it is turned on. A new ledger does not copy them.

- `!total` replies with this ledger's stream total line, the same one as the banner: the total, the next or last named goal with its bonus, and the extra chat note.
- `!top` replies with the top three named donors, the same line as the top-donors bar. It has no `@`, so donors are not pinged. Anonymous and unnamed donors are skipped. `!top` is ignored while top donors are hidden.
- The command must be the first word. Case does not matter, and words after it are fine, so `!TOTAL please` counts. `!totals` and `what's the !total` do not.
- The reply goes back into the chat the command came from. YouTube and Kick replies are posted as the signed-in account. Each YouTube reply uses 21 units of the Data API quota (`videos.list` plus `liveChatMessages.insert`). Rumble replies are typed by the desktop app into the signed-in Rumble chat, so they need Continue on the Rumble page and the desktop window open. Browser mode cannot answer on Rumble, and the Rumble dialog says so.
- One cooldown covers both commands in one chat: after a reply is attempted, every command in that chat is ignored for 2 minutes. Nothing is saved for later. The cooldown is per chat (platform plus video, chat, or channel), not per ledger, so two ledgers reading the same chat answer once. It is kept in memory, so it starts over when SuperBerry restarts.
- Commands are read from public chat: the public video URL, the followed channel, or the Kick channel. The Google sign-in alone does not read ordinary chat. A platform that can read but cannot post (not signed in, a read-only sign-in, the YouTube quota paused, or no Rumble window) ignores commands without starting the cooldown, and its dialog says why.
- History never answers: lines from before SuperBerry started listening, lines older than a minute, and lines with no time in the first batch after a chat connects. A message seen twice, the signed-in account's own lines, and SuperBerry's own replies are ignored. Background ledgers answer too, and their chat still stays out of the open ledger's chat panel and giveaway.
- Replies are cut to fit: 200 characters on YouTube and Rumble, 500 on Kick. The extra chat note is dropped first, then long donor names are shortened. Rumble publishes no limit, so 200 there is a cautious guess.

## Ledger

A **Stream** panel on the left plays the open ledger's video. It starts closed, so the ledger stays as it is until you open it. Choose YouTube, Rumble, or Kick. The player uses the address already saved on that ledger. When follow live is on, it uses the broadcast that is on now, and says so if that channel is offline instead of playing an older video. The video frame stays reserved when nothing is playing. Reload checks again. Choosing another platform, or another ledger, starts that video. **Playback** pauses and plays, **Live** moves to the latest point in the stream, and **Mute** silences it for this window. The choice lasts for this window only and is not part of the settings export. The same panel lists read-only chat from YouTube, Rumble, and Kick together, and each line is labeled with that platform's icon. Those icons also sit beside every platform name, and the name uses the same color as that platform's ledger row. Each line also shows the time the platform gave that message. YouTube emoji show too: a standard emoji is the emoji itself, and a channel or member emoji is its picture from YouTube, with its shortcut (such as `:hand-pink-waving:`) as the hover text and as the text everywhere else, including the ledger message, `!total`/`!top`, the giveaway, and copies. If a picture cannot load, the shortcut shows instead. The clock icon, **Timestamps**, shows or hides those clocks for this window only, and that choice is not part of the settings export. The star beside it, **Giveaway**, opens the drawing. Typing a message is not part of this panel. Those lines are not saved in the ledger and are not part of the settings export.

**Giveaway**, the star beside Timestamps in the Stream panel, opens a prompt for a keyword, such as a hashtag. The app does not type it into chat. Start begins counting messages on the open ledger that are that keyword alone. Case does not matter. Each person counts once on a platform, and the same name on another platform counts again. Repeat messages from the same person on the same platform do not. Leave the timer blank to run until Stop. A timer stops the drawing on its own. Stop, or the end of the timer, picks one winner at random and removes that person from the pool. **Redraw** picks the next person from whoever is left and adds them to the winner list. Someone who has already been drawn is not drawn again. When the pool is empty there is nobody else to draw. Copy announces the keyword. After the drawing is closed, each winner has its own copy button. Closing the prompt does not stop a drawing that is running. The drawing is not saved and is not part of the settings export.

**Kiosk view**, the layout icon in the header beside Export, switches the window to the video, the stream-total blurb, the top-three blurb, a short ledger, and the three chats. Under those chats, a message box and Send post with Enter the same way the combined chat does. Manual entry on that bar opens the manual-entry form. While this computer is running on its own battery, an icon and the percent sit above the header buttons. Wall power hides that line. A battery that belongs to a mouse or a keyboard stays off this line too. The ledger sits beside the video. The total and the top three are one-line headers above that ledger, each with its copy button and the same send button as the ledger. Send posts the line that copy would copy. The video uses the same source dropdown, over the picture, and only one player runs. That bar, including playback, live, mute, and reload, shows while the pointer is on the video. Copy on the total and on the top three copies the same blurbs as the ledger. The short ledger lists time, donor, amount in USD, platform, and type for the page already open, earliest first. A new row scrolls that list to the latest line. A message is on the row tooltip. YouTube, Rumble, and Kick chat sit in the bottom row and use the same website sign-in as the pop-out windows. Each chat's header reloads that chat, makes its text smaller or larger, returns that text to the normal size, and collapses that chat to a narrow strip. The other chats use the freed space. Click the strip to open that chat again. A new message while that chat is collapsed puts a bubble at the bottom of the strip. Opening the chat clears the bubble. On a narrow window the bubble sits at the end of the bar. A chat with no address on the open ledger collapses on its own. For YouTube and Rumble, follow live uses the channel, and a video address is used when follow live is off. Kick uses its channel. A channel that is saved but has no chat open stays as you left it. That automatic fold is not saved. Click the strip to open it until the ledger or its addresses change. On a narrow window the strip is a short bar across the row. Text size and which chats are collapsed stay on this computer and are not part of the settings export. A host leaves the Kick chat on the channel it was opened for. YouTube and Kick are shown as those sites draw them. Rumble can receive a combined-chat line after Continue on the Rumble page, posted as the signed-in Rumble user. Outside the app those boxes stay empty. The switch lasts for this window only and is not part of the settings export.

The stream total counts every donation on the open ledger. **+** opens a separate ledger. There is no control to restart the total on the ledger you already have. Stream total, top donors, and manual entry start collapsed. The chevron at the right edge of each section opens it, and the one-line summary stays visible while it is closed. That stream-total line includes how far the total is from the next named goal when goal tiers are on. The copy button beside the stream-total chevron copies that total blurb. The copy button beside the top-donors chevron copies the top three donors. Donor names are shown without @ in the ledger, the top-donors list, thank-yous, notifications, and exports. Copying the top three, or a thank-you under that section, puts @ back in front of each username. A status bar across the bottom of the window shows the same total and goal sentence, the top three, and those copy buttons. While a giveaway timer is running, the right side of that bar shows the time left. That side stays empty until a temporary status has something to show. Manual entry keeps donor, platform, type, and amount in USD visible while it is collapsed, along with +, Paste, and Clear. Paste still fills the form. The collapsed row shows the dollar value, and + saves the pasted amount, currency, and message. If that gift matches one already on this ledger — the same donor, platform, type, amount, currency, and message — the app asks before saving a second copy. Donations collected from a platform are stored without that question. Hover a manual row and Edit appears beside Delete. The same form fills in and + becomes Save. An automatic row cannot be edited. Double-click a ledger's title to rename it. When YouTube, Rumble, or Kick has a stream on for this ledger, or an upcoming broadcast this ledger is already following, the name dialog can fill the name from that title. Choose the platform when more than one is available. The name changes only after Save. Cancel and Escape leave the saved name as it was.

Each ledger's tab has a menu for its addresses and its plug. Addresses opens that ledger's channel fields. The plug is soft disconnect. It starts on, so a new ledger collects after you reconnect it. Pausing stops that ledger's API requests and live-chat reading and keeps sign-in and addresses. Other ledgers keep their own setting. Every ledger whose plug is connected keeps collecting, including while you are looking at a different ledger. The stream panel, its chat, and a giveaway follow the ledger that is open. **Always show**, in that same menu, puts the address button and the plug on the tab. While those buttons stay in the menu, a paused ledger shows a red menu icon. With Always show on, a paused ledger keeps the red plug visible, and the plug stays out of sight until the pointer is on the tab while that ledger is collecting. That choice stays on this computer and is not part of the settings export.

Open a row to read the message and save a note. Previous and Next move a page at a time, and the pager shows the page number rather than the entry count. Settings → Ledger page size chooses 10, 25, 50, or 100 rows, or a custom whole number from 1 to 500. Until you save one, a page is 100 rows. A donation that just arrived stays highlighted for one minute, then uses the normal row colors: the left edge is the platform, and the background follows the dollar amount, the same scheme as Live Till. Donations are stored as they arrive. The ledger total is the US dollar sum of every donation except individual gift receipts, which would count a gifted membership twice. Set **Membership price** from the menu on the right, under Settings, when a membership or subscription arrives without a price. YouTube and Kick bundles use that price times the number of memberships. YouTube’s “Sent 5 … gift memberships” counts as five. A Rumble bundle uses that price for the first subscription and $5.00 for each additional one, so five subscriptions at $4.99 are $24.99.

The **Export** button in the header, beside Log and the menu, downloads the ledger that is open. CSV and JSONL are in that button's menu. The file uses the filter on screen. **Archive ledger** in that same menu copies the whole open ledger, including rows hidden from the totals, into `archive.db` beside `donations.db`, then closes that ledger. The on-screen filter is not used. Other ledgers stay. Closing the last ledger, from its tab or from Archive, opens a new blank ledger. That blank ledger does not ask for addresses. Closing a ledger that has donations asks whether to save it to the archive first. An empty ledger does not ask that. Saying no still asks before those donations are removed. **Reset ledger** is in the header menu. It asks first, then deletes every donation on the open ledger. Other ledgers, settings, and the log stay.

Export CSV uses the same filter and these columns: Date, Time, Donor, Amount, Platform, Type, Currency, Amount USD, Message. Date and time are this computer's local time, oldest first. The download is named with that oldest donation's date, then the ledger name, as in `06012024-Friday.csv`. JSONL uses the same name with `.jsonl`. The date is MMDDYYYY. A character that cannot be in a Windows, Linux, or Mac file name is replaced. A ledger with nothing to export uses the name alone. Type is only Super Chat, Super Sticker, or Membership. Rants are Super Chats. Subscriptions and memberships are Membership. Kick coins convert at 100 coins = $1; under $5 is a Super Sticker and $5 or more is a Super Chat. Amount USD uses the cached response from `https://open.er-api.com/v6/latest/USD`. That open endpoint allows about one request an hour and updates its table about once a day, so the app checks again only when the last attempt is more than an hour old. Settings → Update exchange rates checks immediately, unless the provider returned HTTP 429, in which case it waits 20 minutes. The saved document is the response body, not a second copy of the rates. JSONL is the full stored record for that download. **Open archive** in the header menu shows the ledgers already saved in `archive.db`. Each row is one saved ledger. The arrow opens that ledger's dates, and each date opens the donations from that day. Nothing there can be edited, and the open ledger is not changed. Messages that start with `=`, `+`, `-`, or `@` are prefixed in CSV so a spreadsheet does not treat chat text as a formula.

From a shell, against the same database:

```sh
PYTHONPATH=source/collector python3 -m streamdonations export csv -o ~/exports.csv
PYTHONPATH=source/collector python3 -m streamdonations export jsonl
PYTHONPATH=source/collector python3 -m streamdonations poll
```

`poll` checks YouTube and Rumble once. Kick donations still arrive only through the webhook listener, so use `serve` for those.

## Layout

```
source/collector/streamdonations/   Python collector and the page the window loads
source/electron/                    Electron main process
bin/stream-donations                Launcher for a checkout
dist/SuperBerry-linux-x64/          Portable Linux folder (Electron, Python, collector)
dist/SuperBerry-x86_64.AppImage     Portable Linux AppImage
~/.local/share/stream-donations/    Ledger, tokens, and client secrets (not in the app folder)
```

Ports default to 47321 for the UI and 47322 for Kick webhooks. Override the UI port with `STREAM_DONATIONS_PORT`. Tokens and the Rumble URL are stored in the database and are not returned to the page after you save them.
