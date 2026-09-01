"""Reading when a session actually dies.

File age is a poor proxy: a freshly exported file can hold a nearly-dead
session, and an old file can hold a long-lived one.
"""

import time

from src.core.cookies import days_until_expiry

HEADER = "# Netscape HTTP Cookie File\n"


def cookie(name, expires_in_days, domain=".instagram.com"):
    expiry = int(time.time() + expires_in_days * 86400)
    return f"{domain}\tTRUE\t/\tTRUE\t{expiry}\t{name}\tvalue\n"


def write(tmp_path, *lines):
    path = tmp_path / "cookies.txt"
    path.write_text(HEADER + "".join(lines))
    return path


def test_reports_the_soonest_session_cookie(tmp_path):
    """One dead cookie ends the session, however healthy the others are."""
    path = write(tmp_path,
                 cookie("sessionid", 365),
                 cookie("ds_user_id", 90),
                 cookie("csrftoken", 400))
    assert 89 < days_until_expiry(path) < 91


def test_other_cookies_do_not_count(tmp_path):
    path = write(tmp_path, cookie("sessionid", 100), cookie("mid", 1))
    assert 99 < days_until_expiry(path) < 101


def test_an_expired_session_reads_negative(tmp_path):
    assert days_until_expiry(write(tmp_path, cookie("sessionid", -5))) < 0


def test_a_file_with_no_session_cookies(tmp_path):
    assert days_until_expiry(write(tmp_path, cookie("mid", 400))) is None


def test_session_only_cookies_are_skipped(tmp_path):
    """Expiry 0 means it dies with the browser; there is no date to report."""
    path = tmp_path / "cookies.txt"
    path.write_text(HEADER + ".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tvalue\n")
    assert days_until_expiry(path) is None


def test_comments_and_blank_lines_are_ignored(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(HEADER + "\n# a comment\n" + cookie("sessionid", 30))
    assert 29 < days_until_expiry(path) < 31


def test_a_malformed_line_does_not_break_it(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(HEADER + "garbage\n" + cookie("sessionid", 30))
    assert 29 < days_until_expiry(path) < 31


def test_a_missing_file_returns_none(tmp_path):
    assert days_until_expiry(tmp_path / "nope.txt") is None
