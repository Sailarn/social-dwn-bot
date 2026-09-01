"""Alerts: the part that reaches you without you going to look."""

import asyncio

from src.telegram.notify import Notifier, new_error_alert


class FakeBot:
    def __init__(self, explode=False):
        self.sent: list[tuple] = []
        self.explode = explode

    async def send_message(self, chat_id, text, **kwargs):
        if self.explode:
            raise RuntimeError("bot blocked by user")
        self.sent.append((chat_id, text))


def run(coro):
    return asyncio.run(coro)


class TestEnabled:
    def test_disabled_without_a_chat_id(self):
        notifier = Notifier(FakeBot(), None)
        assert not notifier.enabled
        assert run(notifier.send("x")) is False

    def test_enabled_with_one(self):
        assert Notifier(FakeBot(), 42).enabled


def test_it_sends_to_the_configured_chat():
    bot = FakeBot()
    assert run(Notifier(bot, 42).send("hello")) is True
    assert bot.sent == [(42, "hello")]


def test_a_send_failure_never_raises():
    """The usual cause is the admin never having started the bot."""
    notifier = Notifier(FakeBot(explode=True), 42)
    assert run(notifier.send("hello")) is False


class TestThrottle:
    def test_it_stops_after_the_hourly_cap(self):
        bot = FakeBot()
        notifier = Notifier(bot, 42, max_per_hour=3)
        results = [run(notifier.send(f"alert {i}")) for i in range(5)]
        assert results == [True, True, True, False, False]
        assert len(bot.sent) == 3

    def test_the_budget_frees_up_over_time(self):
        bot = FakeBot()
        notifier = Notifier(bot, 42, max_per_hour=1)
        assert run(notifier.send("first"))
        assert not run(notifier.send("second"))
        notifier._sent[0] -= 3601          # age the record past the window
        assert run(notifier.send("third"))


def test_the_alert_text_carries_what_you_need():
    text = new_error_alert("a3f21c", "tiktok", "the site is not responding properly")
    assert "a3f21c" in text
    assert "tiktok" in text
    assert "/errors a3f21c" in text, "must say how to get the detail"
