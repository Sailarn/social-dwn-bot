"""Per-user hourly limit."""

from src.storage.ratelimit import RateLimiter


def test_allows_up_to_the_limit_then_refuses():
    limiter = RateLimiter(max_per_hour=3)
    assert [limiter.allow(1) for _ in range(5)] == [True, True, True, False, False]


def test_users_are_independent():
    limiter = RateLimiter(max_per_hour=1)
    assert limiter.allow(1)
    assert not limiter.allow(1)
    assert limiter.allow(2)


def test_zero_disables_the_limit():
    limiter = RateLimiter(max_per_hour=0)
    assert all(limiter.allow(1) for _ in range(200))


def test_window_slides():
    limiter = RateLimiter(max_per_hour=1)
    assert limiter.allow(7)
    assert not limiter.allow(7)
    limiter._history[7][0] -= 3601      # age the entry out of the window
    assert limiter.allow(7)


def test_seconds_until_free_is_within_the_hour():
    limiter = RateLimiter(max_per_hour=1)
    limiter.allow(3)
    assert 3500 < limiter.seconds_until_free(3) <= 3600


def test_seconds_until_free_is_zero_when_unused():
    assert RateLimiter(max_per_hour=5).seconds_until_free(99) == 0
