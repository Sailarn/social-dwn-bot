"""Shrinking an oversized clip, and refusing to when it would be wrong."""

import shutil
import subprocess

import pytest

from src.core.config import Config
from src.core.errors import ClipRejected, ClipUnavailable
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



class TestMakePlayable:
    """Against real ffmpeg: Apify's shape is a VP9 video-only stream plus a
    separate AAC file, which must come out as one H.264 MP4 with audio."""

    @pytest.fixture
    def clips(self, tmp_path):
        if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            pytest.skip("needs ffmpeg")
        encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                                  capture_output=True, text=True).stdout
        if "libvpx-vp9" not in encoders or "libx264" not in encoders:
            pytest.skip("needs libvpx-vp9 and libx264")

        def make(name, *args):
            path = tmp_path / name
            subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(path)], check=True)
            return path

        h264 = make("h264.mp4", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30",
                    "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p")
        return {
            "vp9": make("vp9.mp4", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=60",
                        "-t", "1", "-c:v", "libvpx-vp9", "-deadline", "realtime"),
            "h264": h264,
            "audio": make("audio.m4a", "-f", "lavfi", "-i", "sine=frequency=440",
                          "-t", "1", "-c:a", "aac"),
            # Side data (a phone's rotation matrix) made ffprobe's csv output
            # "h264," — which is not "h264", so it was converted for nothing.
            "rotated": make("rotated.mp4", "-display_rotation", "90", "-i", str(h264),
                            "-c", "copy"),
        }

    def streams(self, path):
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "stream=codec_type,codec_name", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True).stdout
        return sorted(line.strip() for line in out.splitlines() if line.strip())

    def test_vp9_with_separate_audio_becomes_h264_with_audio(self, clips, config):
        result = transcode.make_playable(clips["vp9"], clips["audio"], config)
        assert self.streams(result) == ["aac,audio", "h264,video"]

    def test_vp9_alone_is_converted(self, clips, config):
        result = transcode.make_playable(clips["vp9"], None, config)
        assert self.streams(result) == ["h264,video"]

    def test_h264_with_its_audio_is_left_alone(self, clips, config):
        assert transcode.make_playable(clips["h264"], None, config) == clips["h264"]

    def test_h264_with_side_data_is_still_recognised(self, clips, config):
        assert transcode.video_codec(clips["rotated"]) == "h264"
        assert transcode.make_playable(clips["rotated"], None, config) == clips["rotated"]

    def test_h264_with_separate_audio_is_joined(self, clips, config):
        result = transcode.make_playable(clips["h264"], clips["audio"], config)
        assert self.streams(result) == ["aac,audio", "h264,video"]

    def test_unreadable_input_fails_so_the_pipeline_can_move_on(self, tmp_path, config):
        if not shutil.which("ffmpeg"):
            pytest.skip("needs ffmpeg")
        junk = tmp_path / "junk.mp4"
        junk.write_bytes(b"not a video")
        with pytest.raises(ClipUnavailable):
            transcode.make_playable(junk, None, config)
