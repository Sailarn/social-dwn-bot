# social-download-tg

A Telegram bot that turns a social media link into the actual video or photo, in
the chat, as a real Telegram attachment rather than a preview.

Post an Instagram, X or TikTok link; the bot replies with the media. It is built
to run on a small always-on machine at home — a Raspberry Pi, an old laptop — with
no inbound networking, no domain and no TLS certificate.

```
you:  https://www.instagram.com/reel/Cx1y2z3AbCd/
bot:  [video]
```

## What it supports

| | Videos | Images |
|---|---|---|
| **Instagram** | yes | yes |
| **X / Twitter** | yes | yes |
| **TikTok** | yes | — |
| **YouTube** | no | no |

YouTube is excluded on purpose: Telegram already embeds a working player for it, so
downloading a copy earns nothing.

**Limits:** 50 MB per video (Telegram's cap for bots — larger clips are re-encoded
to fit), 5 minutes per clip, 10 MB per image, 10 items per album, 30 downloads an
hour per person.

Full list, including the exact failure messages: [docs/FEATURES.md](docs/FEATURES.md).

## Why long polling

The bot dials **out** to Telegram and holds the connection. Nothing ever connects
in, which means no port forwarding, no tunnel, no domain, no certificate and no
public IP. It runs behind NAT with no configuration, which is what makes a machine
at home a practical place to put it.

That choice also keeps it portable: the same process runs on a Pi, a VPS or a
container host without changing a line. See
[docs/HOSTING.md](docs/HOSTING.md) for the trade-offs of moving it off a home
connection — the short version is that a residential IP is the single biggest
advantage this design has.

## Quickstart

1. **Create a bot.** Message [@BotFather](https://t.me/BotFather) → `/newbot`. Keep
   the token it gives you.
2. **For group use**, turn off Group Privacy in BotFather → *Bot Settings*, so the
   bot can see links that are not addressed to it.
3. **Install and run:**

```bash
git clone <your-fork> social-download-tg
cd social-download-tg
cp .env.example .env          # put BOT_TOKEN in it
./deploy/install-pi.sh        # installs ffmpeg, builds a venv, starts under PM2
```

That is it. Add the bot to a group and post a link.

Prefer Docker? `docker compose up -d --build`. Prefer systemd? See
`deploy/social-download-tg.service`.

## ⚠️ Read this before adding it to a group

**By default the bot answers everyone.** Anyone who discovers its username can make
it download, and that traffic leaves from *your* IP address. If somebody abuses it,
the platforms throttle **you**, not them.

That default exists so the bot works with zero configuration, which is genuinely
convenient. If your bot is not private, restrict it:

```bash
ALLOWED_USER_IDS=123456789        # specific people, anywhere
ALLOWED_CHAT_IDS=-1001234567890   # anyone in these groups; /chatid prints the id
```

Either list restricts the bot; setting one is enough. `RATE_LIMIT_PER_HOUR`
(30 by default) is the only brake while both are empty.

Everything else is configured through `.env` — every setting is documented inline
in [.env.example](.env.example).

## The machine it runs on

Developed and running in production on:

| | |
|---|---|
| Board | Raspberry Pi 4 Model B Rev 1.4 |
| CPU | 4 × Cortex-A72 (arm64) |
| Memory | 4 GB |
| Storage | 117 GB SSD over USB |
| OS | Debian 13 (trixie), kernel 6.12 |
| Runtime | Python 3.13, ffmpeg 7.1 |

It shares that Pi with another web application, which shaped several decisions:
re-encodes are serialised and niced, and the bot refuses work when the machine is
low on disk or memory rather than taking its neighbour down with it.

Measured on that hardware: ~180 MB resident, a typical clip delivered in 2–4
seconds, and no measurable effect on the co-hosted app's response times during
downloads.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m pytest                 # ~3s, no network access
.venv/bin/python -m pytest --run-network   # also hits real sites
.venv/bin/ruff check .
.venv/bin/python scripts/loadtest.py all   # load tests; stubs both network edges
```

The test suite never touches the network by default: extraction runs against
recorded yt-dlp results, and Telegram is faked. The load tests stub both edges too,
so they cannot get your IP throttled.

| Path | |
|---|---|
| `bot.py` | Entry point, deliberately tiny |
| `src/core/` | Config, errors, models, retry, tracing, resource guards |
| `src/media/` | Link matching, extraction, downloading, re-encoding |
| `src/storage/` | Re-send cache, rate limiting, usage stats |
| `src/telegram/` | Handlers, delivery, admin commands |

## Keeping it working

yt-dlp breaks whenever a platform changes its player, and that is by far the most
likely reason this bot ever stops working. `deploy/update-ytdlp.sh` upgrades it and
restarts the bot only if the version actually changed. Run it daily:

```
0 5 * * * /path/to/social-download-tg/deploy/update-ytdlp.sh >> /path/to/data/ytdlp-update.log 2>&1
```

## Notes

This is a personal-use tool. You are responsible for how you use it: respect the
platforms' terms and other people's copyright. Downloading media does not give you
the right to redistribute it.

The actual extraction is done by [yt-dlp](https://github.com/yt-dlp/yt-dlp); this
project is a Telegram front end around it, and is not affiliated with or endorsed
by any platform.

## Licence

MIT — see [LICENSE](LICENSE). Free to use, modify and redistribute.
