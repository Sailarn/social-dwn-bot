"""with_retries: what gets a second chance and what does not."""

import asyncio

import pytest

from src.core import retry
from src.core.errors import ClipRejected, ClipUnavailable


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Backoff is real in production; tests should not sit through it."""
    async def instant(seconds):
        return None
    monkeypatch.setattr(retry.asyncio, "sleep", instant)


def run(operation, attempts=3):
    return asyncio.run(retry.with_retries(operation, attempts, "test"))


def test_success_returns_immediately():
    calls = []
    assert run(lambda: calls.append(1) or "ok") == "ok"
    assert len(calls) == 1


def test_transient_failure_is_retried_to_the_limit():
    calls = []

    def failing():
        calls.append(1)
        raise ClipUnavailable("network problem")

    with pytest.raises(ClipUnavailable):
        run(failing, attempts=3)
    assert len(calls) == 3


def test_permanent_failure_is_not_retried():
    """A post needing a login will still need one four seconds later."""
    calls = []

    def rejected():
        calls.append(1)
        raise ClipRejected("that post needs a login to view")

    with pytest.raises(ClipRejected):
        run(rejected, attempts=3)
    assert len(calls) == 1


def test_recovers_on_a_later_attempt():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ClipUnavailable("timeout")
        return "recovered"

    assert run(flaky, attempts=3) == "recovered"
    assert len(calls) == 3
