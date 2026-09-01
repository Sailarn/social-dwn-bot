"""Error text shown to users, and whether a retry could ever help."""

import pytest

from src.core.errors import ClipRejected, ClipUnavailable, as_user_error

REAL_INSTAGRAM_LOGIN_ERROR = (
    "ERROR: [Instagram] Dy4w5x6EfGh: Instagram sent an empty media response. "
    "Check if this post is accessible in your browser without being logged-in. "
    "If it is not, then use --cookies-from-browser or --cookies for the "
    "authentication. See https://github.com/yt-dlp/yt-dlp/wiki/FAQ"
)


@pytest.mark.parametrize("raw,message,permanent", [
    (REAL_INSTAGRAM_LOGIN_ERROR, "that post needs a login to view", True),
    ("ERROR: [Instagram] X: This account is private", "that post is private", True),
    ("ERROR: Video unavailable. It has been removed",
     "that post is gone or unavailable", True),
    ("ERROR: [TikTok] Video not available in your country",
     "that post is blocked in this region", True),
    ("ERROR: This video is age-restricted", "that post is age-restricted", True),
    ("ERROR: No suitable extractor found for URL http://192.168.1.1/",
     "can't handle that link", True),
    ("ERROR: HTTP Error 429: Too Many Requests",
     "the site is rate-limiting us, try again later", False),
    ("ERROR: Unable to download webpage: The read operation timed out",
     "network problem reaching the site, try again", False),
])
def test_known_errors_map_to_short_messages(raw, message, permanent):
    error = as_user_error(Exception(raw))
    assert str(error) == message
    expected = ClipRejected if permanent else ClipUnavailable
    assert isinstance(error, expected)


def test_permanent_failures_are_not_retried():
    """ClipRejected is what stops with_retries from burning three attempts."""
    assert isinstance(as_user_error(Exception(REAL_INSTAGRAM_LOGIN_ERROR)), ClipRejected)


def test_the_long_real_error_becomes_short():
    assert len(REAL_INSTAGRAM_LOGIN_ERROR) > 250
    assert len(str(as_user_error(Exception(REAL_INSTAGRAM_LOGIN_ERROR)))) < 40


def test_unmapped_error_keeps_only_the_first_sentence():
    error = as_user_error(Exception("ERROR: [Site] novel failure. Extra advice here."))
    assert str(error) == "[Site] novel failure"
    assert isinstance(error, ClipUnavailable)


def test_unmapped_error_is_capped():
    error = as_user_error(Exception("ERROR: " + "x" * 400))
    assert len(str(error)) <= 103
    assert str(error).endswith("...")


def test_empty_error_still_says_something():
    assert str(as_user_error(Exception(""))) == "download failed"


class TestPlatformThrottling:
    """The signature TikTok actually returns when it throttles a home IP.

    Observed live: three rapid extractions produce it, waiting clears it. It used
    to classify as `unclassified`, so the platform cooldown — the whole point of
    which is avoiding an IP ban — never fired for the real-world case.
    """

    REAL = ("ERROR: [TikTok] 7365419007513496839: Unable to extract universal data "
            "for rehydration; please report this issue on "
            "https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the template")

    def test_it_is_recognised_as_throttling(self):
        error = as_user_error(Exception(self.REAL))
        assert error.reason == "site_throttled", "otherwise no cooldown is applied"

    def test_it_is_retryable_not_permanent(self):
        assert isinstance(as_user_error(Exception(self.REAL)), ClipUnavailable)

    def test_the_message_does_not_blame_the_user(self):
        assert str(as_user_error(Exception(self.REAL))) == \
            "the site is not responding properly, try again shortly"

    def test_a_deleted_post_is_still_permanent(self):
        """The new rule must not swallow the 'gone' case."""
        error = as_user_error(Exception("ERROR: Video unavailable. It has been removed"))
        assert error.reason == "gone" and isinstance(error, ClipRejected)
