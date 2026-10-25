# Chat server

How the community chat runs, how to get into its server, and how to bring it back. The chat is a
Matrix homeserver for `glyphatlas.org`; Discord, Telegram and Libera.Chat (IRC) are bridged to it.
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
| `cloudflared` | The tunnel `glyphatlas-matrix` | `/etc/cloudflared/config.yml` and the tunnel's credential file beside it |
| `tailscaled` | Tailscale | — |
| `chat-backup.timer` | Nightly backup | `/usr/local/sbin/chat-backup` |

The three bridges declare `Requires=continuwuity.service`: stopping the homeserver stops them, and
starting it does not start them. Start all four together:

```sh
systemctl restart continuwuity mautrix-discord mautrix-telegram heisenbridge
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
databases and configs), `data/heisenbridge/registration.yaml`, and `etc/` (homeserver, tunnel,
systemd units). On a fresh server: install the same software versions, put the files back in the
places listed under *What runs*, start `continuwuity`, then the bridges, then `cloudflared`. The
tunnel's credential file brings `matrix.glyphatlas.org` back without touching DNS.

## Updating

Ubuntu's packages update themselves. These do not, and each release should be read before
upgrading, since their configuration changes between versions:

- continuwuity — a single static binary from its Forgejo releases;
- mautrix-discord and mautrix-telegram — binaries in their directories;
- heisenbridge — `pip install -U heisenbridge` in its venv;
- cloudflared — from Cloudflare's apt repository, so it updates with the system;
- Element at chat.glyphatlas.org — `apps/chat` in this repository, pinned by version and checksum.

Take a backup before upgrading the homeserver.

## Bridged rooms

Each public room in the space has a Discord channel, a Telegram topic and a Libera.Chat channel
(`#glyphatlas` for 歡迎 · Welcome, `#glyphatlas-<room>` for the others). To add a room:

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
