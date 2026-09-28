"""Reading when a cookie session actually dies, rather than guessing.

File age is a poor proxy: a freshly exported file can hold a session that is
nearly expired. The Netscape format carries the real expiry per cookie, so read
those and warn before the session goes rather than after posts start failing.
"""

import base64
import binascii
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Without these an Instagram session is not logged in. The soonest of them is
# what actually ends the session.
SESSION_COOKIES = frozenset({"sessionid", "ds_user_id", "csrftoken"})
NETSCAPE_FIELDS = 7
EXPIRY_FIELD = 4
NAME_FIELD = 5


def days_until_expiry(path: Path) -> float | None:
    """Days until the first session-critical cookie expires, or None if unknown."""
    try:
        lines = path.read_text().splitlines()
    except OSError as error:
        log.warning("could not read cookies at %s: %s", path, error)
        return None

    expiries = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < NETSCAPE_FIELDS or fields[NAME_FIELD] not in SESSION_COOKIES:
            continue
        try:
            expiry = int(fields[EXPIRY_FIELD])
        except ValueError:
            continue
        if expiry > 0:
            expiries.append(expiry)

    if not expiries:
        return None
    return (min(expiries) - time.time()) / 86400


def write_from_base64(encoded: str, target: Path) -> Path | None:
    """Decode cookies passed through the environment into a private file.

    Returns None rather than raising: bad cookies must not stop the bot, only
    the posts that need a login.
    """
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        log.warning("COOKIES_B64 is not valid base64; continuing without cookies")
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Created 0600 rather than chmod-ed after, so it is never briefly readable.
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
    except OSError as error:
        log.warning("could not write cookies to %s: %s", target, error)
        return None
    return target
