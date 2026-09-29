"""Admin-only commands: a short read of what the bot has been doing.

Gated on ADMIN_USER_IDS, and answered only in a private chat with the bot: the
stats name groups, and a group is the wrong place to read another group's name.
With no admins set nobody qualifies, because the bot itself may be open to
everyone and usage data should not be.
"""

import asyncio
import calendar
import logging
import resource
import time

import yt_dlp
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import src
from src.core.config import Config
from src.core.limits import (
    APIFY_COST_PER_RUN_USD,
    APIFY_MONTHLY_CREDIT_USD,
    RENDER_MONTHLY_BANDWIDTH_BYTES,
)
from src.core.resources import available_memory_bytes, free_disk_bytes
from src.storage.reports import Reports
from src.telegram import format as fmt
from src.telegram.services import Services

log = logging.getLogger(__name__)
router = Router()

RECENT_ERROR_LIMIT = 8
KB_PER_MB = 1024


def _denied(message: Message, config: Config) -> bool:
    """Silence, not a refusal: nothing to hint that the command exists."""
    return (message.from_user is None
            or not config.is_admin(message.from_user.id)
            or message.chat.type != "private")


@router.message(Command("stats"))
async def handle_stats(message: Message, config: Config, services: Services) -> None:
    if _denied(message, config):
        return
    # Storage may be remote: build the report off the event loop.
    text = await asyncio.to_thread(_stats_text, Reports(services.events))
    await message.reply(text, parse_mode="HTML")


def _stats_text(reports: Reports) -> str:
    windows = [reports.summary(1), reports.summary(7), reports.summary(30)]
    return "\n".join([
        *_usage_lines(windows),
        *_source_lines(reports.sources(30)),
        *_platform_lines(reports.platforms(30)),
        *_chat_lines(reports.chats(30)),
        *_reason_lines(windows[2]),
        *_month_lines(reports.since(_start_of_month())),
    ])


def _usage_lines(windows: list[dict]) -> list[str]:
    lines = ["📊 <b>usage</b>", "<pre>", f"{'':<10}{'24h':>7}{'7d':>7}{'30d':>7}"]
    failed = [sum(v for k, v in w["counts"].items()
                  if k in ("rejected", "unavailable", "error")) for w in windows]
    rows = (
        ("requests", [w["total"] for w in windows]),
        ("sent", [w["counts"].get("sent", 0) for w in windows]),
        ("cached", [w["counts"].get("cache_hit", 0) for w in windows]),
        ("failed", failed),
        ("chats", [w["chats"] for w in windows]),
    )
    lines += [f"{label:<10}{a:>7}{b:>7}{c:>7}" for label, (a, b, c) in rows]
    lines.append("</pre>")
    month = windows[2]
    served = month["counts"].get("sent", 0) + month["counts"].get("cache_hit", 0)
    lines.append(f"median {fmt.milliseconds(month['median_ms'])} · "
                 f"p95 {fmt.milliseconds(month['p95_ms'])} · "
                 f"ok {fmt.percent(served, month['total'])}")
    return lines


def _source_lines(sources: list[dict]) -> list[str]:
    """Which step served what: the pipeline, measured."""
    if not sources:
        return []
    lines = ["", "<b>by source</b> (30d)", "<pre>",
             f"{'':<10}{'tried':>6}{'served':>7}{'median':>8}"]
    lines += [f"{s['step']:<10}{s['tried']:>6}{s['served']:>7}"
              f"{fmt.milliseconds(s['median_ms']):>8}" for s in sources]
    lines.append("</pre>")
    return lines


def _platform_lines(platforms: list[dict]) -> list[str]:
    if not platforms:
        return []
    lines = ["", "<b>by platform</b> (30d)", "<pre>", f"{'':<10}{'asked':>6}{'ok':>7}"]
    lines += [f"{fmt.esc(p['platform'])[:10]:<10}{p['requests']:>6}"
              f"{fmt.percent(p['served'], p['requests']):>7}" for p in platforms]
    lines.append("</pre>")
    return lines


def _chat_lines(chats: dict) -> list[str]:
    if not chats["groups"] and not chats["private_chats"]:
        return []
    lines = ["", "<b>chats</b> (30d)"]
    lines += [f"· {fmt.esc(title)} <code>{chat_id}</code> × {count}"
              for title, chat_id, count in chats["groups"]]
    if chats["private_chats"]:
        lines.append(f"· private: {chats['private_chats']} chat(s) × "
                     f"{chats['private_requests']}")
    return lines


def _reason_lines(month: dict) -> list[str]:
    if not month["reasons"]:
        return []
    return ["", "<b>why things failed</b> (30d)",
            *[f"· {fmt.esc(reason)} × {count}" for reason, count in month["reasons"]]]


def _month_lines(month: dict) -> list[str]:
    """Against the free plans' monthly limits, which reset on the 1st."""
    spent = month["apify_runs"] * APIFY_COST_PER_RUN_USD
    bandwidth = month["sent_bytes"] / RENDER_MONTHLY_BANDWIDTH_BYTES
    return [
        "", "<b>this month</b>",
        f"apify {month['apify_runs']} run(s) ≈ ${spent:.2f} of "
        f"${APIFY_MONTHLY_CREDIT_USD:.0f}",
        f"cookie account used {month['cookie_uses']}×",
        f"sent {fmt.size(month['sent_bytes'])} ({bandwidth:.1%} of Render's 100 GB)",
    ]


def _start_of_month() -> int:
    today = time.gmtime()
    return int(calendar.timegm((today.tm_year, today.tm_mon, 1, 0, 0, 0)))


@router.message(Command("errors"))
async def handle_errors(message: Message, config: Config, services: Services) -> None:
    if _denied(message, config):
        return
    parts = (message.text or "").split()
    fingerprint = parts[1].strip() if len(parts) > 1 else None
    text = await asyncio.to_thread(
        _errors_text, Reports(services.events), fingerprint, config)
    await message.reply(text, parse_mode="HTML")


def _errors_text(reports: Reports, fingerprint: str | None, config: Config) -> str:
    if fingerprint is not None:
        return _error_detail(reports, fingerprint, config)
    rows = reports.recent_errors(RECENT_ERROR_LIMIT)
    if not rows:
        return "✅ no errors recorded"
    lines = [f"⚠️ <b>{len(rows)} unique error(s)</b>"]
    for row in rows:
        lines.append(
            f"\n<code>{fmt.esc(row['fingerprint'])}</code> ×{row['seen_count']} · "
            f"{fmt.esc(row['platform'])} · {fmt.ago(row['last_seen'])}\n"
            f"{fmt.esc(row['message'][:90])}")
    lines.append("\n<code>/errors &lt;id&gt;</code> for detail")
    return "\n".join(lines)


def _log_hint(request_id: str, config: Config) -> list[str]:
    """Where the full log for this request lives, on the host it runs on."""
    if config.on_render:
        return ["<b>full log:</b> Render → Logs → search",
                f"<code>{fmt.esc(request_id)}</code>"]
    return ["<b>full log on the pi:</b>",
            f"<code>grep {fmt.esc(request_id)} "
            f"~/.pm2/logs/social-download-tg-error.log</code>"]


def _error_detail(reports: Reports, fingerprint: str, config: Config) -> str:
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
        *_log_hint(row["request_id"], config),
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
    event_rows, error_rows = await asyncio.to_thread(services.events.counts)
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
