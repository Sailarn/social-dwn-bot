"""Which messages the bot reacts to, and who is allowed to make it react."""

import asyncio
import logging

from aiogram import Bot, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from src.core.config import Config
from src.media.links import find_supported_link, platform_of
from src.storage.ratelimit import RateLimiter
from src.storage.stats import Event
from src.telegram.delivery import deliver_clip
from src.telegram.services import Services

log = logging.getLogger(__name__)
router = Router()

HELP_TEXT = (
    "Post an Instagram, X or TikTok link and I'll reply with the video — "
    "or the photo, if the post is an image."
)


@router.message(CommandStart())
@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    await message.reply(HELP_TEXT)


@router.message(Command("chatid"))
async def handle_chat_id(message: Message, config: Config) -> None:
    """Prints the current chat's id, which is what ALLOWED_CHAT_IDS wants."""
    if message.from_user is None or not config.is_allowed(
        message.from_user.id, message.chat.id
    ):
        return
    await message.reply(
        f"chat id: <code>{message.chat.id}</code>\n"
        f"your id: <code>{message.from_user.id}</code>",
        parse_mode="HTML",
    )


@router.message()
async def handle_possible_link(
    message: Message,
    bot: Bot,
    config: Config,
    services: Services,
    download_slots: asyncio.Semaphore,
    rate_limiter: RateLimiter,
) -> None:
    url = find_supported_link(message.text or message.caption or "")
    if url is None:
        return
    if message.from_user is None or not config.is_allowed(
        message.from_user.id, message.chat.id
    ):
        return

    if not rate_limiter.allow(message.from_user.id):
        wait_minutes = max(rate_limiter.seconds_until_free(message.from_user.id) // 60, 1)
        services.events.record(Event(
            outcome="rate_limited", reason="rate_limited",
            platform=platform_of(url),
            chat_id=message.chat.id, user_id=message.from_user.id,
        ))
        await message.reply(f"⚠️ rate limit reached, try again in {wait_minutes} min")
        return

    # Space requests to the platform *before* taking a slot, so a paced request
    # does not occupy capacity while it waits.
    platform = platform_of(url)
    waited = await services.platform_pacer.wait(platform)
    if waited:
        log.info("waited %.1fs before asking %s again", waited, platform)

    async with download_slots:
        await deliver_clip(message, bot, config, services, url)
