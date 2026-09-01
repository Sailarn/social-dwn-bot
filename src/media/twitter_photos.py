"""Photos from X, which yt-dlp does not expose.

yt-dlp reaches the tweet but returns no formats and no thumbnails for a
photo-only post, so the images have to come from somewhere else. Two sources,
tried in order:

1. `cdn.syndication.twimg.com` — X's own endpoint, the one embedded tweets use.
   First-party and anonymous. The `token` parameter is not validated; any
   non-empty value works, an empty one returns no media.
2. `api.fxtwitter.com` — a third-party mirror, used only if the first fails.
"""

import json
import logging
import re
import urllib.request

log = logging.getLogger(__name__)

TWEET_ID_PATTERN = re.compile(r"/status(?:es)?/(\d+)")
SYNDICATION_TOKEN = "a"  # any non-empty value is accepted
# pbs.twimg.com serves a downscaled variant by default: a 3840x1080 image comes
# back as 1200x338 unless the original is asked for by name.
ORIGINAL_SIZE_QUERY = "name=orig"
REQUEST_TIMEOUT_SECONDS = 20
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def tweet_id_from(url: str) -> str | None:
    match = TWEET_ID_PATTERN.search(url or "")
    return match.group(1) if match else None


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_USER_AGENT})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read())


def _full_size(url: str) -> str:
    if "name=" in url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{ORIGINAL_SIZE_QUERY}"


def _from_syndication(tweet_id: str) -> list[str]:
    payload = _get_json(
        f"https://cdn.syndication.twimg.com/tweet-result"
        f"?id={tweet_id}&token={SYNDICATION_TOKEN}&lang=en"
    )
    return [_full_size(photo["url"])
            for photo in (payload.get("photos") or []) if photo.get("url")]


def _from_fxtwitter(tweet_id: str) -> list[str]:
    payload = _get_json(f"https://api.fxtwitter.com/status/{tweet_id}")
    media = (payload.get("tweet") or {}).get("media") or {}
    return [_full_size(photo["url"])
            for photo in (media.get("photos") or []) if photo.get("url")]


def photo_urls(url: str) -> list[str]:
    """Best-effort. Returns an empty list rather than raising: the caller has a
    perfectly good "no media" message already."""
    tweet_id = tweet_id_from(url)
    if not tweet_id:
        return []

    for source, fetch in (("syndication", _from_syndication),
                          ("fxtwitter", _from_fxtwitter)):
        try:
            photos = fetch(tweet_id)
        except Exception as error:  # noqa: BLE001 - any failure just tries the next
            log.info("x photos via %s failed for %s: %s", source, tweet_id, error)
            continue
        if photos:
            log.info("x photos via %s: %d for %s", source, len(photos), tweet_id)
            return photos
    return []
