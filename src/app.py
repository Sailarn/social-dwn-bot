"""Startup, shutdown, and the health endpoint some hosts insist on."""

import asyncio
import logging
import os
import signal
import time
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiohttp import web

from src.core import cookies, heartbeat
from src.core.config import Config, load_config
from src.core.pacing import Pacer, PlatformPacer
from src.core.tracing import RequestIdFilter
from src.media import transcode
from src.storage.cache import FileIdCache
from src.storage.ratelimit import RateLimiter
from src.storage.stats import EventLog
from src.telegram import admin, failures, handlers
from src.telegram.notify import Notifier
from src.telegram.services import Services

import src

log = logging.getLogger("socialdl")

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s"


def _configure_logging() -> None:
    """Every line carries the request id, so /errors leads straight to the log."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(), format=LOG_FORMAT
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(RequestIdFilter())


async def _start_health_server(host: str, port: int) -> web.AppRunner:
    """Some free hosts kill a service that never binds a port."""
    app = web.Application()
    app.router.add_get("/", lambda _: web.Response(text="ok"))
    app.router.add_get("/health", lambda _: web.Response(text="ok"))
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info("health endpoint on %s:%d", host, port)
    return runner


def _report_cookies(config: Config) -> float | None:
    """Days remaining when the session is close enough to expiry to say so.

    Read from the cookie file's own expiry fields rather than its age: a freshly
    exported file can still hold a nearly-dead session.
    """
    if config.cookies_file is None:
        log.info("no cookies file: login-walled posts will be refused")
        return None

    remaining = cookies.days_until_expiry(Path(config.cookies_file))
    if remaining is None:
        log.info("cookies loaded from %s (no expiry found)", config.cookies_file)
        return None

    log.info("cookies loaded from %s (session expires in %.0f days)",
             config.cookies_file, remaining)
    if remaining <= config.cookie_warn_days:
        log.warning("instagram session expires in %.0f days; export cookies again",
                    remaining)
        return remaining
    return None


def _warn_if_open(config: Config) -> None:
    if not config.is_open_to_everyone:
        return
    log.warning(
        "open to everyone: any user in any chat may use this bot. The %d/hour "
        "per-user rate limit is the only brake. Set ALLOWED_USER_IDS or "
        "ALLOWED_CHAT_IDS in .env to restrict it.",
        config.rate_limit_per_hour,
    )


def _build_dispatcher() -> Dispatcher:
    """Admin router first: a catch-all in a router is still checked after it."""
    dispatcher = Dispatcher()
    dispatcher.include_router(admin.router)
    dispatcher.include_router(handlers.router)
    dispatcher.include_router(failures.router)
    return dispatcher


def _open_storage(config: Config) -> tuple[FileIdCache, EventLog]:
    cache = FileIdCache(config.data_dir / "sent_clips.db", config.cache_ttl_days)
    expired = cache.prune()
    if expired:
        log.info("pruned %d cache entries older than %d days",
                 expired, config.cache_ttl_days)

    events = EventLog(config.data_dir / "events.db",
                      config.stats_retention_days, config.event_salt)
    dropped = events.prune()
    if dropped:
        log.info("pruned %d events older than %d days",
                 dropped, config.stats_retention_days)
    return cache, events


async def main() -> None:
    _configure_logging()
    config = load_config()
    _warn_if_open(config)
    expiring_in_days = _report_cookies(config)
    if not config.admin_user_ids:
        log.warning("ADMIN_USER_IDS is empty: /stats, /errors and /health are disabled")

    cache, events = _open_storage(config)
    bot = Bot(config.bot_token)
    services = Services(
        cache=cache,
        events=events,
        chat_pacer=Pacer(config.chat_send_interval_seconds),
        platform_pacer=PlatformPacer(config.platform_interval_seconds,
                                     config.platform_cooldown_seconds),
        notifier=Notifier(bot, config.alert_chat_id),
    )
    # Re-encodes are the one CPU-heavy thing here; keep them serialised.
    transcode.configure(config.max_concurrent_transcodes)
    health_runner = await _start_health_server(config.health_host, config.health_port)

    # Clears any webhook left over from another host, and skips the backlog that
    # piled up while the bot was down.
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("social-download-tg v%s", src.__version__)
    log.info(
        "polling: max %ds, max %dMB, %d concurrent, %d/hour, cache ttl %dd, stats %dd",
        config.max_duration_seconds, config.max_filesize_mb,
        config.max_concurrent_downloads, config.rate_limit_per_hour,
        config.cache_ttl_days, config.stats_retention_days,
    )
    log.info(
        "limits: %.0fs per chat, %.0fs per platform, %.0fs cooldown, "
        "%d transcode(s), floors %dMB disk / %dMB ram",
        config.chat_send_interval_seconds, config.platform_interval_seconds,
        config.platform_cooldown_seconds, config.max_concurrent_transcodes,
        config.min_free_disk_mb, config.min_free_memory_mb,
    )

    if expiring_in_days is not None:
        await services.notifier.send(
            f"🍪 Instagram session expires in {expiring_in_days:.0f} days. "
            f"Export cookies again before login-walled posts start failing.")

    heartbeat_task = None
    if config.heartbeat_url:
        heartbeat_task = asyncio.create_task(
            heartbeat.run(config.heartbeat_url, config.heartbeat_interval_seconds))
    else:
        log.info("no HEARTBEAT_URL: nothing will notice if this process stops")

    try:
        await _build_dispatcher().start_polling(
            bot,
            config=config,
            services=services,
            download_slots=asyncio.Semaphore(config.max_concurrent_downloads),
            rate_limiter=RateLimiter(config.rate_limit_per_hour),
            started_at=time.time(),
            handle_signals=False,
        )
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
        await health_runner.cleanup()
        await bot.session.close()
        cache.close()
        events.close()


def run() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(main())
    for received in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(received, task.cancel)
    try:
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        log.info("shutting down")
    finally:
        loop.close()
