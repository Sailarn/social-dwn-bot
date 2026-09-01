"""Shrinking an oversized clip, and refusing to when it would be wrong."""

import pytest

from src.core.config import Config
from src.core.errors import ClipRejected
from src.media import transcode


@pytest.fixture
def config():
    return Config(bot_token="x", max_filesize_mb=50, min_free_memory_mb=0)


def test_refuses_without_ffmpeg(tmp_path, monkeypatch, config):
    monkeypatch.setattr(transcode.shutil, "which", lambda name: None)
    with pytest.raises(ClipRejected) as caught:
        transcode.shrink_to_limit(tmp_path / "x.mp4", 30, config)
    assert caught.value.reason == "no_ffmpeg"


def test_refuses_without_a_known_duration(tmp_path, monkeypatch, config):
    """Bitrate cannot be computed, so the output size would be a guess."""
    monkeypatch.setattr(transcode.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    with pytest.raises(ClipRejected) as caught:
        transcode.shrink_to_limit(tmp_path / "x.mp4", 0, config)
    assert caught.value.reason == "too_large"


def test_refuses_when_the_result_would_be_unwatchable(tmp_path, monkeypatch, config):
    monkeypatch.setattr(transcode.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    with pytest.raises(ClipRejected) as caught:
        transcode.shrink_to_limit(tmp_path / "x.mp4", 100_000, config)
    assert caught.value.reason == "too_long_to_fit"


class TestBitrateBudget:
    def test_a_longer_clip_gets_a_lower_bitrate(self, config):
        assert transcode._bitrate_budget_kbps(30, config) > \
               transcode._bitrate_budget_kbps(300, config)

    def test_the_budget_leaves_room_for_audio(self, config):
        total = (config.max_filesize_bytes - transcode.SIZE_TARGET_MARGIN_BYTES) * 8 / 60 / 1000
        assert transcode._bitrate_budget_kbps(60, config) == \
            int(total) - transcode.AUDIO_BITRATE_KBPS


class TestCommand:
    def test_it_is_niced_so_the_co_hosted_app_keeps_priority(self, tmp_path):
        command = transcode._ffmpeg_command(tmp_path / "a.mp4", tmp_path / "b.mp4", 800)
        assert command[:3] == ["nice", "-n", "10"]

    def test_faststart_so_telegram_can_stream_it(self, tmp_path):
        command = transcode._ffmpeg_command(tmp_path / "a.mp4", tmp_path / "b.mp4", 800)
        assert "+faststart" in command
        assert command[command.index("-vf") + 1].startswith("scale=")

    def test_it_is_an_argument_list_not_a_shell_string(self, tmp_path):
        """Nothing to inject: the filename never reaches a shell."""
        command = transcode._ffmpeg_command(tmp_path / "a; rm -rf /.mp4",
                                            tmp_path / "b.mp4", 800)
        assert isinstance(command, list)
        assert any("rm -rf" in part for part in command), "kept as one literal argument"


def test_configure_sets_the_number_of_parallel_encodes():
    transcode.configure(3)
    assert transcode._transcode_slots._value == 3
    transcode.configure(1)
    assert transcode._transcode_slots._value == 1
