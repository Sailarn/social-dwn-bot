"""Turning a link into a reply, and recording what happened."""

import asyncio
import logging
import tempfile
import time
import traceback
from pathlib import Path

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import Message

from src.core.config import Config
from src.core.errors import ClipRejected, ClipUnavailable, MediaError
from src.core.models import ClipInfo
from src.core.retry import with_retries
from src.core.tracing import start_request
from src.media import fetch, links
from src.media.extract import probe
from src.storage.stats import Event
from src.telegram import notify, send
from src.telegram.services import Services

log = logging.getLogger(__name__)

# Telegram answers a flood with a 429 carrying how long to wait. Honour it once;
# a second one means the pacing is wrong, not that we should keep hammering.
FLOOD_RETRY_GRACE_SECONDS = 1


async def with_flood_retry(operation):
    """Telegram's own rate limit, obeyed rather than fought."""
    try:
        return await operation()
    except TelegramRetryAfter as error:
        log.warning("telegram asked for %ss before the next send", error.retry_after)
        await asyncio.sleep(error.retry_after + FLOOD_RETRY_GRACE_SECONDS)
        return await operation()


async def deliver_clip(
    message: Message,
    bot: Bot,
    config: Config,
    services: Services,
    url: str,
) -> None:
    request_id = start_request()
    started = time.monotonic()
    platform = links.platform_of(url)
    record = {"outcome": "error", "reason": None, "kind": None, "bytes": 0}

    try:
        await _run(message, bot, config, services, url, record)
    except MediaError as error:
        record["outcome"] = "rejected" if isinstance(error, ClipRejected) else "unavailable"
        record["reason"] = error.reason
        if isinstance(error, ClipUnavailable):
            # Rejections are normal operation; only unexpected failures are
            # worth a place in the error register.
            await _register(services, platform, error, url, request_id, str(error))
        if error.reason == "site_throttled":
            services.platform_pacer.penalise(platform)
        log.info("[%s] %s: %s", request_id, record["outcome"], error)
        await message.reply(f"⚠️ {error}")
    except Exception as error:  # noqa: BLE001 - last resort, must not kill polling
        record["outcome"], record["reason"] = "error", "unhandled"
        log.exception("[%s] unexpected failure on %s", request_id, url)
        await _register(services, platform, error, url, request_id,
                        traceback.format_exc())
        await message.reply("⚠️ something went wrong, try again")
    finally:
        services.events.record(Event(
            outcome=record["outcome"],
            platform=platform,
            kind=record["kind"],
            reason=record["reason"],
            total_ms=int((time.monotonic() - started) * 1000),
            bytes=record["bytes"],
            chat_id=message.chat.id,
            user_id=message.from_user.id if message.from_user else None,
            request_id=request_id,
        ))


async def _run(message, bot, config, services: Services, url, record) -> None:
    url_key = f"url:{links.normalize_url(url)}"
    cache = services.cache

    # Fast path: the same link already sent. Skips the metadata lookup entirely,
    # which is what makes a repeat post genuinely instant.
    cached = cache.get(url_key)
    if cached:
        await services.chat_pacer.wait(message.chat.id)
        kind = await with_flood_retry(lambda: send.send_cached(message, cached))
        record.update(outcome="cache_hit", kind=kind.value)
        return

    await bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_VIDEO)
    info = await with_retries(
        lambda: probe(url, config), config.download_attempts, "probe")
    record["kind"] = info.kind.value

    # Slower path: a different URL for something already sent.
    cached = cache.get(info.key)
    if cached:
        cache.put(url_key, cached)
        await services.chat_pacer.wait(message.chat.id)
        await with_flood_retry(lambda: send.send_cached(message, cached))
        record["outcome"] = "cache_hit"
        return

    file_id, size = await _download_and_send(message, config, services, info, url)
    record.update(outcome="sent", bytes=size)
    if file_id:
        value = send.cache_value(info.kind, file_id)
        cache.put(info.key, value)
        cache.put(url_key, value)


async def _download_and_send(
    message: Message, config: Config, services: Services, info: ClipInfo, url: str
) -> tuple[str | None, int]:
    """Fetch every item in the post, send it, and report the file_id and size."""
    with tempfile.TemporaryDirectory(prefix="socialdl-") as workdir:
        downloaded = await with_retries(
            lambda: fetch.download_items(url, info, Path(workdir), config),
            config.download_attempts, "download")
        size = sum(entry.path.stat().st_size for entry in downloaded)
        log.info("sending %s (%s, %d item(s), %d bytes)",
                 info.key, info.kind.value, len(downloaded), size)

        await services.chat_pacer.wait(message.chat.id)
        if len(downloaded) > 1:
            await with_flood_retry(lambda: send.send_album(message, downloaded))
            return None, size
        return await with_flood_retry(lambda: send.send_one(message, downloaded[0])), size


async def _register(services: Services, platform: str, error: Exception,
                    url: str, request_id: str, detail: str) -> None:
    fingerprint, is_new = services.events.record_error(
        platform=platform,
        error_type=type(error).__name__,
        message=str(error),
        url=url,
        detail=detail,
        request_id=request_id,
    )
    if not is_new:
        return
    # Only the first occurrence alerts, so a broken platform is one message
    # rather than one per affected post.
    log.warning("[%s] new unique error %s: %s", request_id, fingerprint, error)
    await services.notifier.send(
        notify.new_error_alert(fingerprint, platform, str(error)))
