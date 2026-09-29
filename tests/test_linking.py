"""Which items Telegram may fetch by URL, and what happens when it cannot."""

import asyncio
import base64
import json

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendVideo

from fakes import FakeMessage
from src.core.models import ClipInfo, MediaItem, MediaKind, Source
from src.media import linking
from src.telegram import delivery
from src.telegram.services import Services


def cdn(tag: str | None, host="scontent-ham3-1.cdninstagram.com", ext="mp4") -> str:
    """An Instagram CDN link carrying an encoding tag, as the real ones do."""
    query = ""
    if tag is not None:
        efg = base64.urlsafe_b64encode(json.dumps({"vencode_tag": tag}).encode()).decode()
        query = f"?efg={efg.rstrip('=')}&oh=x"
    return f"https://{host}/o1/v/t2/f2/m86/file.{ext}{query}"


PROGRESSIVE = cdn("xpv_progressive.INSTAGRAM.CLIPS.C3.720.dash_baseline_1_v1")
VP9 = cdn("ig-xpvds.clips.c2-C3.dash_r2evevp9-r1gen2vp9-hfr_q90")
PHOTO = cdn(None, ext="jpg")


def video(url=PROGRESSIVE, **kwargs):
    return MediaItem(kind=MediaKind.VIDEO, video_url=url, duration_seconds=10, **kwargs)


def photo(url=PHOTO):
    return MediaItem(kind=MediaKind.PHOTO, image_url=url)


def post(*items, source=Source.APIFY):
    return ClipInfo(key="Instagram:X", title="t", items=tuple(items), source=source)


@pytest.fixture
def heads(monkeypatch):
    """HEAD answers by URL; every link is fine unless a test says otherwise."""
    answers = {}

    def head(url):
        default = ("image/jpeg", 90_000) if url.split("?")[0].endswith(".jpg") else (
            "video/mp4", 4_000_000)
        return answers.get(url, default)

    monkeypatch.setattr(linking, "_head", head)
    return answers


class TestRules:
    def test_a_progressive_video_is_linked(self, heads):
        [link] = linking.link_items(post(video()))
        assert link.url == PROGRESSIVE and link.size == 4_000_000

    def test_vp9_is_never_linked(self, heads):
        """Telegram would get it without any codec check from us: a frozen frame."""
        assert linking.link_items(post(video(VP9))) is None

    def test_a_video_without_an_encoding_tag_is_not_trusted(self, heads):
        assert linking.link_items(post(video(cdn(None)))) is None

    def test_split_audio_needs_joining_so_cannot_be_linked(self, heads):
        assert linking.link_items(post(video(audio_url=cdn(None, ext="m4a")))) is None

    def test_photos_are_linked(self, heads):
        [link] = linking.link_items(post(photo()))
        assert link.url == PHOTO

    @pytest.mark.parametrize("head", [
        ("video/mp4", 21 * 1024 * 1024),   # over Telegram's 20 MB for URLs
        ("video/mp4", 0),                  # size unknown
        ("text/html", 4_000_000),          # an error page, not the file
        None,                              # HEAD failed
    ])
    def test_a_video_telegram_would_refuse_is_downloaded_instead(self, heads, head):
        heads[PROGRESSIVE] = head
        assert linking.link_items(post(video())) is None

    def test_photos_have_telegrams_smaller_limit(self, heads):
        heads[PHOTO] = ("image/jpeg", 6 * 1024 * 1024)
        assert linking.link_items(post(photo())) is None

    def test_only_instagram_cdn_hosts(self, heads):
        other = cdn("xpv_progressive.720", host="video.twimg.com")
        assert linking.link_items(post(video(other))) is None

    def test_only_apify_results(self, heads):
        """yt-dlp results carry no direct URL we have vetted."""
        assert linking.link_items(post(video(), source=Source.COOKIES)) is None

    def test_a_carousel_is_linked_whole_or_not_at_all(self, heads):
        assert len(linking.link_items(post(video(), photo(), video()))) == 3
        assert linking.link_items(post(video(), video(VP9))) is None

    def test_reads_the_tag_from_a_real_link_shape(self):
        assert "progressive" in linking._encoding_tag(PROGRESSIVE)
        assert linking._encoding_tag("https://x.cdninstagram.com/v.mp4") == ""


class TestDelivery:
    @pytest.fixture
    def services(self, tmp_path):
        from fakes import FakeNotifier
        from src.core.pacing import Pacer, PlatformPacer
        from src.storage.cache import FileIdCache
        from src.storage.database import Database
        from src.storage.stats import EventLog
        return Services(cache=FileIdCache(Database.local(tmp_path / "c.db"), 30),
                        events=EventLog(Database.local(tmp_path / "e.db"), 90, "s"),
                        chat_pacer=Pacer(0), platform_pacer=PlatformPacer(0, 0),
                        notifier=FakeNotifier())

    def deliver(self, message, services, info):
        attempt = type("Attempt", (), {})()
        downloads = []

        async def download(clip, workdir):
            downloads.append(clip)
            path = workdir / "v.mp4"
            path.write_bytes(b"x" * 10)
            from src.core.models import DownloadedItem
            return clip, [DownloadedItem(path=path, item=clip.items[0])]

        attempt.download = download
        result = asyncio.run(delivery._download_and_send(message, services, attempt, info))
        return result, downloads

    def test_a_linked_video_is_sent_as_its_url(self, heads, services):
        message = FakeMessage()
        (_, file_id, size), downloads = self.deliver(message, services, post(video()))
        assert message.videos == [PROGRESSIVE]
        assert downloads == [], "nothing downloaded"
        assert (file_id, size) == ("VIDEOID", 4_000_000)

    def test_when_telegram_cannot_fetch_it_the_file_is_uploaded(self, heads, services):
        class RefusingMessage(FakeMessage):
            async def reply_video(self, media, **kwargs):
                if isinstance(media, str):
                    raise TelegramBadRequest(method=SendVideo(chat_id=1, video=media),
                                             message="failed to get HTTP URL content")
                return await super().reply_video(media, **kwargs)

        message = RefusingMessage()
        (_, file_id, _), downloads = self.deliver(message, services, post(video()))
        assert len(downloads) == 1
        assert len(message.videos) == 1 and not isinstance(message.videos[0], str)
        assert file_id == "VIDEOID"
