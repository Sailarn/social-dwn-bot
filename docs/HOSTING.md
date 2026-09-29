# Running it somewhere other than a home machine

The short version: **a machine on a home connection is the best place to run this,
and it is not close.** Everything below explains why, and what changes if you move
it anyway.

## Why the network matters more than the hardware

This bot is a thin wrapper over yt-dlp, and yt-dlp's difficulty is not CPU — it is
being allowed to fetch at all. Instagram, TikTok and X rate-limit and bot-check by
IP, and they treat datacenter ranges very differently from residential ones.

On a home connection most posts fetch anonymously without trouble. From a cloud
provider you will meet login walls and throttling far sooner, on the same content.

This also breaks the cookies workaround, rather than fixing it. A session is bound
to the IP it was created from; exporting cookies at home and replaying them from a
datacenter is the pattern platforms watch for, and those sessions die quickly. See
[COOKIES.md](COOKIES.md).

**So: prefer any always-on machine at home** — a Pi, an old laptop, a NAS. It needs
no inbound networking at all (see below), so there is nothing to expose.

## What the bot needs from a host

| Requirement | Why |
|---|---|
| A **long-running process** | It uses long polling: it dials out to Telegram and holds the connection |
| **No inbound access** | No port forwarding, no domain, no TLS, no public IP |
| A **writable directory** | SQLite for the re-send cache and usage counters |
| **ffmpeg** on the PATH | Only for clips that arrive over 50 MB |
| ~250 MB RAM | Measured: ~180 MB idle, ~220 MB peak at concurrency 8 |

The "no inbound" part is what makes this easy to place. Nothing needs to reach the
bot, so it can sit behind NAT with no configuration.

## Options, honestly

### A VPS — works unchanged

Oracle Cloud Always Free, Hetzner, or anything similar. Install Docker, clone,
`docker compose up -d`. Identical to a home machine except for the IP problem
above, which is the whole cost.

### Container hosts — work, with caveats

Fly.io, Render, Railway, Hugging Face Spaces. The bot binds `$PORT` and answers a
health check, so hosts that kill a service without a listening port are satisfied.

Two things to get right:

- **Persistence.** `DATA_DIR` must point at a volume, or the re-send cache and
  usage stats reset on every deploy. Nothing breaks — clips are simply fetched
  again — but the bandwidth saving is lost.
- **Sleeping.** Free tiers that idle out will miss messages while asleep. Telegram
  queues updates for a while, so they usually arrive late rather than never.

### Render (free) — the tested container path

`render.yaml` at the repo root is a Blueprint: Dashboard → New → Blueprint → pick
the repo, and fill in the secrets it asks for. No card is needed.

The free web service is 512 MB, 0.1 CPU and 750 hours a month **per workspace**.
One service running all month is ~744 hours, so it fits only if nothing else in
the workspace runs — suspend any other free service there.

What each setting in the blueprint is for:

- **Sleeping.** A free service sleeps after 15 minutes without an *inbound*
  request, and polling is outbound, so it counts as idle. Point an external
  pinger (cron-job.org is free) at `https://<service>.onrender.com/health` every
  10 minutes. `HEARTBEAT_URL` does not help here: it is outbound too, and it is
  the alarm for when the service is down, not the thing that keeps it up.
- **Restarts.** Render restarts and redeploys on its own schedule.
  `DROP_PENDING_UPDATES=false` keeps the links sent meanwhile, so they arrive
  late instead of never. A deploy briefly overlaps old and new instances; the
  new one logs a few `409 Conflict` retries and then takes over.
- **Cookies.** The disk is wiped on every restart, so a cookies file cannot live
  there. Put `base64 -i cookies.txt` (Mac) or `base64 -w0 cookies.txt` (Linux)
  in `COOKIES_B64`; it is written to `DATA_DIR/cookies.txt`, mode 600, at every
  start. Changing it redeploys the service.
- **Instagram.** Render's shared IP gets HTTP 429 from Instagram on every
  anonymous request. With `APIFY_TOKEN` set, those posts go through Apify's
  Instagram scraper (its own proxies, ~10–60 s, credit from Apify's free $5/month)
  and only then to the cookie account (`COOKIES_ON_THROTTLE=true`). The order is
  anonymous → Apify → cookies; `/stats` shows how many each one served.
- **Storage.** The disk is wiped on every deploy, so the re-send cache and the
  stats would start from zero each time. Set `TURSO_DATABASE_URL` and
  `TURSO_AUTH_TOKEN` (Turso's free plan, no card) and both live there instead.
  Pick a Turso region near the Render one; every lookup is a network round trip.
- **Memory.** The memory floor reads the container's cgroup limit, so it refuses
  work before the 512 MB cap OOM-kills the process.
- **CPU.** At 0.1 CPU a re-encode of an over-50 MB clip is slow and can hit the
  15-minute timeout (`reencode_failed`). Most clips never need one.

Remember: before the first deploy finishes, stop whatever else polls this token.

### Serverless — a poor fit, and not for the obvious reason

Vercel Functions, AWS Lambda, Cloud Run and friends are built for short request
handlers. This bot is a process that holds a connection open. You *can* make it
work by switching from long polling to webhooks, and the download itself fits
comfortably inside modern limits, but the shape fights you:

- **Long polling has to go.** Webhooks mean a public HTTPS endpoint and a TLS
  certificate — reintroducing everything the current design avoids.
- **The filesystem is ephemeral.** The SQLite cache and event log need replacing
  with a hosted database. That is the bulk of the porting work.
- **Concurrency is per-invocation.** The rate limiter and pacers are in-process, so
  they stop being global. Two concurrent invocations would each think they hold the
  whole budget — which matters, because pacing is what keeps the IP from being
  throttled.
- **Bandwidth is metered.** Every clip costs roughly twice its size, and egress is
  usually the line item that bites.

None of this is impossible; it is simply a different program. If you want a bot on
serverless, design it for that from the start rather than porting this one.

### What does not work

Anything without a persistent process and outbound network: static hosts, edge
runtimes without a filesystem, and free tiers that cap process lifetime to minutes.

## If you do move it

1. Set `DATA_DIR` to a persistent volume.
2. Expect more `needs a login` failures. Consider cookies, and read the warning in
   [COOKIES.md](COOKIES.md) first.
3. Keep `PLATFORM_INTERVAL_SECONDS` and `PLATFORM_COOLDOWN_SECONDS` at their
   defaults or higher. They matter more from a datacenter, not less.
4. Only one instance may poll a bot token at a time. Stop the old one before
   starting the new one, or Telegram returns a 409 conflict.
