"""Self-limiting against ceilings we cannot raise."""

import asyncio
import time

from src.core.pacing import Pacer, PlatformPacer


def test_the_first_call_does_not_wait():
    assert asyncio.run(Pacer(5).wait("chat")) == 0


def test_the_second_call_to_the_same_key_waits():
    async def scenario():
        pacer = Pacer(0.05)
        await pacer.wait("chat")
        return await pacer.wait("chat")

    assert asyncio.run(scenario()) > 0


def test_different_keys_do_not_block_each_other():
    """One busy group must not slow another."""
    async def scenario():
        pacer = Pacer(5)
        await pacer.wait("chat-a")
        return await pacer.wait("chat-b")

    assert asyncio.run(scenario()) == 0


def test_zero_interval_disables_pacing():
    async def scenario():
        pacer = Pacer(0)
        return [await pacer.wait("k") for _ in range(50)]

    assert asyncio.run(scenario()) == [0.0] * 50


def test_concurrent_callers_queue_rather_than_collide():
    """Slots are reserved under the lock, so three callers get three slots."""
    async def scenario():
        pacer = Pacer(0.05)
        started = time.monotonic()
        await asyncio.gather(*(pacer.wait("same") for _ in range(3)))
        return time.monotonic() - started

    # Three callers, two of them waiting one and two intervals.
    assert asyncio.run(scenario()) >= 0.09


class TestPlatformCooldown:
    def test_penalising_forces_a_wait(self):
        async def scenario():
            pacer = PlatformPacer(0, cooldown_seconds=0.08)
            pacer.penalise("tiktok")
            return await pacer.wait("tiktok")

        assert asyncio.run(scenario()) > 0

    def test_only_the_penalised_platform_is_slowed(self):
        async def scenario():
            pacer = PlatformPacer(0, cooldown_seconds=5)
            pacer.penalise("tiktok")
            return await pacer.wait("instagram")

        assert asyncio.run(scenario()) == 0

    def test_a_zero_cooldown_is_a_no_op(self):
        async def scenario():
            pacer = PlatformPacer(0, cooldown_seconds=0)
            pacer.penalise("tiktok")
            return await pacer.wait("tiktok")

        assert asyncio.run(scenario()) == 0

    def test_penalising_twice_does_not_shorten_the_cooldown(self):
        pacer = PlatformPacer(0, cooldown_seconds=10)
        pacer.penalise("tiktok")
        first = pacer._next_allowed["tiktok"]
        pacer._cooldown = 1
        pacer.penalise("tiktok")
        assert pacer._next_allowed["tiktok"] >= first


def test_a_cooldown_survives_spacing_being_disabled():
    """Setting the interval to 0 turns off spacing, not the throttle cooldown."""
    async def scenario():
        pacer = PlatformPacer(0, cooldown_seconds=0.08)
        pacer.penalise("tiktok")
        return await pacer.wait("tiktok")

    assert asyncio.run(scenario()) > 0


def test_keys_are_forgotten_once_nothing_is_pending():
    """Otherwise the dict grows one entry per chat, forever."""
    async def scenario():
        pacer = Pacer(0)
        for index in range(100):
            await pacer.wait(f"chat-{index}")
        return len(pacer._next_allowed)

    assert asyncio.run(scenario()) == 0
