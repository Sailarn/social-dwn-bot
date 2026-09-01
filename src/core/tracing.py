"""A short id per request, carried into every log line it produces.

This is the link between the two views. `/errors` in Telegram shows the id; the
full story — including yt-dlp's own error text and any traceback — is in the Pi
log under the same id, so finding it is a grep rather than a hunt by timestamp.
"""

import contextvars
import logging
import secrets

REQUEST_ID_LENGTH = 6
NO_REQUEST = "-"

current_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=NO_REQUEST
)


def new_request_id() -> str:
    return secrets.token_hex(REQUEST_ID_LENGTH // 2)


def start_request() -> str:
    request_id = new_request_id()
    current_request_id.set(request_id)
    return request_id


class RequestIdFilter(logging.Filter):
    """Adds `request_id` to every record so the log format can show it.

    `asyncio.to_thread` copies the context, so lines logged from the blocking
    download work carry the id too.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id.get()
        return True
