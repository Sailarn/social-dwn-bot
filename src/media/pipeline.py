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


class Attempt:
    """One post's way through the steps. `trail` records every step tried, in
    order — including ones that failed, since a failed Apify run still costs
    credit and a failed cookie request still spends the account."""

    def __init__(self, url: str, config: Config, alert: Alert):
        self.url = url
        self.config = config
        self.alert = alert
        self.trail: list[str] = []

    async def resolve(self) -> ClipInfo:
        """Metadata for the post, from the first step that can read it."""
        failure = await self._anonymous_or_skip()
        if isinstance(failure, ClipInfo):
            return failure

        steps = []
        if _apify_could_help(failure, self.url, self.config):
            steps.append(self._apify)
        if extract.cookies_could_help(failure.reason, self.url, self.config):
            steps.append(self._cookies)

        shown = failure
        for step in steps:
            try:
                return await step()
            except MediaError as error:
                log.info("%s: next step failed too (%s)", platform_of(self.url), error)
                shown = _worth_showing(shown, error)
                if isinstance(error, ClipRejected):
                    break
        raise shown

    async def _anonymous_or_skip(self) -> ClipInfo | MediaError:
        """The anonymous step's result, or its failure for the next steps to go on.

        Skipped for platforms listed in SKIP_ANONYMOUS_PLATFORMS when Apify can
        take over — on a datacenter IP Instagram answers every anonymous request
        with a 429, so asking costs a second or two for nothing. Skipping reads as
        that throttle, so the later steps behave exactly as if it had happened.
        """
        platform = platform_of(self.url)
        if (platform in self.config.skip_anonymous_platforms
                and apify.applies_to(self.url, self.config)):
            return ClipUnavailable(f"anonymous skipped for {platform}", "site_throttled")
        self.trail.append(Source.ANONYMOUS)
        try:
            return await with_retries(
                lambda: extract.probe(self.url, self.config),
                self.config.download_attempts, "probe")
        except MediaError as error:
            return error

    async def download(self, info: ClipInfo,
                       workdir: Path) -> tuple[ClipInfo, list[DownloadedItem]]:
        """The files, and the info they came from — which changes if the Apify
        media would not download and cookies had to be used instead."""
        try:
            return info, await self._download_into(info, workdir)
        except ClipUnavailable as error:
            if info.source != Source.APIFY or not self.config.cookies_file:
                raise
            log.info("apify's media did not download (%s); trying with cookies", error)

        cookie_info = await self._cookies()
        return cookie_info, await self._download_into(cookie_info, workdir)

    async def _apify(self) -> ClipInfo:
        self.trail.append(Source.APIFY)
        try:
            return await asyncio.to_thread(apify.probe, self.url, self.config)
        except apify.CreditsExhausted as error:
            if error.first_time:
                await self.alert(
                    "💳 Apify credits are used up for this month. Instagram falls "
                    "back to cookies until the 1st.")
            raise

    async def _cookies(self) -> ClipInfo:
        self.trail.append(Source.COOKIES)
        log.info("trying %s with cookies", self.url)
        return await asyncio.to_thread(
            extract.probe, self.url, self.config, with_cookies=True)

    async def _download_into(self, info: ClipInfo,
                             workdir: Path) -> list[DownloadedItem]:
        """A directory per source, so a failed attempt's partial files are never
        mistaken for the next attempt's."""
        destination = workdir / info.source
        destination.mkdir(parents=True, exist_ok=True)
        return await with_retries(
            lambda: fetch.download_items(self.url, info, destination, self.config),
            self.config.download_attempts, "download")
