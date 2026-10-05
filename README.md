# Stream Donations

A local Electron app that records donation events from your own YouTube, Rumble, and Kick channels and keeps them in a SQLite ledger you can filter, annotate, and export.

The window is only a front end. A Python collector talks to each service and writes the ledger in the home directory (`~/.local/share/stream-donations/donations.db` on Linux). The application folder holds no API keys, tokens, or database.

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

A copy you can move to another Linux machine, with Electron and Python beside the launcher, is built with `scripts/package-linux.sh`. That writes `dist/StreamDonations-linux-x64/` and, when the AppImage tool is available, `dist/StreamDonations-x86_64.AppImage`. Windows and macOS are not built here. They should use the same split: a shell, the collector, a private Python, and `user_data_dir()` for the home folder.

The ledger is created at `~/.local/share/stream-donations/donations.db` (mode 0600). On macOS that is `~/Library/Application Support/stream-donations`. On Windows it is `%APPDATA%\stream-donations`. A database left in the old `data/` folder next to this checkout is moved there on the next start. Export commands and any SQLite client read that same file.

To run the collector without Electron, from this directory:

```sh
PYTHONPATH=source/collector python3 -m streamdonations serve
```

The process prints a one-time `http://127.0.0.1:47321/boot?token=...` link. Open that link in a browser on this machine. The UI and API stay on 127.0.0.1.

## What is collected

| Service | Endpoint | Events |
| --- | --- | --- |
| YouTube | `POST https://www.youtube.com/youtubei/v1/live_chat/get_live_chat` for any public live video. `superChatEvents.list` is optional and only covers your own channel | Super Chats, Super Stickers, and memberships visible in that chat |
| YouTube | `GET https://www.googleapis.com/youtube/v3/liveBroadcasts` then `liveChat/messages` | New members, member milestones, and gifted memberships while you are live |
| Rumble | `GET https://web7.rumble.com/chat/api/chat/{video_id}/stream` for a public video. The owner-only URL `https://rumble.com/-livestream-api/get-data?key=...` is optional | Rants, new subscribers, and gifted subscriptions from chat. The owner URL also includes subscriber dollar amounts |
| Kick | Public chat at `wss://ws-us2.pusher.com` for any channel. Signed webhooks are optional and only for a channel you own | New subs, gifted subs, and Kicks in that chat. Renewals need the owner webhook |

Ordinary chat, follows, and bans are not stored. YouTube Super Chats seen in live chat are not stored a second time; the Super Chat endpoint is the record for those.

## Connect a channel

### YouTube

Paste a watch URL or 11-character video id into **Public video URL or id**. The app reads the public live chat for that broadcast. Super Chats, Super Stickers, new members, and gifted memberships that appear in chat are stored. You do not need to own the channel, and Google’s “Access blocked” screen does not apply to this path. Chat closes when the broadcast ends, so leave the app open while the stream is live.

Turn on **Follow the live stream** and paste a channel URL, such as `https://www.youtube.com/@channel`, instead of a video. While that is on, the app checks the channel's public live page and reads chat only for the broadcast that is live now. The saved video URL is left as it is and is not used until the option is off. That check does not spend Data API quota. Opening a new ledger asks the same question: that switch changes the YouTube field from a video URL to a channel URL.

The Google sign-in below is only for your own channel’s Super Chat history from the last 30 days (`superChatEvents.list`). Skip it if you only want a public stream. If that account has never enabled live streaming, YouTube answers `liveBroadcasts` with `403 The user is not enabled for live streaming`. The app records that once and keeps collecting Super Chat history and any public chat. It does not mean the public video failed.

1. In Google Cloud, create a project and enable **YouTube Data API v3**.
2. On the OAuth consent screen, leave the app in **Testing** and add the Google account you will sign in with under **Audience → Test users**. If that address is missing, Google shows “Access blocked: [app name] has not completed the Google verification process” (`403 access_denied`). You do not need to submit the app for verification to read your own channel.
3. Create an OAuth client of type **Web application**.
4. Add the redirect URI shown in the app. With the default port it is `http://127.0.0.1:47321/oauth/google/callback`.
5. Paste the client id and secret into the YouTube card, save, and click Connect.

The app requests only `https://www.googleapis.com/auth/youtube.readonly`. The default Data API quota is 10,000 units per day and resets at midnight Pacific Time. `superChatEvents.list`, `liveBroadcasts.list`, and `liveChatMessages.list` cost 1 unit per call. Super Chat history is one page per poll, at least 5 minutes apart, and an unfinished 30-day walk continues from the saved page instead of starting over. Own-channel live chat is polled only while a broadcast is active, and only after YouTube's `pollingIntervalMillis`. Broadcasts are checked every 5 minutes while idle. A `quotaExceeded` response pauses signed-in calls until the next Pacific midnight. Public live chat uses the watch page, not this quota.

### Rumble

The account API at [rumble.com/account/livestream-api](https://rumble.com/account/livestream-api) is only for a channel you own. The key in that URL will not read someone else's stream.

For any public stream, paste the video URL (or the numeric chat id from the watch page) into **Public video URL or chat id**. The app follows `https://web7.rumble.com/chat/api/chat/{video_id}/stream`, which is the chat the player shows to viewers. That includes the rant text and price, gifted-sub notices, and new-subscriber notices. It does not include the dollar amount of a subscription. Add the owner API URL only when you want those amounts for your own channel.

Turn on **Follow the live stream** and paste a channel URL such as `https://rumble.com/c/channel`. While that is on, chat is read only when that channel page shows one live video, using that video's chat id. The owner API URL is not a channel address. The saved video URL is not used until the option is off. The new-ledger prompt uses the same switch for Rumble.

Leave the app running while the stream is live. A rant stays in the chat for its paid duration, and the connection is read continuously so a short notice is not missed between polls.

### Kick

Paste a channel URL or slug into **Public channel**, for example `https://kick.com/channel`. The app joins that channel's public chat. New subscriptions, gifted subscriptions, and Kicks that show up in chat are stored. You do not need to own the channel. Subscription renewals are not announced in chat; those still need the owner webhooks below. Chat stays connected whether or not a broadcast is on. Turn on **Show whether this channel is live** to add live or offline to the status. That does not change which chat is read. A new ledger asks for that channel directly and does not add a follow switch, because the address is already a channel.

1. Create an app at [kick.com/settings/developer](https://kick.com/settings/developer).
2. Register the redirect URI shown in the app (`http://127.0.0.1:47321/oauth/kick/callback` by default).
3. Save the client id and secret here, then click Connect. The requested scopes are `user:read`, `channel:read`, `events:subscribe`, and `kicks:read`.
4. Kick only delivers these events as webhooks. In the Kick app, enable webhooks and set the URL to the webhook value shown in the card. The path on this app is `/kick/webhook`.
5. By default the listener is `127.0.0.1:47322`, which Kick's servers cannot reach. Either set the listen host to `0.0.0.0` and forward that TCP port, or point a tunnel at it and put the public origin in **Public base** (for example `https://your-host:47322`).
6. Click Subscribe after the webhook URL answers. Subscribing before Kick can reach the listener gets the subscription disabled after about a day of failures.

Every webhook is checked with Kick's RSA public key (`GET https://api.kick.com/public/v1/public-key`, with the documented key as fallback) over `message id + "." + timestamp + "." + raw body`, using SHA-256 and PKCS#1 v1.5. Unsigned or stale requests are rejected. The check uses the `openssl` binary so the collector does not need extra Python packages.

## Streamer presets

Open **Streamers** from the menu to save a name, the channel URL for YouTube, Rumble, and Kick, and that streamer's goal tiers. A preset needs at least one channel and can leave the others blank. Goals alone are not enough. Turn goal tiers on to name up to five goals. Saving the same name again replaces those channels and goals. **Edit** on a saved preset fills the form, so the goals can change without retyping the channels. Presets are shared by every ledger. Saving one does not change the ledger that is open.

When you open a new ledger, or open a ledger's addresses again, choose a preset to fill the channel fields. YouTube and Rumble turn on **Follow the live stream**. Kick is filled as the channel. Saving that dialog writes the preset's goal tiers onto that ledger. **None**, then Save, leaves the ledger's goals as they are. You can edit the addresses before Save, or skip and leave them unchanged. Skip still saves nothing. A new ledger starts with goal tiers off and does not copy the previous ledger's goals. Presets and goal tiers are not included in the settings export. Membership price, the extra chat note, and top donors stay in Settings and in that export.

## Ledger

A **Stream** panel on the left plays the open ledger's video. It starts closed, so the ledger stays as it is until you open it. Choose YouTube, Rumble, or Kick. The player uses the address already saved on that ledger. When follow live is on, it uses the broadcast that is on now, and says so if that channel is offline instead of playing an older video. The video frame stays reserved when nothing is playing. Reload checks again. The choice lasts for this window only and is not part of the settings export. The same panel lists read-only chat from YouTube, Rumble, and Kick together, and each line is labeled with that platform's icon. Each line also shows the time the platform gave that message. The clock icon, **Timestamps**, shows or hides those clocks for this window only, and that choice is not part of the settings export. The star beside it, **Giveaway**, opens the drawing. Typing a message is not part of this panel. Those lines are not saved in the ledger and are not part of the settings export.

**Giveaway**, the star beside Timestamps in the Stream panel, opens a prompt for a keyword, such as a hashtag. The app does not type it into chat. Start begins counting messages on the open ledger that are that keyword alone. Case does not matter. Each person counts once on a platform, and the same name on another platform counts again. Repeat messages from the same person on the same platform do not. Leave the timer blank to run until Stop. A timer stops the drawing on its own. Stop, or the end of the timer, picks one winner at random and removes that person from the pool. **Redraw** picks the next person from whoever is left and adds them to the winner list. Someone who has already been drawn is not drawn again. When the pool is empty there is nobody else to draw. Copy announces the keyword, and after the drawing is closed it can also announce the winner. Closing the prompt does not stop a drawing that is running. The drawing is not saved and is not part of the settings export.

The stream total counts every donation on the open ledger. **+** opens a separate ledger. There is no control to restart the total on the ledger you already have. Stream total, top donors, and manual entry start collapsed. The chevron at the right edge of each section opens it, and the one-line summary stays visible while it is closed. That stream-total line includes how far the total is from the next named goal when goal tiers are on. The copy button beside the stream-total chevron copies that total blurb. The copy button beside the top-donors chevron copies the top three donors. A status bar across the bottom of the window shows the same total and goal sentence, the top three, and those copy buttons. While a giveaway timer is running, the right side of that bar shows the time left. That side stays empty until a temporary status has something to show. Manual entry keeps donor, platform, type, and amount in USD visible while it is collapsed, along with +, Paste, and Clear. Paste still fills the form. The collapsed row shows the dollar value, and + saves the pasted amount, currency, and message. Double-click a ledger's title to rename it. Cancel and Escape leave the saved name as it was.

Each ledger has its own soft disconnect: the plug on that ledger's tab, beside close. It is on by default, so a new ledger does not collect until you turn it off. Pausing stops that ledger's API requests and live-chat reading and keeps sign-in and addresses. Other ledgers keep their own setting. Only the open ledger is collected, and only while its pause is off. Hover the tab to use the plug while it is collecting; a paused ledger keeps the red plug visible.

Open a row to read the message and save a note. Previous and Next move a page at a time, and the pager shows the page number rather than the entry count. A donation that just arrived stays highlighted for one minute, then uses the normal row colors: the left edge is the platform, and the background follows the dollar amount, the same scheme as Live Till. Donations are stored as they arrive. The ledger total is the US dollar sum of every donation except individual gift receipts, which would count a gifted membership twice. Set **Membership price** from the menu on the right, under Settings, when a membership or subscription arrives without a price. YouTube and Kick bundles use that price times the number of memberships. YouTube’s “Sent 5 … gift memberships” counts as five. A Rumble bundle uses that price for the first subscription and $5.00 for each additional one, so five subscriptions at $4.99 are $24.99.

Export CSV uses the same filter and these columns: Date, Time, Donor, Amount, Platform, Type, Currency, Amount USD, Message. Date and time are this computer's local time, oldest first. Type is only Super Chat, Super Sticker, or Membership. Rants are Super Chats. Subscriptions and memberships are Membership. Kick coins convert at 100 coins = $1; under $5 is a Super Sticker and $5 or more is a Super Chat. Amount USD uses the cached response from `https://open.er-api.com/v6/latest/USD`. That open endpoint allows about one request an hour and updates its table about once a day, so the app checks again only when the last attempt is more than an hour old. Settings → Update exchange rates checks immediately, unless the provider returned HTTP 429, in which case it waits 20 minutes. The saved document is the response body, not a second copy of the rates. JSONL is still the full stored record. Messages that start with `=`, `+`, `-`, or `@` are prefixed in CSV so a spreadsheet does not treat chat text as a formula.

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
dist/StreamDonations-linux-x64/     Portable Linux folder (Electron, Python, collector)
~/.local/share/stream-donations/    Ledger, tokens, and client secrets (not in the app folder)
```

Ports default to 47321 for the UI and 47322 for Kick webhooks. Override the UI port with `STREAM_DONATIONS_PORT`. Tokens and the Rumble URL are stored in the database and are not returned to the page after you save them.
