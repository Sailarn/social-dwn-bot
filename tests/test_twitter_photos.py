"""X photos, which yt-dlp does not expose at all."""

import pytest

from src.media import twitter_photos


@pytest.mark.parametrize("url,expected", [
    ("https://x.com/503Vernn/status/2094307703338463301", "2094307703338463301"),
    ("https://twitter.com/u/status/123?s=20", "123"),
    ("https://x.com/u/statuses/456", "456"),
    ("https://x.com/u/", None),
    ("https://instagram.com/p/X/", None),
])
def test_tweet_id_extraction(url, expected):
    assert twitter_photos.tweet_id_from(url) == expected


def test_uses_x_own_endpoint_first(monkeypatch):
    calls = []

    def syndication(tweet_id):
        calls.append("syndication")
        return ["http://pbs/a.jpg"]

    monkeypatch.setattr(twitter_photos, "_from_syndication", syndication)
    monkeypatch.setattr(twitter_photos, "_from_fxtwitter",
                        lambda i: pytest.fail("mirror must not be reached"))
    assert twitter_photos.photo_urls("https://x.com/u/status/1") == ["http://pbs/a.jpg"]
    assert calls == ["syndication"]


def test_falls_back_to_the_mirror_when_x_fails(monkeypatch):
    monkeypatch.setattr(twitter_photos, "_from_syndication",
                        lambda i: (_ for _ in ()).throw(OSError("502")))
    monkeypatch.setattr(twitter_photos, "_from_fxtwitter", lambda i: ["http://pbs/b.jpg"])
    assert twitter_photos.photo_urls("https://x.com/u/status/1") == ["http://pbs/b.jpg"]


def test_falls_back_when_x_returns_nothing(monkeypatch):
    monkeypatch.setattr(twitter_photos, "_from_syndication", lambda i: [])
    monkeypatch.setattr(twitter_photos, "_from_fxtwitter", lambda i: ["http://pbs/c.jpg"])
    assert twitter_photos.photo_urls("https://x.com/u/status/1") == ["http://pbs/c.jpg"]


def test_both_failing_returns_empty_not_an_exception(monkeypatch):
    """The caller already has a good 'no media' message."""
    monkeypatch.setattr(twitter_photos, "_from_syndication",
                        lambda i: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(twitter_photos, "_from_fxtwitter",
                        lambda i: (_ for _ in ()).throw(ValueError("bad json")))
    assert twitter_photos.photo_urls("https://x.com/u/status/1") == []


def test_a_url_without_a_tweet_id_does_no_requests(monkeypatch):
    monkeypatch.setattr(twitter_photos, "_from_syndication",
                        lambda i: pytest.fail("must not be called"))
    assert twitter_photos.photo_urls("https://x.com/someone") == []


class TestFullSize:
    """pbs.twimg.com serves a downscaled variant unless asked otherwise."""

    def test_original_size_is_requested(self, monkeypatch):
        monkeypatch.setattr(twitter_photos, "_get_json", lambda url: {
            "photos": [{"url": "https://pbs.twimg.com/media/ABC.jpg"}]})
        assert twitter_photos._from_syndication("1") == \
            ["https://pbs.twimg.com/media/ABC.jpg?name=orig"]

    def test_an_existing_name_parameter_is_left_alone(self, monkeypatch):
        monkeypatch.setattr(twitter_photos, "_get_json", lambda url: {
            "photos": [{"url": "https://pbs.twimg.com/media/ABC.jpg?name=large"}]})
        assert twitter_photos._from_syndication("1") == \
            ["https://pbs.twimg.com/media/ABC.jpg?name=large"]

    def test_query_string_is_appended_correctly(self):
        assert twitter_photos._full_size("http://x/a.jpg?format=jpg") == \
            "http://x/a.jpg?format=jpg&name=orig"
