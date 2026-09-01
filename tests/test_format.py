"""Rendering helpers for the admin commands."""

import time

import pytest

from src.telegram import format as fmt


@pytest.mark.parametrize("seconds,expected", [
    (0, "0s"), (45, "45s"), (60, "1m"), (3599, "59m"),
    (3600, "1h00m"), (7830, "2h10m"), (86400, "1d0h"), (180000, "2d2h"),
])
def test_duration(seconds, expected):
    assert fmt.duration(seconds) == expected


def test_ago_reads_from_now():
    assert fmt.ago(int(time.time()) - 3600).endswith("ago")


@pytest.mark.parametrize("value,expected", [(0, "-"), (1500, "1.5s"), (12000, "12.0s")])
def test_milliseconds(value, expected):
    assert fmt.milliseconds(value) == expected


@pytest.mark.parametrize("value,expected", [
    (500, "0KB"), (2048, "2KB"), (5 * 1024 * 1024, "5.0MB"),
])
def test_size(value, expected):
    assert fmt.size(value) == expected


def test_percent_handles_an_empty_denominator():
    assert fmt.percent(0, 0) == "-"
    assert fmt.percent(3, 4) == "75%"


class TestEscaping:
    @pytest.mark.parametrize("raw,expected", [
        ("<b>x</b>", "&lt;b&gt;x&lt;/b&gt;"),
        ("a & b", "a &amp; b"),
        ("needs_login", "needs_login"),      # underscores are safe in HTML
        ("*bold*", "*bold*"),
    ])
    def test_only_html_specials_are_escaped(self, raw, expected):
        assert fmt.esc(raw) == expected

    def test_non_strings_are_handled(self):
        assert fmt.esc(42) == "42"
