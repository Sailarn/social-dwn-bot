"""The dead-man's switch.

It must keep pinging through failures: a transient network blip should not stop
the heartbeat, or a recovered bot would look dead forever.
"""

import asyncio
import contextlib

import pytest

from src.core import heartbeat


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, statuses=None, raises=None):
        self.calls: list[str] = []
        self.statuses = list(statuses or [])
        self.raises = list(raises or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, timeout=None):
        self.calls.append(url)
        if self.raises and self.raises.pop(0):
            raise OSError("network unreachable")
        return FakeResponse(self.statuses.pop(0) if self.statuses else 200)


@pytest.fixture
def session(monkeypatch):
    made = {}

    def factory(*args, **kwargs):
        made["session"] = made.get("session") or FakeSession()
        return made["session"]

    monkeypatch.setattr(heartbeat.aiohttp, "ClientSession", factory)
    return made


async def run_briefly(url, interval, cycles=3):
    task = asyncio.create_task(heartbeat.run(url, interval))
    await asyncio.sleep(interval * cycles)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def test_it_pings_the_url_repeatedly(session):
    asyncio.run(run_briefly("https://hc-ping.com/abc", 0.01))
    calls = session["session"].calls
    assert len(calls) >= 2
    assert all(url == "https://hc-ping.com/abc" for url in calls)


def test_it_pings_immediately_rather_than_after_the_first_interval(session):
    """Otherwise a restart looks like an outage for a whole period."""
    async def scenario():
        task = asyncio.create_task(heartbeat.run("https://hc-ping.com/abc", 60))
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    assert len(session["session"].calls) == 1


def test_a_network_failure_does_not_stop_it(monkeypatch):
    made = {}

    def factory(*args, **kwargs):
        made["session"] = made.get("session") or FakeSession(raises=[True, False, False])
        return made["session"]

    monkeypatch.setattr(heartbeat.aiohttp, "ClientSession", factory)
    asyncio.run(run_briefly("https://hc-ping.com/abc", 0.01))
    assert len(made["session"].calls) >= 3, "must keep going after the first failure"


def test_an_error_status_does_not_stop_it(monkeypatch):
    made = {}

    def factory(*args, **kwargs):
        made["session"] = made.get("session") or FakeSession(statuses=[500, 200, 200])
        return made["session"]

    monkeypatch.setattr(heartbeat.aiohttp, "ClientSession", factory)
    asyncio.run(run_briefly("https://hc-ping.com/abc", 0.01))
    assert len(made["session"].calls) >= 3


def test_cancellation_is_honoured(session):
    """Shutdown must not hang waiting for the next ping."""
    async def scenario():
        task = asyncio.create_task(heartbeat.run("https://hc-ping.com/abc", 3600))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
