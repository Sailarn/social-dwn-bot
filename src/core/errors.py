"""Turning yt-dlp's errors into one short sentence, and deciding about retries."""

import re
from dataclasses import dataclass

USER_ERROR_MAX_CHARS = 100


class MediaError(Exception):
    """Carries a short slug alongside the user-facing sentence.

    The slug is what the event log counts, so "why did things fail" is a field
    rather than something parsed back out of a message.
    """

    default_reason = "unknown"

    def __init__(self, message: str, reason: str | None = None):
        super().__init__(message)
        self.reason = reason or self.default_reason


class ClipRejected(MediaError):
    """The clip is not something we will send. Retrying will not help."""

    default_reason = "rejected"


class ClipUnavailable(MediaError):
    """The download failed for a reason that may be transient."""

    default_reason = "failed"


@dataclass(frozen=True)
class _ErrorRule:
    pattern: re.Pattern
    message: str
    permanent: bool
    reason: str


# yt-dlp's own errors are long, quote the URL and end in advice aimed at someone
# holding a command line. Map the ones we actually see onto a short sentence, and
# record whether retrying could ever help: a post that needs a login will still
# need one in four seconds, so retrying it just wastes time.
ERROR_RULES = (
    _ErrorRule(
        re.compile(r"empty media response|login required|requested content is not"
                   r" available|sign in|log in|logged.?in", re.IGNORECASE),
        "that post needs a login to view", True, "needs_login"),
    _ErrorRule(
        re.compile(r"private|not authorized|no access", re.IGNORECASE),
        "that post is private", True, "private"),
    _ErrorRule(
        re.compile(r"age.?restrict", re.IGNORECASE),
        "that post is age-restricted", True, "age_restricted"),
    _ErrorRule(
        re.compile(r"geo.?block|geo.?restrict|not available in your country"
                   r"|blocked in your", re.IGNORECASE),
        "that post is blocked in this region", True, "geoblocked"),
    # What TikTok actually returns when it throttles a residential IP: three
    # rapid extractions produce this, and waiting clears it. Left unclassified,
    # the platform cooldown never fired for the one throttle seen in the wild.
    # A genuinely broken extractor produces the same shape, and backing off is
    # the right response to that too.
    _ErrorRule(
        re.compile(r"unable to extract|rehydration|please report this issue",
                   re.IGNORECASE),
        "the site is not responding properly, try again shortly", False,
        "site_throttled"),
    _ErrorRule(
        re.compile(r"unavailable|removed|deleted|does not exist|not found|404",
                   re.IGNORECASE),
        "that post is gone or unavailable", True, "gone"),
    _ErrorRule(
        re.compile(r"no suitable extractor|unsupported url", re.IGNORECASE),
        "can't handle that link", True, "unsupported_link"),
    _ErrorRule(
        re.compile(r"rate.?limit|429|too many requests", re.IGNORECASE),
        "the site is rate-limiting us, try again later", False, "site_throttled"),
    _ErrorRule(
        re.compile(r"timed out|timeout|connection|network|resolve|temporar",
                   re.IGNORECASE),
        "network problem reaching the site, try again", False, "network"),
)


def _first_sentence(text: str) -> str:
    """Trim yt-dlp's advice paragraph down to something readable in a chat."""
    first_line = text.splitlines()[0] if text else ""
    sentence = first_line.split(". ")[0].strip()
    if len(sentence) > USER_ERROR_MAX_CHARS:
        sentence = sentence[:USER_ERROR_MAX_CHARS].rstrip() + "..."
    return sentence or "download failed"


def as_user_error(error: Exception) -> Exception:
    """Turn a yt-dlp error into a short message, and decide if a retry could help."""
    text = str(error).replace("ERROR: ", "").strip()
    for rule in ERROR_RULES:
        if rule.pattern.search(text):
            failure = ClipRejected if rule.permanent else ClipUnavailable
            return failure(rule.message, rule.reason)
    return ClipUnavailable(_first_sentence(text), "unclassified")
