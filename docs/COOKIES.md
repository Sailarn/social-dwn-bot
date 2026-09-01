# Instagram cookies

Optional. Without them the bot works, but Instagram refuses some posts with
`⚠️ that post needs a login to view`.

## Why it is needed

Instagram increasingly puts a login wall in front of **public** content — not just private
accounts. yt-dlp then gets an empty response and there is nothing to download. A logged-in
session gets past that wall.

## The one rule that makes this safe

**Use a dedicated account, and never let it follow anyone.**

The bot fetches as whatever account the cookies belong to. Anyone who can use the bot can
therefore reach anything that account can see. If the account follows nobody and has no
private access, that is exactly the public content the bot already serves, so an open bot
gives away nothing extra. The moment that account follows a private account, everything it
can see becomes fetchable by anyone who messages the bot.

So: dedicated account, follows nobody, no private access. Never a personal account.

## How the bot uses them

**Anonymous first.** Every request is made without cookies. Only if the post fails for a
reason a session could actually fix — `needs a login`, `private`, `age-restricted` — is it
retried with the cookies.

That matters more than it sounds. Sustained authenticated API traffic is what gets a
scraping account flagged and eventually disabled. Most posts work anonymously, so the
session is spent only on the handful that need it, and the account stays quiet.

Failures cookies cannot fix — a network timeout, a clip over the length limit, a deleted
post — are never retried with them.

The log says when it happens:

```
INFO socialdl [a3f21c]: retrying https://instagram.com/reel/X/ with cookies (needs_login)
```

Media URLs from an authenticated request are bound to that session, so if the metadata came
from a cookie'd request the download uses cookies too. That is handled automatically.

## Setup

1. **Create a second Instagram account.** Do not use it for anything else.

2. **Let it age a few days.** A brand-new account that immediately starts fetching gets
   flagged. Open the app, scroll, behave like a person for a bit first.

3. **Log in from the same network as the bot.** Sessions are bound to the IP they were
   created from; one created elsewhere and replayed from the Pi is invalidated much faster.
   This is the same reason Instagram is harder from a datacenter than from home.

4. **Export the cookies in Netscape format.** A browser extension such as
   *Get cookies.txt LOCALLY* does this — export while logged in to Instagram. The file
   starts with `# Netscape HTTP Cookie File`.

5. **Copy it to the machine running the bot and lock it down:**

   ```bash
   scp cookies.txt user@host:~/social-download-tg/data/cookies.txt
   ssh user@host 'chmod 600 ~/social-download-tg/data/cookies.txt'
   ```

6. **Point the bot at it** in `.env`, then restart:

   ```
   COOKIES_FILE=data/cookies.txt
   ```

7. **Check the startup log says it loaded:**

   ```
   INFO socialdl [-]: cookies loaded from data/cookies.txt (0 days old)
   ```

   If the path is wrong you get a warning instead of silence, and the bot keeps running
   without cookies.

## Maintenance

Sessions expire. The bot warns once they pass 30 days:

```
WARNING socialdl [-]: cookies are 34 days old; if Instagram starts refusing posts, export them again
```

The symptom of expiry is `needs a login` returning for posts that used to work. Re-export
and restart; nothing else changes.

## Security

- `chmod 600`. The file is equivalent to that account's password.
- Never commit it. `data/` is already in `.gitignore`.
- If it leaks, change that account's password, which invalidates the session.
- `EVENT_SALT` and the bot token live in `.env`; the cookies file is separate so it can be
  replaced without touching anything else.

## What it does and does not fix

| Fixes | Does not fix |
|---|---|
| Login-walled public posts | Posts from accounts the bot's account cannot see |
| Some age-restricted content | Deleted posts |
| Occasional rate-limiting | yt-dlp being out of date |
