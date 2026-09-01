"""Rendering the numbers into something short enough to read on a phone.

Output is HTML, not Markdown. Telegram's Markdown treats `_` as italic, and the
failure reasons are slugs like `needs_login` — an underscore that never closes
its entity makes the API reject the whole message. HTML needs only three
characters escaped.
"""

import html
import time

MINUTE = 60
HOUR = 3600
DAY = 86400


def duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < MINUTE:
        return f"{seconds}s"
    if seconds < HOUR:
        return f"{seconds // MINUTE}m"
    if seconds < DAY:
        return f"{seconds // HOUR}h{(seconds % HOUR) // MINUTE:02d}m"
    return f"{seconds // DAY}d{(seconds % DAY) // HOUR}h"


def ago(unix_timestamp: int) -> str:
    return f"{duration(time.time() - unix_timestamp)} ago"


def milliseconds(value: int) -> str:
    return f"{value / 1000:.1f}s" if value else "-"


def size(byte_count: int) -> str:
    if byte_count >= 1024 * 1024:
        return f"{byte_count / 1024 / 1024:.1f}MB"
    return f"{byte_count / 1024:.0f}KB"


def percent(part: int, whole: int) -> str:
    return f"{part * 100 // whole}%" if whole else "-"


def esc(value) -> str:
    """Escape a value for Telegram HTML. Only &, < and > matter."""
    return html.escape(str(value), quote=False)
