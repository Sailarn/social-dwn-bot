# What it supports

Post a link in a chat, get the media back as a real Telegram video or photo.

## Platforms

| | Videos | Images | Notes |
|---|---|---|---|
| **Instagram** | yes | yes | Posts and reels. Short links work |
| **X / Twitter** | yes | yes | `x.com`, `twitter.com` and `t.co` links |
| **TikTok** | yes | — | Including `vm.tiktok.com` / `vt.tiktok.com` short links |
| **YouTube** | no | no | Excluded on purpose: Telegram already plays YouTube links |

Everything above has been confirmed working on real posts.

**Implemented but not yet seen in the wild:** multi-item carousels (a post with
several photos, or photos plus a video) arrive as one Telegram album. The code is
tested, but no real carousel has been through it yet. TikTok photo slideshows are
untested and may not work at all.

## Limits

| | |
|---|---|
| Video size | **50 MB** — Telegram's cap for bots. Larger clips are shrunk to fit |
| Video length | **5 minutes** — longer posts are refused without downloading |
| Photo size | **10 MB** per image |
| Album | **10 items** per post, 50 MB total |
| Per person | **30 downloads an hour** |

## Behaviour worth knowing

- **A repeat link is instant.** Anything sent in the last 30 days comes back
  immediately without being downloaded again.
- **Nothing is stored.** The file is deleted as soon as Telegram has it.
- **Silence means unsupported.** A message with no supported link gets no reply.
- **Some Instagram posts need a login.** With a cookies file configured these
  work; without one they are refused with a one-line message. See
  [COOKIES.md](COOKIES.md).

## Commands

| | |
|---|---|
| `/help` | What the bot does |
| `/chatid` | This chat's ID, for the allow-lists |

Admin only, if `ADMIN_USER_IDS` is set:

| | |
|---|---|
| `/stats` | Usage over 24h / 7d / 30d, and what has been failing |
| `/errors` | Unique errors, each with an id to look up |
| `/health` | Uptime, memory, disk, yt-dlp version |

## If something breaks

The owner gets one Telegram message the **first** time a new kind of failure
happens — so a platform breaking is a single alert, not one per post. Repeats of
the same fault are counted silently and shown by `/errors`.

## When it can't

Every failure gets one short line, never a wall of text:

| | |
|---|---|
| `⚠️ that post needs a login to view` | Instagram wants a session |
| `⚠️ that post is private` | The account is private |
| `⚠️ that post is gone or unavailable` | Deleted or removed |
| `⚠️ clip is 8m30s, limit is 5m00s` | Too long |
| `⚠️ no video or image in that post` | Nothing to download |
| `⚠️ the site is not responding properly, try again shortly` | The platform is throttling us |
| `⚠️ rate limit reached, try again in N min` | Over 30 an hour |

---

Every setting is documented inline in [.env.example](../.env.example).
