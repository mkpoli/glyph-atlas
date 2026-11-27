# Chat server

How the community chat runs, how to get into its server, and how to bring it back. The chat is a
Matrix homeserver for `glyphatlas.org`; Discord, Telegram, QQ and Libera.Chat (IRC) are bridged to it.
Nothing secret is written here: each secret is named by the file that holds it on the maintainer's
machine, under `~/.config/glyph-atlas-matrix/`.

## The server

- A Vultr VPS in Osaka (1 vCPU, 1 GB), Ubuntu 24.04, host name `matrix`.
- **No port is open to the internet.** Web traffic reaches it through a Cloudflare Tunnel, and SSH only
  over Tailscale. Its public IP answers nothing.
- Security updates install themselves every day (`unattended-upgrades`). When one needs a restart, the
  server reboots at 04:00 JST; the chat is gone for about a minute and every service starts again.

## Getting in

1. **Over Tailscale (normal).** The server is the machine `glyphatlas-matrix` in the maintainer's
   tailnet. From a device in the same tailnet:

   ```sh
   ssh -i ~/.ssh/vultr root@glyphatlas-matrix      # or root@$(tailscale ip -4 glyphatlas-matrix)
   ```

   Login is by key only; passwords are refused.

2. **Vultr web console (when Tailscale is down or the key has expired).** In the Vultr dashboard,
   open the server and choose *View Console*. Log in as `root` with the password in
   `vultr-root-console-password`; that password works only on the console. Then either
   `tailscale up` (open the printed link to sign the server in again), or `ufw allow 22` to open SSH
   to the internet for a while, and `ufw delete allow 22` afterwards.

The server's Tailscale key is set never to expire (Tailscale admin console → Machines →
`glyphatlas-matrix` → *Disable key expiry*). Check that it still is after re-adding the machine.

## What runs

| Service | What it is | Where |
|---|---|---|
| `continuwuity` | The homeserver, on `127.0.0.1:6167` | binary `/usr/local/bin/continuwuity`, config `/etc/continuwuity/continuwuity.toml`, database `/var/lib/continuwuity/db` |
| `mautrix-discord` | Discord bridge | `/var/lib/mautrix-discord` (config, registration, SQLite) |
| `mautrix-telegram` | Telegram bridge | `/var/lib/mautrix-telegram` |
| `heisenbridge` | IRC bridge to Libera.Chat | `/var/lib/heisenbridge` (Python venv, registration); its state lives in Matrix account data |
| `qq-bridge` | QQ bridge: the official QQ bot, one group | `/var/lib/qq-bridge` (config, registration, state); program in `/opt/qq-bridge` (source, venv) |
| `ids-bot` | Answers a description with its IDS Workshop page | `/opt/ids-bot/ids_bot.py`; token in `/etc/ids-bot/token`, sync position in `/var/lib/ids-bot/state.json` |
| `fish-bot` | 大肥魚, the community's chat character; files an issue when asked | `/opt/fish-bot/fish_bot.py`; config and secrets in `/etc/fish-bot`, database in `/var/lib/fish-bot` |
| `cloudflared` | The tunnel `glyphatlas-matrix` | `/etc/cloudflared/config.yml` and the tunnel's credential file beside it |
| `tailscaled` | Tailscale | — |
| `chat-backup.timer` | Nightly backup | `/usr/local/sbin/chat-backup` |

The bridges and the two bots declare `Requires=continuwuity.service`: stopping the homeserver stops
them, and starting it does not start them. Start all seven together:

```sh
systemctl restart continuwuity mautrix-discord mautrix-telegram heisenbridge qq-bridge ids-bot fish-bot
```

**The tunnel.** `matrix.glyphatlas.org` is a proxied CNAME to the tunnel. `config.yml` sends
`/mautrix-discord/avatar/*` (avatars Discord fetches for relayed messages) to the Discord bridge on
`127.0.0.1:29334` and everything else to the homeserver. Other servers find the homeserver through
`glyphatlas.org/.well-known/matrix/server`, served by the site. Caddy is installed but disabled; it was
the web server before the tunnel.

**Uploads.** The homeserver refuses requests over 50 MB (`max_request_size`). Cloudflare's own limit
for the free plan is 100 MB, so it never comes into play.

**Link previews.** The homeserver fetches pages for Element's link previews from any site
(`url_preview_domain_contains_allowlist = ["*"]`); private network addresses stay refused by its
default `ip_range_denylist`.

## Sign-in

Logins are OAuth only: the homeserver sends people to glyphatlas.org to sign in, as the OAuth client
`matrix-glyphatlas` (row in the accounts D1 table `oauthClient`; its secret is in `oidc-client-secret`
and `/etc/continuwuity/oidc_client_secret`). A person with an older password account links it at the
first sign-in by entering its user name and old password, or an admin links it in `#admins`:

```text
!admin oidc link @name:glyphatlas.org <site user id>
```

The homeserver reads the site's signing keys at startup; restart `continuwuity` after they change.

**Admin commands** are posted in the room `#admins:glyphatlas.org` by a server admin. The account
`@ops:glyphatlas.org` (token in `ops-token`) is used for scripted ones and is the bridges' relay
account.

## Backups

Every night at 04:30 JST `chat-backup` stops the homeserver for about ten seconds to copy its
database, copies the bridges' SQLite databases online, adds the configuration of every service, and
uploads one archive to the R2 bucket `glyph-atlas-chat-backup`:

- `daily/chat-<time>.tar.zst.age`, deleted by the bucket after 30 days;
- `monthly/…`, the backup of the 1st of each month, kept a year.

Each archive is about 16 MB. It is encrypted with `age` to the public key in
`/etc/chat-backup.recipient`; **the private key is only in `backup-age-key.txt`** on the maintainer's
machine (keep a second copy in a password manager). Neither the server nor R2 can read a backup.
The server's R2 key (`/etc/chat-backup.env`, copy in `chat-backup.env`) can reach this bucket only.

Stopping the homeserver also stops every service that requires it (the bridges and the bots), and
the script starts them again by name, in its `trap` and after the copy. A new service of that kind
goes into both `systemctl start` lines of `/usr/local/sbin/chat-backup`, or it stays down after the
first night.

A failed run posts a notice to the private room 收件 · Inbox. To run one by hand:
`systemctl start chat-backup`.

### Restoring

```sh
rclone copyto r2:glyph-atlas-chat-backup/daily/<file> ./backup.age
age -d -i ~/.config/glyph-atlas-matrix/backup-age-key.txt backup.age | zstd -dc | tar -xf -
```

The archive holds `data/continuwuity-db` (the homeserver database), `data/mautrix-*` (bridge
databases and configs), `data/heisenbridge/registration.yaml`, `data/qq-bridge` (the QQ bridge's
config, registration and state), `opt/qq-bridge/src` (the QQ bridge's program), and `etc/`
(homeserver, tunnel, systemd units). On a fresh server: install the same software versions, put the
files back in the places listed under *What runs*, start `continuwuity`, then the bridges, then
`cloudflared`. The tunnel's credential file brings `matrix.glyphatlas.org` back without touching DNS.

## Updating

Ubuntu's packages update themselves. These do not, and each release should be read before
upgrading, since their configuration changes between versions:

- continuwuity — a single static binary from its Forgejo releases;
- mautrix-discord and mautrix-telegram — binaries in their directories;
- heisenbridge — `pip install -U heisenbridge` in its venv;
- qq-bridge — a Python program in `/opt/qq-bridge` (source in `src`, virtualenv in `venv`); copy the
  changed files in and restart the service;
- ids-bot — `apps/ids-bot/ids_bot.py` in this repository; copy the file to `/opt/ids-bot/ids_bot.py`
  and restart the service;
- cloudflared — from Cloudflare's apt repository, so it updates with the system;
- Element at chat.glyphatlas.org — `apps/chat` in this repository, pinned by version and checksum.

Take a backup before upgrading the homeserver.

## Bridged rooms

Each public room in the space has a Discord channel, a Telegram topic and a Libera.Chat channel
(`#glyphatlas` for 歡迎 · Welcome, `#glyphatlas-<room>` for the others). QQ mirrors the public rooms
in use, apart from 管理 · Admin and 收件 · Inbox; see *QQ* below. To add a room:

1. Create the Matrix room (public, history readable by anyone; the bridge bots at power level 50),
   the Discord channel, and the Telegram topic, and post once in the topic.
2. From the Telegram bridge's direct-message room: `doin <automatic portal> unbridge`, then
   `doin <room> bridge channel:<group>:<topic>` and `doin <room> set-relay`.
3. In the room: `!discord bridge <channel id>`, then `!discord set-relay --create`. A channel created
   after the Discord bridge started needs `systemctl restart mautrix-discord` first. Bridge Telegram
   before Discord: the Discord bridge's replies in an already-bridged room are relayed to Telegram.
4. In the IRC bridge's network room: `PLUMB <room id> #glyphatlas-<room>`, then register the channel
   with ChanServ and remove its secret mode (`MODE #channel -s`).
5. Redact the setup commands from the room.

## QQ

The QQ group [字形大图谱｜Glyph Atlas](https://qm.qq.com/q/bKrGelyYIE) is one chat, so the bridge
mirrors the public rooms in use — 歡迎 · Welcome, English, 中文, 日本語, 한국어, Tiếng Việt, 奇字 ·
Curiosities, 詢問 · Questions, 聯絡 · Feedback, 開發 · Dev, 談話 · Chatting, 投稾 · Posts, 事務 · Affairs and
IDS Workshop — prefixing each text message with its room (`[日本語] name: …`). A message written in QQ
is posted to `#zh:glyphatlas.org`. 管理 · Admin and 收件 · Inbox stay out of QQ.

The bridge is the QQ app `Glyph Atlas Matrix` (app id in `qq-appid`, secret in `qq-app-secret`) and
talks to the homeserver as the appservice `@qqbot:glyphatlas.org`. Its configuration,
`/var/lib/qq-bridge/config.toml`, lists the group and every mirrored room:

```toml
[qq]
app_id = "<app id>"
app_secret = "<app secret>"
group = "<group openid>"
incoming = "#zh:glyphatlas.org"

[[room]]
alias = "#ja:glyphatlas.org"
label = "日本語"
```

`label` is the tag QQ shows; `incoming` is the room QQ messages are posted to. Adding a room means
adding a `[[room]]` entry and restarting `qq-bridge`. In the QQ group, `!qq id` replies with the
group's openid and `!qq status` says the bridge is running.

**Replaying history.** QQ limits how much a bot may push, so history is replayed once, in batches:
several room messages per QQ message, each line stamped with its time (Asia/Shanghai) and room.
Events already carried to QQ are skipped, so a run can be repeated after a failure. Images and files
appear as `[圖片]` and the like in those batches; `media` sends the images themselves afterwards, in
time order, a few seconds apart, skipping the ones QQ already has. Stop the bridge while either runs,
then start it again:

```sh
systemctl stop qq-bridge
qq() {
  runuser -u qq-bridge -- env PYTHONPATH=/opt/qq-bridge/src /opt/qq-bridge/venv/bin/python \
    -m qq_bridge "$@" --config /var/lib/qq-bridge/config.toml
}
qq backfill --dry-run
qq backfill
qq media --dry-run
qq media
systemctl start qq-bridge
```

## IDS bot

`@idsbot:glyphatlas.org` joins every room in the server's public directory and answers a message that
carries an ideographic description with the IDS Workshop page for it: `⿱⿻韦丷吋` gets
https://ids.mkpo.li/⿱⿻韦丷吋. A description must stand between spaces or punctuation, so a sentence
that merely uses the operator (用⿰表示) is left alone. Its answers follow the sync stream, so they
reach the bridged rooms too. Its own messages, edits and messages that already link the Workshop get
no answer.

The program is `apps/ids-bot/ids_bot.py`, standard library only. On the server, make its account and
directory:

```sh
useradd --system --user-group --home /var/lib/ids-bot --shell /usr/sbin/nologin ids-bot
install -d -o ids-bot -g ids-bot /var/lib/ids-bot
install -d -o root -g ids-bot /opt/ids-bot
install -d -m 750 -o root -g ids-bot /etc/ids-bot
```

From a checkout of this repository, copy the program in and give it to the service's group:

```sh
scp apps/ids-bot/ids_bot.py root@glyphatlas-matrix:/opt/ids-bot/ids_bot.py
ssh root@glyphatlas-matrix 'chown root:ids-bot /opt/ids-bot/ids_bot.py; chmod 640 /opt/ids-bot/ids_bot.py'
```

In `#admins`, `!admin users create idsbot` makes the account and prints a password;
`!admin users issue-token idsbot <password>` prints its token. Put the token in
`/etc/ids-bot/token`, owned by root and readable by the `ids-bot` group (the maintainer's copy is
`idsbot-token`), and set the account's display name to `IDS bot`.

The unit is `/etc/systemd/system/ids-bot.service`:

```ini
[Unit]
Description=IDS bot (links descriptions to the IDS Workshop)
After=network-online.target continuwuity.service
Requires=continuwuity.service

[Service]
User=ids-bot
Group=ids-bot
WorkingDirectory=/var/lib/ids-bot
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /opt/ids-bot/ids_bot.py \
  --homeserver http://127.0.0.1:6167 --token-file /etc/ids-bot/token --state /var/lib/ids-bot/state.json
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/var/lib/ids-bot
MemoryMax=100M

[Install]
WantedBy=multi-user.target
```

`systemctl daemon-reload` then `systemctl enable --now ids-bot`. The bot reads the directory again
every hour, so a new public room needs no restart; a room is covered once it is published there.

## 大肥魚

`@bigfatfish:glyphatlas.org`, shown as 大肥魚, is the community's chat character: a whale girl in a
maid dress, after the fan nickname 大肥鱼 for DeepSeek's whale. She joins up to 200 rooms in the
server's public directory, keeps their readable text in a local SQLite log, and answers anyone who
calls her by name (大肥魚, 大肥鱼, 肥魚, 肥鱼, Big Fat Fish), by mention, or with `!fish`, and anyone who replies to one
of her messages, which is how a reply from Discord or Telegram reaches her. The log keeps what each
message replies to, so she sees which message a reply answers: by its number when it is on screen,
and by its sender and opening text when it is older. She introduces herself
only when asked (`!fish 自我介紹`, or 「大肥魚，自我介紹一下」), and only in the rooms listed in
`bot.intro_rooms` (歡迎 · Welcome and 談話 · Chatting); elsewhere she points to them.

She types. Each bubble goes out after the time a quick typist needs for it (five Han characters or
eleven Latin ones a second, six seconds at most, a little random), with the room showing her typing;
while she works on an answer to a call that already counts. The bubbles wait in a queue, so other
rooms are read meanwhile. Typing shows on Matrix. continuwuity 26.9 does not pass typing on to the
bridges; 26.11 does (commit 7ce47be4, 2026-10-06, in its alphas when read on 2026-11-24), and
mautrix-discord then shows it for accounts logged in to it, as hers is. mautrix-telegram v26.10
passes on no typing for relayed accounts, and QQ's bot API has none for groups
(@tencent-connect/qqbot-nodejs 1.0.6, `sendTyping`).

A call is answered once its caller has been quiet for three seconds, ten at most, with typing shown
from the start. A forwarded post or a picture sent right after the call is read in the same turn.

She stays in a conversation. For four minutes after she speaks, she reads each new line in that room
after a pause of a few seconds and decides whether it is for her: a follow-up, a correction such as
"I mean the whole server!", or a thank-you gets an answer or an emoji without anyone naming her
again. Replies to other people are theirs. Three silent looks at the person she was answering, or two
at anyone else, close the conversation, and 40 looks an hour is the cap across rooms. A line she
answers after others have spoken gets a reply mark. The window and caps sit outside the model because
models asked without training when to speak judge it badly (Bhagtani et al.,
[arXiv 2603.11409](https://arxiv.org/abs/2603.11409), 2026; on pauses in voice talk); MaiBot likewise
lets its view frequency fall with each look
(https://github.com/MaiM-with-u/MaiBot, `src/maisaka/turn_trigger`, read at b7f3682, 2026-11-19).

She welcomes newcomers, once each (`[chat] welcome`). A Matrix account or a Telegram member is new when
it joins a public room, a Discord member with the bridge's "joined the server" notice, and anyone else
with a first line. QQ's bot API reports no joins and IRC people speak through one bridge account, so a
QQ member is met on speaking and an IRC one is not. She waits 40 to 120 seconds, so that a join and a
first line make one greeting, and posts it where they spoke, else in a welcome room they joined.
Whoever was in a room when she first read it is not new, nor is a known person's name on a second
account; more than five at once is taken for a bridge syncing its members. `known_people` keeps the
account IDs. People who asked to be forgotten are not greeted.

She also reads along. She opens a room with unread lines at random times, on average 25 minutes after
the first of them (`[chat] visit_minutes`; sooner after a question or a laugh, later when she lately
spoke there, at most `visits_per_hour` in all), and waits for a burst to end. The model then rates
its interest from 0 to 5: it speaks from 3, adds an emoji (or, in the mood, a sticker) from 2, and otherwise only
reads. It reads 20 lines and the pictures not read yet. It may look a fact up before it speaks. She never files from these turns. Inner Thoughts voices a thought once its score passes a threshold (Liu et al.,
[arXiv 2501.00383](https://arxiv.org/abs/2501.00383), 2024; its thresholds were set by trial and
error, as are these) and GroupGPT splits the decision to chime in from the reply
([arXiv 2603.01059](https://arxiv.org/abs/2603.01059), 2026). Each visit's interest and outcome are
in the journal.

Every evening from 21:00 JST she posts a digest in each language room listed in `[digest.rooms]`,
wholly in the language set there, whatever the room's recent messages are written in: what the
repositories in `[digest] repos` merged and released in the last day, and a line for each other
room's discussion, with no one named. A quiet day gets no digest.

She remembers. Once a stretch of a room has ended (a pause of six hours, or half an hour of quiet,
or a minute of quiet after a turn in which she chose to remember it),
the model summarizes it into an episode and edits a set of short notes about the people in it, the
community and herself: it adds, updates or deletes a note against the ones it already holds. A turn
brings in the notes about the people talking and the notes and earlier episodes, from any room, that
share words with the last few lines, ranked by relevance, recency and importance. Episodes and notes
outlive the 180-day log, up to `[memory] days` (365) after they last came up. `!fish forget me`
deletes everything remembered about the sender, every episode they took part in and every note those
episodes wrote, and keeps what they say out of memory, even when she is busy; `!fish remember me`
starts new notes, and what they said in between stays forgotten. A redacted message takes its
episode and that episode's notes with it. Bridges and bots are never remembered, and rooms that
leave the public directory are no longer recalled. Memory calls wait for the chat to be still for a
few seconds, and a stretch the model fails on three times is skipped.

The design follows Mem0's fact edits (Chhikara et al., [arXiv 2504.19413](https://arxiv.org/abs/2504.19413),
2025; a vendor paper whose full-context baseline still scores highest) and Generative Agents' recall
score (Park et al., [arXiv 2304.03442](https://arxiv.org/abs/2304.03442), 2023; relevance there comes
from embeddings, here from SQLite FTS5 over words and CJK character pairs). LoCoMo found extracted
facts retrieve better than raw turns or session summaries (Maharana et al.,
[arXiv 2402.17753](https://arxiv.org/abs/2402.17753), 2024; synthetic two-person dialogues), and
LongMemEval found BM25 a little behind dense retrieval (Wu et al.,
[arXiv 2410.10813](https://arxiv.org/abs/2410.10813), 2024; single-user sessions). MaiBot's person
notes and group summaries (https://github.com/MaiM-with-u/MaiBot, read at b7f3682, 2026-11-19) and
Red-DiscordBot's `mydata forgetme` (https://docs.discord.red/en/latest/red_core_data_statement.html)
are the group-bot precedents.

When someone calls her with a question she cannot answer from the chat, she can look it up first:
the web through Gemini's Google Search grounding (5,000 free queries a month on the paid tier) and
URL context tool (run on Google's side, so the homeserver's loopback address is out of reach;
https://ai.google.dev/gemini-api/docs/google-search and
https://ai.google.dev/gemini-api/docs/url-context), code, issues, pull requests and commits in the
community's repositories through GitHub, and her own memory of the rooms she reads. She gets up to four
rounds of lookups per answer and `[lookup] per_hour` (120) across the bot. MemGPT lets a model page through its memory with tools and reports that it often stops
early (Packer et al., [arXiv 2310.08560](https://arxiv.org/abs/2310.08560), 2023), so the recalled
memory still comes with every turn.

She knows the rooms. Every turn carries the room's alias, creation date, how many people are in it
on each platform, and the Discord, Telegram, QQ and IRC groups it is bridged to; in an answer she can
list the public rooms, read a room's creator and first joins, and list its members, all from the
homeserver. QQ members appear only once they have spoken, because QQ's bot API cannot list a group,
so the QQ group has more people than she sees.

She catches newcomers up. `!fish recap`, asking her for the whole server in plain words, or a set phrase
(「大肥魚，前情提要」, 「我錯過了什麼？」, 「これまでの話」, 「지금까지 요약해 줘」)
answers with a brief of everything she remembers from every room she reads: what the community is
building, the subjects that keep coming back, decisions and recent news, in the language of the
request.

She adds to 珍字集覽 (rare.glyphatlas.org) when someone asks her to, through its MCP endpoint
(`[compendium]`). A call gets its tools when the caller's lines, the line they answer or the six lines
before speak of the compendium or of adding; the match is wide, since without the tools she can only ask
to be called again. Whether an addition was asked for is hers to read from the caller's words, and the
tools that add stay shut for a caller whose newest line says not to. She adds a record or a sighting, at
most ten an hour and four for one person, and never the same one twice in a day. She cites the asker's message as a via, and every addition is written down in the table
`compendium_writes`. Pictures go by public address, so a picture posted only in the chat needs a link.
She cannot change or remove what is there. Without a token she writes under the one anonymous name the
endpoint hands her, kept in the table `settings`.

She knows Unicode from `unicode-18.0.0.json.gz`, built by `make_unicode.py` from the Unicode Character
Database: the block, version and name of every character and the size of every block. Her instructions
hold the basic facts, and a test compares them with the data. After a new version is published, run
`python3 make_unicode.py <version> <release date>` and bring both up to it.

She sees pictures. The log keeps the link of each image and sticker people post, and a turn sends
the newest few among its last lines (four when called, two on a visit) to the model as the
homeserver's thumbnails, 640 pixels wide, or 320 when a long screenshot passes 3 MB at 640.
`gemini-4-hydrogen` reads PNG, JPEG and WebP inline and no GIF, so a GIF goes as a PNG of its first
frame (https://ai.google.dev/gemini-api/docs/image-understanding). A posted picture also counts as a
lively moment for reading along, and memory sees up to four pictures of each stretch it summarizes.

She sends stickers from 大肥鱼日常表情包 by Nobu.Mashiro ([@wr3162](https://x.com/wr3162/status/2122581552219734149),
2026-11-17, offered with "欢迎大家取用！") and the 16 bundled images in `apps/fish-bot/stickers/`. On
start the bot fetches the 16 images from that post and reads the bundled ones from
`/opt/fish-bot/stickers/`, checks each against the hash in `STICKERS` and uploads it to the
homeserver once; a sticker whose image no longer matches is left out. Discord, Telegram and QQ show
them; the QQ bridge sends a sticker as an image. The last eight she sent in a room
rest there, so a talk gets different ones. A draw each turn decides whether she is in the mood for
one (`STICKER_MOOD`); when she is not, she sends one only if someone asks.

When a person asks her to file something, she hands the discussion to the issue desk: it reads the
latest messages, checks the open issues for duplicates, and files in `mkpoli/glyph-atlas` only when
the log names a concrete request. `!fish review` proposes a title and keeps it for a day; `!fish
issue` files that proposal, or reads the room again when none is pending. Filing is capped at five
issues a day and one every ten minutes; model calls are capped per user and across the bot.

Anyone can also ask her for a pull request ("做個 PR", "open a PR for #123"). The desk finds the open
issue or files one, and puts the label `fish-pr` (`draft_label`) on it; a program on a maintainer's
machine takes it from there, and she writes nothing herself. One person may ask once a day and everyone
together three times (`max_drafts_per_day`); an issue that carries the label or one of its later forms
is not asked for again. Every ten minutes she looks at the issues she labelled, and tells the room once
when a draft is up, with its address from the comment on the issue, or that none came of it. After a
week without either she stops looking.

Feedback is filed without anyone asking (`file_feedback`). In any turn, a visit included, she marks
lines that report something broken in, or ask for a change to, the community's sites and tools; the
desk then reads the room, and files when the request is new and it is at least 0.85 sure, posting the
link. For one already open it posts that issue's link, once a day per room. Otherwise it says nothing.
Such readings are paid from the money for unasked work, come at most six a day and one per room every
half hour, and leave two of the day's issues for people who ask.

Draft pull requests are written by `apps/fish-bot/pr_worker.py`, which runs on the maintainer's own machine, not on the server. Every ten minutes
it lists the open issues labelled `fish-pr`, oldest first, and for each one makes a worktree off
`origin/main`, has Codex work on it there, and opens a draft pull request that closes the issue. It
never merges. The label moves to `fish-pr-working`, then to `fish-pr-opened`, `fish-pr-declined` (the
agent found it is not a small fix, and says why on the issue) or `fish-pr-failed`, and a comment on the issue gives the draft's address. Only people who can
label issues can ask, which is the repository's own access rule.

The issue text comes from a public chat, so the agent gets it as data and runs boxed in:

- bubblewrap gives it a small world: the worktree at `/work`, the system's programs (`/usr`, `/nix/store`,
  a few files of `/etc`), and Codex with its own login. No home directory, no other project, no key, no
  `.git`, no `/mnt`, `/tmp` or `/var`, and no path that names the machine's user.
- Codex's `workspace-write` sandbox takes the network from the commands it runs and lets them write
  only in the worktree. It cannot commit or push.
- The worktree's pointer to its repository is hidden from the agent and cannot be replaced; a worktree
  whose pointer changed anyway is deleted unread.
- The worker stages what is left, with git's hooks, filters and file monitor off, and refuses it when it touches
  `.github`, `.husky`, `.gitmodules`, any `.gitattributes` or a file of secrets, adds a symbolic link or
  a submodule, is larger than 40 files or 3,000 added lines, or holds anything that looks like a secret:
  every changed file is read whole, and the agent's own login is looked for plain, reversed, in hex, in
  base64 and in pieces. The commit message, the title, the description and a decline's reason are read
  the same way before they are posted, and lose any line saying what wrote them.
- Three attempts a day (`per_day`), forty minutes each (`minutes`).

Two things the box does not do. The agent's commands can read its own login file, since Codex needs it
there; the checks on what is pushed and posted are what keep it in. And a draft's code runs in the pull
request's checks like any contributor's: those workflows use no secrets and a read-only token, and
`.github` is barred, but read a draft before running its code anywhere else.

The worker leaves a checkout alone whose files pass through a git filter such as LFS, or whose git
settings run a program by a relative path (a filter, a
signing program, an alias), since the agent could put a program there.

Install: copy `pr_worker.py` to `~/.local/share/fish-pr/`, write `~/.config/fish-pr/config.toml` from
`pr-worker.example.toml`, run `pr_worker.py --labels` once, and enable `fish-pr.timer` with
`fish-pr.service` in `~/.config/systemd/user/`. `journalctl --user -u fish-pr` shows each run; what the
agent printed is under `logs/` in the state directory.

The agent has no network, so it can run only the test tools already on the machine. To give it some,
fill a directory once and name it as `tools` in the configuration:

```sh
uv pip install --python /usr/bin/python3 --target ~/.local/share/fish-pr/tools pytest ruff
```

Install with the system's Python, as above, so that no file in the directory names a home directory.
The agent sees the directory read-only at `/opt/tools`, with its `bin` on `PATH` and its packages
importable, and is told to try `python3 -m pytest` and `ruff check`. Add what a repository's tests
import in the same way.

The program is `apps/fish-bot/fish_bot.py`, standard library only. On the server, make its account
and directories:

```sh
useradd --system --user-group --home /var/lib/fish-bot --shell /usr/sbin/nologin fish-bot
install -d -o fish-bot -g fish-bot /var/lib/fish-bot
install -d -o root -g fish-bot /opt/fish-bot /opt/fish-bot/stickers
install -d -m 750 -o root -g fish-bot /etc/fish-bot
```

Copy the program, the stickers, the example configuration and the unit in, then give them to the
service's group:

```sh
scp apps/fish-bot/fish_bot.py apps/fish-bot/config.example.toml apps/fish-bot/fish-bot.service \
  root@glyphatlas-matrix:/tmp/
scp apps/fish-bot/stickers/*.png root@glyphatlas-matrix:/opt/fish-bot/stickers/
scp apps/fish-bot/unicode-*.json.gz root@glyphatlas-matrix:/opt/fish-bot/
ssh root@glyphatlas-matrix 'set -e
  install -o root -g fish-bot -m 640 /tmp/fish_bot.py /opt/fish-bot/fish_bot.py
  chown root:fish-bot /opt/fish-bot/stickers/*.png /opt/fish-bot/unicode-*.json.gz
  chmod 640 /opt/fish-bot/stickers/*.png /opt/fish-bot/unicode-*.json.gz
  install -o root -g fish-bot -m 640 /tmp/config.example.toml /etc/fish-bot/config.toml
  install -o root -g root -m 644 /tmp/fish-bot.service /etc/systemd/system/fish-bot.service
  rm /tmp/fish_bot.py /tmp/config.example.toml /tmp/fish-bot.service'
```

Create the three secret files, owned by `root:fish-bot` and readable only by that group:

```sh
install -m 640 -o root -g fish-bot /dev/null /etc/fish-bot/matrix-token
install -m 640 -o root -g fish-bot /dev/null /etc/fish-bot/gemini-key
install -m 640 -o root -g fish-bot /dev/null /etc/fish-bot/github-token
```

Fill them:

- `matrix-token` — the account's access token. In `#admins`, `!admin users create bigfatfish` makes
  the account and prints a password; `!admin users issue-token bigfatfish <password>` prints a token.
  Set the account's display name to `大肥魚`.
- `gemini-key` — the paid Google AI Studio API key for `gemini-4-hydrogen`.
- `deepseek-key` — the DeepSeek API key, named in `[deepseek]`. It does the work nobody hears, such as
  memory; Gemini does it when DeepSeek fails or is not configured.
- `github-token` — a fine-grained token for `mkpoli/glyph-atlas` with **Issues: read and write** and
  **Metadata: read-only**. No other repository or permission is needed: the same token reads the
  public repositories for lookups. Without this file the bot still chats and looks up the web, but it
  neither files nor reads GitHub.

On Discord she has a bot of her own, the private Discord application 大肥魚 (ID 1573231606041411648)
with the Server Members and Message Content intents, added to the Glyph Atlas server. Her Matrix
account is logged in to mautrix-discord with that bot's token (`login-token bot <token>` sent from
her account in a direct chat with `@discordbot`), so her messages and reactions reach Discord as that
bot. The bridge drops reactions from Matrix accounts without a Discord login, and the relay webhook
it uses for them cannot react. The token lives only in the bridge's database; to replace it, reset it
on the application's Bot page and send the command again.

Check the homeserver, the model key and GitHub read access before enabling the unit:

```sh
runuser -u fish-bot -- python3 /opt/fish-bot/fish_bot.py --config /etc/fish-bot/config.toml --check
```

Then enable it:

```sh
systemctl daemon-reload
systemctl enable --now fish-bot
journalctl -u fish-bot -f
```

Every model, search and fetch call is counted per day (JST) with its tokens, failures and an
estimated cost from the price tables of Gemini 4 Hydrogen (Standard) and DeepSeek Flash, logged once a
day. Google Search queries count as free up to 3,000 a month (Google gives 5,000, shared with the
account's other projects), then at $14 per 1,000. `[budget] daily_usd` is what calls may cost a day on average. A day may also spend what earlier days
saved, up to one day's worth, and borrow one day's worth from the next, so a busy evening is not cut off
and a quiet day pays for it. A call sets $0.05 aside before it is sent and is settled at its real cost
(one that costs more may pass the limit by the difference). With nothing left no call is sent until
midnight JST. Her unasked visits have
their own part of it, let out as people write through the day by the hours of the last two weeks. The last
$0.35 is kept for people who call her: below it she makes no visits and no digest, reads 15 lines and one
picture, and leaves bystanders in a conversation alone; with none left she tells a room that calls her, once
a day, and keeps the call. Once there is money again, or the model answers again after a failure, she
answers everything missed in a room in one reply, one room at a time while the chat is still; a missed
call is kept 18 hours. A
client without a purse sends nothing at all, and only the process systemd runs as `fish-bot.service`
may call a model: nothing that costs money is ever tested, by anyone or any agent. The last week is
printed by

```sh
runuser -u fish-bot -- python3 /opt/fish-bot/fish_bot.py --db /var/lib/fish-bot/chat.sqlite3 --usage
```

The database is `/var/lib/fish-bot/chat.sqlite3`; text older than 180 days is deleted. The bot reads
the public directory again every hour, so a new room needs no restart. A room that leaves the public
directory is no longer read, though the account remains joined. The database uses WAL: copy it with
`sqlite3 chat.sqlite3 ".backup /var/lib/fish-bot/chat-backup.sqlite3"` rather than copying the live
file.
