"""The catch-all that puts handler bugs into /errors instead of only the log."""

import asyncio

from fakes import FakeNotifier
from src.core.pacing import Pacer, PlatformPacer
from src.storage.reports import Reports
from src.storage.stats import EventLog
from src.telegram import failures
from src.telegram.services import Services


def services_with(events):
    return Services(cache=None, events=events, chat_pacer=Pacer(0),
                    platform_pacer=PlatformPacer(0, 0), notifier=FakeNotifier())


class FakeErrorEvent:
    def __init__(self, exception):
        self.exception = exception
        self.update = None


def test_an_unhandled_error_is_registered(tmp_path):
    events = EventLog(tmp_path / "e.db", 90, "salt")
    handled = asyncio.run(failures.record_unhandled(
        FakeErrorEvent(RuntimeError("boom")), services_with(events)))
    assert handled is True

    rows = Reports(events).recent_errors()
    assert len(rows) == 1
    assert rows[0]["error_type"] == "RuntimeError"
    assert rows[0]["platform"] == "bot"


def test_the_same_bug_twice_is_one_signature(tmp_path):
    events = EventLog(tmp_path / "e.db", 90, "salt")
    for _ in range(3):
        asyncio.run(failures.record_unhandled(
            FakeErrorEvent(ValueError("same fault")), services_with(events)))
    rows = Reports(events).recent_errors()
    assert len(rows) == 1 and rows[0]["seen_count"] == 3


def test_it_never_raises_even_if_recording_fails(tmp_path):
    """The error handler is the last line; it must not become the error."""
    class Exploding:
        def record_error(self, **kwargs):
            raise RuntimeError("database on fire")

    handled = asyncio.run(failures.record_unhandled(
        FakeErrorEvent(RuntimeError("boom")), services_with(Exploding())))
    assert handled is True
