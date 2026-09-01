"""Catch-all for handler bugs, so they show up in /errors rather than only in the log.

The delivery path records its own failures. Everything else — a command handler
raising, a Telegram API rejection — used to surface only in the Pi log, which is
exactly where a bug hides when nobody is grepping.
"""

import logging
import traceback

from aiogram import Router
from aiogram.types import ErrorEvent

from src.core.tracing import current_request_id
from src.telegram import notify
from src.telegram.services import Services

log = logging.getLogger(__name__)
router = Router()


@router.errors()
async def record_unhandled(event: ErrorEvent, services: Services) -> bool:
    """Returning True marks it handled; polling continues either way."""
    error = event.exception
    log.exception("unhandled error while processing an update: %s", error)
    try:
        fingerprint, is_new = services.events.record_error(
            platform="bot",
            error_type=type(error).__name__,
            message=str(error),
            url="",
            detail=traceback.format_exc(),
            request_id=current_request_id.get(),
        )
        if is_new:
            log.warning("new unique bot error %s: %s", fingerprint, error)
            await services.notifier.send(
                notify.new_error_alert(fingerprint, "bot", str(error)))
    except Exception:  # noqa: BLE001 - the error handler must never raise
        log.exception("could not record the error above")
    return True
