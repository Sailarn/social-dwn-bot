"""Admin-only commands: a short read of what the bot has been doing.

Gated on ADMIN_USER_IDS. With none set nobody qualifies, because the bot itself
may be open to everyone and usage data should not be.
"""

import asyncio
import logging
import resource
import time

import yt_dlp
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import src
from src.core.config import Config
from src.core.resources import available_memory_bytes, free_disk_bytes
from src.storage.reports import Reports
from src.telegram import format as fmt
from src.telegram.services import Services

log = logging.getLogger(__name__)
router = Router()

RECENT_ERROR_LIMIT = 8
KB_PER_MB = 1024


def _denied(message: Message, config: Config) -> bool:
    return message.from_user is None or not config.is_admin(message.from_user.id)


@router.message(Command("stats"))
async def handle_stats(message: Message, config: Config, services: Services) -> None:
    if _denied(message, config):
        return
    reports = Reports(services.events)
    lines = ["📊 <b>usage</b>", "<pre>"]
    lines.append(f"{'':<10}{'24h':>7}{'7d':>7}{'30d':>7}")
    windows = [reports.summary(1), reports.summary(7), reports.summary(30)]
    for label, key in (("requests", None), ("sent", "sent"), ("cached", "cache_hit")):
        values = [w["total"] if key is None else w["counts"].get(key, 0) for w in windows]
        lines.append(f"{label:<10}{values[0]:>7}{values[1]:>7}{values[2]:>7}")
    failed = [sum(v for k, v in w["counts"].items()
                  if k in ("rejected", "unavailable", "error")) for w in windows]
    lines.append(f"{'failed':<10}{failed[0]:>7}{failed[1]:>7}{failed[2]:>7}")
    lines.append(f"{'chats':<10}{windows[0]['chats']:>7}{windows[1]['chats']:>7}"
                 f"{windows[2]['chats']:>7}")
    lines.append("</pre>")

    month = windows[2]
    lines.append(f"median {fmt.milliseconds(month['median_ms'])} · "
                 f"p95 {fmt.milliseconds(month['p95_ms'])} · "
                 f"ok {fmt.percent(month['counts'].get('sent', 0) + month['counts'].get('cache_hit', 0), month['total'])}")
    if month["reasons"]:
        lines.append("\n<b>why things failed</b> (30d)")
        lines += [f"· {fmt.esc(reason)} × {count}" for reason, count in month["reasons"]]

    busiest = reports.busiest_chats(30)
    if len(busiest) > 1:
        lines.append("\n<b>busiest chats</b> (30d)")
        lines += [f"· {fmt.esc(chat_hash[:6])} × {count}" for chat_hash, count in busiest]
    await message.reply("\n".join(lines), parse_mode="HTML")


@router.message(Command("errors"))
async def handle_errors(message: Message, config: Config, services: Services) -> None:
    if _denied(message, config):
        return
    reports = Reports(services.events)
    parts = (message.text or "").split()
    if len(parts) > 1:
        await message.reply(_error_detail(reports, parts[1].strip()),
                            parse_mode="HTML")
        return

    rows = reports.recent_errors(RECENT_ERROR_LIMIT)
    if not rows:
        await message.reply("✅ no errors recorded")
        return
    lines = [f"⚠️ <b>{len(rows)} unique error(s)</b>"]
    for row in rows:
        lines.append(
            f"\n<code>{fmt.esc(row['fingerprint'])}</code> ×{row['seen_count']} · "
            f"{fmt.esc(row['platform'])} · {fmt.ago(row['last_seen'])}\n"
            f"{fmt.esc(row['message'][:90])}")
    lines.append("\n<code>/errors &lt;id&gt;</code> for detail")
    await message.reply("\n".join(lines), parse_mode="HTML")


def _error_detail(reports: Reports, fingerprint: str) -> str:
    row = reports.error_detail(fingerprint)
    if row is None:
        return f"no error with id <code>{fmt.esc(fingerprint)}</code>"
    return "\n".join([
        f"⚠️ <code>{fmt.esc(row['fingerprint'])}</code> · "
        f"{fmt.esc(row['error_type'])} · ×{row['seen_count']}",
        f"platform: {fmt.esc(row['platform'])}",
        f"first: {fmt.ago(row['first_seen'])} · last: {fmt.ago(row['last_seen'])}",
        f"message: {fmt.esc(row['message'])}",
        f"url: {fmt.esc(row['url'])}",
        "",
        "<b>full log on the pi:</b>",
        f"<code>grep {fmt.esc(row['request_id'])} "
        f"~/.pm2/logs/social-download-tg-error.log</code>",
        "",
        f"<pre>{fmt.esc((row['detail'] or '')[-600:])}</pre>",
    ])


@router.message(Command("health"))
async def handle_health(message: Message, config: Config, services: Services,
                        download_slots: asyncio.Semaphore,
                        started_at: float) -> None:
    if _denied(message, config):
        return
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / KB_PER_MB
    free_gb = free_disk_bytes(config.data_dir) / 1024 ** 3
    memory = available_memory_bytes()
    memory_line = f" · {memory / 1024 ** 2:.0f}MB free ram" if memory else ""
    event_rows, error_rows = services.events.counts()
    await message.reply("\n".join([
        f"🩺 <b>health</b> · v{fmt.esc(src.__version__)}",
        f"up {fmt.duration(time.time() - started_at)} · rss {rss_mb:.0f}MB · "
        f"disk {free_gb:.0f}GB free{memory_line}",
        f"yt-dlp {fmt.esc(yt_dlp.version.__version__)}",
        f"free slots {download_slots._value}/{config.max_concurrent_downloads} · "
        f"{config.rate_limit_per_hour}/hour per user",
        f"pacing: {config.chat_send_interval_seconds:.0f}s per chat · "
        f"{config.platform_interval_seconds:.0f}s per platform",
        f"events {event_rows} · unique errors {error_rows}",
    ]), parse_mode="HTML")
