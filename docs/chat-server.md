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
| `fish-bot` | Reads the public rooms and files an issue when a discussion calls for one | `/opt/fish-bot/fish_bot.py`; config and secrets in `/etc/fish-bot`, database in `/var/lib/fish-bot` |
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

## Issue bot

`@bigfatfish:glyphatlas.org` joins every room in the server's public directory and follows the sync
stream. It keeps the readable text of those rooms in a local SQLite log and answers a mention or
`!fish` with one decision: file an issue in `mkpoli/glyph-atlas`, or say why not. It reads the latest
messages, checks the open issues for duplicates, and files only when the log names a concrete request
and someone asks for action. The bot only sees rooms it has joined; the public directory decides
which rooms those are.

`!fish review` proposes a title and keeps it for a day. `!fish issue` files that proposal, or reads
the room again when none is pending. Automatic filing is capped at five issues a day and one every
ten minutes. Starting the program with `--dry-run` answers `[dry run] Would file: …` and writes
nothing to GitHub.

The program is `apps/fish-bot/fish_bot.py`, standard library only. On the server, make its account
and directories:

```sh
useradd --system --user-group --home /var/lib/fish-bot --shell /usr/sbin/nologin fish-bot
install -d -o fish-bot -g fish-bot /var/lib/fish-bot
install -d -o root -g fish-bot /opt/fish-bot
install -d -m 750 -o root -g fish-bot /etc/fish-bot
```

Copy the program, the example configuration and the unit in, then give them to the service's group:

```sh
scp apps/fish-bot/fish_bot.py apps/fish-bot/config.example.toml apps/fish-bot/fish-bot.service \
  root@glyphatlas-matrix:/tmp/
ssh root@glyphatlas-matrix '
  install -o root -g fish-bot -m 640 /tmp/fish_bot.py /opt/fish-bot/fish_bot.py
  install -o root -g fish-bot -m 640 /tmp/config.example.toml /etc/fish-bot/config.toml
  install -o root -g root -m 644 /tmp/fish-bot.service /etc/systemd/system/fish-bot.service
  rm /tmp/fish_bot.py /tmp/config.example.toml /tmp/fish-bot.service'
```

Create the three secret files, owned by `root:fish-bot` and readable only by that group:

```sh
install -m 640 -o root -g fish-bot /dev/null /etc/fish-bot/matrix-token
install -m 640 -o root -g fish-bot /dev/null /etc/fish-bot/deepseek-key
install -m 640 -o root -g fish-bot /dev/null /etc/fish-bot/github-token
```

Fill them:

- `matrix-token` — the account's access token. In `#admins`, `!admin users create bigfatfish` makes
  the account and prints a password; `!admin users issue-token bigfatfish <password>` prints a token.
  Set the account's display name to `Big Fat Fish`.
- `deepseek-key` — the API key for the model, starting `sk-`.
- `github-token` — a fine-grained token for `mkpoli/glyph-atlas` with **Issues: read and write** and
  **Metadata: read-only**. No other repository or permission is needed. Without this file the bot
  still reads and decides, but it does not file.

Check the homeserver, the model and GitHub before enabling the unit:

```sh
runuser -u fish-bot -- python3 /opt/fish-bot/fish_bot.py --config /etc/fish-bot/config.toml --check
```

Then enable it:

```sh
systemctl daemon-reload
systemctl enable --now fish-bot
journalctl -u fish-bot -f
```

The database is `/var/lib/fish-bot/chat.sqlite3`; text older than 180 days is deleted. The bot reads
the public directory again every hour, so a new room needs no restart. A room that is not in the
public directory is left alone.
