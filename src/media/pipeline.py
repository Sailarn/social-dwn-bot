"""The order a post is tried in, and when to move on to the next way.

    yt-dlp anonymous  →  Apify (Instagram)  →  yt-dlp with cookies

Anonymous is free and fast, and on a home connection it is usually enough.
Apify gets past a datacenter IP that Instagram throttles on sight, without
spending the cookie account. Cookies come last because every authenticated
request spends the account's trust, and some posts need them anyway.

Only the anonymous step is retried. The later steps run once each: retrying
Apify costs another minute and more credit for the same answer, and a failure
of either just moves on.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from src.core.config import Config
from src.core.errors import ClipRejected, ClipUnavailable, MediaError
from src.core.models import ClipInfo, DownloadedItem, Source
from src.core.retry import with_retries
from src.media import apify, extract, fetch
from src.media.links import platform_of

log = logging.getLogger(__name__)

Alert = Callable[[str], Awaitable[object]]

# Anonymous failures a different route could get past. Apify fetches through
# its own proxies, so a throttle on our IP is exactly what it is for.
APIFY_HELPS_WITH = frozenset({"needs_login", "private", "age_restricted",
                              "site_throttled"})


def _apify_could_help(failure: MediaError, url: str, config: Config) -> bool:
    return apify.applies_to(url, config) and failure.reason in APIFY_HELPS_WITH


def _worth_showing(current: MediaError, candidate: MediaError) -> MediaError:
    """A later step's definite answer ("too long", "private") beats the first
    failure; its own breakdown ("apify failed") does not."""
    return candidate if isinstance(candidate, ClipRejected) else current


async def _apify_step(url: str, config: Config, alert: Alert) -> ClipInfo:
    try:
        return await asyncio.to_thread(apify.probe, url, config)
    except apify.CreditsExhausted as error:
        if error.first_time:
            await alert(
                "💳 Apify credits are used up for this month. Instagram falls "
                "back to cookies until the 1st.")
        raise


async def _cookies_step(url: str, config: Config) -> ClipInfo:
    log.info("trying %s with cookies", url)
    return await asyncio.to_thread(extract.probe, url, config, with_cookies=True)


async def resolve(url: str, config: Config, alert: Alert) -> ClipInfo:
    """Metadata for the post, from the first step that can read it."""
    try:
        return await with_retries(
            lambda: extract.probe(url, config), config.download_attempts, "probe")
    except MediaError as error:
        failure = error

    steps = []
    if _apify_could_help(failure, url, config):
        steps.append(lambda: _apify_step(url, config, alert))
    if extract.cookies_could_help(failure.reason, url, config):
        steps.append(lambda: _cookies_step(url, config))

    shown = failure
    for step in steps:
        try:
            return await step()
        except MediaError as error:
            log.info("%s: next step failed too (%s)", platform_of(url), error)
            shown = _worth_showing(shown, error)
            if isinstance(error, ClipRejected):
                break
    raise shown


async def download(url: str, info: ClipInfo, workdir: Path,
                   config: Config) -> tuple[ClipInfo, list[DownloadedItem]]:
    """The files, and the info they came from — which changes if the Apify
    media would not download and cookies had to be used instead."""
    try:
        return info, await _download_into(url, info, workdir, config)
    except ClipUnavailable as error:
        if info.source != Source.APIFY or not config.cookies_file:
            raise
        log.info("apify's media did not download (%s); trying with cookies", error)

    cookie_info = await _cookies_step(url, config)
    return cookie_info, await _download_into(url, cookie_info, workdir, config)


async def _download_into(url: str, info: ClipInfo, workdir: Path,
                         config: Config) -> list[DownloadedItem]:
    """A directory per source, so a failed attempt's partial files are never
    mistaken for the next attempt's."""
    destination = workdir / info.source
    destination.mkdir(parents=True, exist_ok=True)
    return await with_retries(
        lambda: fetch.download_items(url, info, destination, config),
        config.download_attempts, "download")
