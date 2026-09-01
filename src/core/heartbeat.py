"""A dead-man's switch, so silence is noticed.

Alerts can only fire while the bot is alive. If the Pi loses power, the internet
drops, or the process hangs, nothing reports it — the bot simply stops existing
and the failure looks identical to nobody sending links.

This pings a URL on a schedule. A monitoring service (Healthchecks.io, Better
Stack, cron-job.org) notifies you when the pings stop. Outbound only, so it adds
no inbound exposure.
"""

import asyncio
import logging

import aiohttp

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 15


async def run(url: str, interval_seconds: float) -> None:
    """Ping forever. Never raises: a monitoring failure must not stop the bot."""
    log.info("heartbeat every %.0fs", interval_seconds)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
                ) as response:
                    if response.status >= 400:
                        log.warning("heartbeat returned %s", response.status)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                log.warning("heartbeat failed: %s", error)
            await asyncio.sleep(interval_seconds)
