# Stream Donations

A local Electron app that records donation events from your own YouTube, Rumble, and Kick channels and keeps them in a SQLite ledger you can filter, annotate, mark processed, and export.

The window is only a front end. A Python collector talks to each service and writes `data/donations.db`.

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

Nothing is installed with rpm-ostree. After a successful build, start the app with:

```sh
bin/stream-donations
```

The ledger is created at `data/donations.db` (mode 0600). The same database is what the export commands and any SQLite client read.

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

The Google sign-in below is only for your own channel’s Super Chat history from the last 30 days (`superChatEvents.list`). Skip it if you only want a public stream. If that account has never enabled live streaming, YouTube answers `liveBroadcasts` with `403 The user is not enabled for live streaming`. The app records that once and keeps collecting Super Chat history and any public chat. It does not mean the public video failed.

1. In Google Cloud, create a project and enable **YouTube Data API v3**.
2. On the OAuth consent screen, leave the app in **Testing** and add the Google account you will sign in with under **Audience → Test users**. If that address is missing, Google shows “Access blocked: [app name] has not completed the Google verification process” (`403 access_denied`). You do not need to submit the app for verification to read your own channel.
3. Create an OAuth client of type **Web application**.
4. Add the redirect URI shown in the app. With the default port it is `http://127.0.0.1:47321/oauth/google/callback`.
5. Paste the client id and secret into the YouTube card, save, and click Connect.

The app requests only `https://www.googleapis.com/auth/youtube.readonly`. Default quota is 10,000 units per day. `superChatEvents.list` and `liveChatMessages.list` cost 1 unit per call. Live chat is polled only while a broadcast is active, and only as often as YouTube's `pollingIntervalMillis` asks.

### Rumble

The account API at [rumble.com/account/livestream-api](https://rumble.com/account/livestream-api) is only for a channel you own. The key in that URL will not read someone else's stream.

For any public stream, paste the video URL (or the numeric chat id from the watch page) into **Public video URL or chat id**. The app follows `https://web7.rumble.com/chat/api/chat/{video_id}/stream`, which is the chat the player shows to viewers. That includes the rant text and price, gifted-sub notices, and new-subscriber notices. It does not include the dollar amount of a subscription. Add the owner API URL only when you want those amounts for your own channel.

Leave the app running while the stream is live. A rant stays in the chat for its paid duration, and the connection is read continuously so a short notice is not missed between polls.

### Kick

Paste a channel URL or slug into **Public channel**, for example `https://kick.com/channel`. The app joins that channel's public chat. New subscriptions, gifted subscriptions, and Kicks that show up in chat are stored. You do not need to own the channel. Subscription renewals are not announced in chat; those still need the owner webhooks below.

1. Create an app at [kick.com/settings/developer](https://kick.com/settings/developer).
2. Register the redirect URI shown in the app (`http://127.0.0.1:47321/oauth/kick/callback` by default).
3. Save the client id and secret here, then click Connect. The requested scopes are `user:read`, `channel:read`, `events:subscribe`, and `kicks:read`.
4. Kick only delivers these events as webhooks. In the Kick app, enable webhooks and set the URL to the webhook value shown in the card. The path on this app is `/kick/webhook`.
5. By default the listener is `127.0.0.1:47322`, which Kick's servers cannot reach. Either set the listen host to `0.0.0.0` and forward that TCP port, or point a tunnel at it and put the public origin in **Public base** (for example `https://your-host:47322`).
6. Click Subscribe after the webhook URL answers. Subscribing before Kick can reach the listener gets the subscription disabled after about a day of failures.

Every webhook is checked with Kick's RSA public key (`GET https://api.kick.com/public/v1/public-key`, with the documented key as fallback) over `message id + "." + timestamp + "." + raw body`, using SHA-256 and PKCS#1 v1.5. Unsigned or stale requests are rejected. The check uses the `openssl` binary so the collector does not need extra Python packages.

## Process the ledger

In the window, filter to **Waiting**, open a row, add a note, and mark it processed. **Mark loaded processed** updates only the rows on the current page. The ledger total is the US dollar sum of every donation except individual gift receipts, which would count a gifted membership twice. Set **Membership price** on the Settings tab when a membership or subscription arrives without a price. YouTube and Kick bundles use that price times the number of memberships. YouTube’s “Sent 5 … gift memberships” counts as five. A Rumble bundle uses that price for the first subscription and $5.00 for each additional one, so five subscriptions at $4.99 are $24.99.

Export CSV uses the same filter and these columns: Date, Time, Donor, Amount, Platform, Type, Currency, Amount USD, Message. Date and time are this computer's local time, oldest first. Type is only Super Chat, Super Sticker, or Membership. Rants are Super Chats. Subscriptions and memberships are Membership. Kick coins convert at 100 coins = $1; under $5 is a Super Sticker and $5 or more is a Super Chat. Amount USD uses the cached response from `https://open.er-api.com/v6/latest/USD`. That open endpoint allows about one request an hour and updates its table about once a day, so the app checks again only when the last attempt is more than an hour old. Settings → Update exchange rates checks immediately, unless the provider returned HTTP 429, in which case it waits 20 minutes. The saved document is the response body, not a second copy of the rates. JSONL is still the full stored record. Messages that start with `=`, `+`, `-`, or `@` are prefixed in CSV so a spreadsheet does not treat chat text as a formula.

From a shell, against the same database:

```sh
PYTHONPATH=source/collector python3 -m streamdonations export csv -o data/exports.csv
PYTHONPATH=source/collector python3 -m streamdonations export jsonl --processed 0
PYTHONPATH=source/collector python3 -m streamdonations poll
```

`poll` checks YouTube and Rumble once. Kick donations still arrive only through the webhook listener, so use `serve` for those.

```sql
SELECT created_at, platform, kind, author_name, amount_value, currency, message
FROM donations
WHERE processed = 0
ORDER BY created_at;
```

`sqlite3 data/donations.db` runs that query. After you have handled a row outside the app, set `processed` back in the UI or with SQL.

## Layout

```
source/collector/streamdonations/   Python collector, ledger, and the page the window loads
source/electron/                    Electron main process
bin/stream-donations                Built launcher
bin/app/                            Electron shell, produced by the Node image
bin/collector/                      Collector copied into the build
data/donations.db                   The ledger
```

Ports default to 47321 for the UI and 47322 for Kick webhooks. Override the UI port with `STREAM_DONATIONS_PORT`. Tokens and the Rumble URL are stored in the database and are not returned to the page after you save them.
