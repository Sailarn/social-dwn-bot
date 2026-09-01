"""Shared fixtures.

No test in the default run touches the network. Extraction is exercised against
recorded yt-dlp results in `fixtures/ytdlp_results.json`, captured once from real
calls, so the shapes are real without the tests depending on a site being up.
"""

import json
from pathlib import Path

import pytest
import yt_dlp

from src.core.config import Config

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_addoption(parser):
    parser.addoption(
        "--run-network", action="store_true", default=False,
        help="also run tests that hit real sites",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-network"):
        return
    skip = pytest.mark.skip(reason="needs --run-network")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def ytdlp_results() -> dict:
    return json.loads((FIXTURES / "ytdlp_results.json").read_text())


@pytest.fixture
def config() -> Config:
    """Defaults matching production, with a token so load_config is not needed."""
    return Config(bot_token="1:test")


def fake_ydl(result=None, error=None, on_download=None):
    """A stand-in for yt_dlp.YoutubeDL that returns a canned result."""

    class FakeYoutubeDL:
        instances = []

        def __init__(self, options):
            self.options = options
            self.downloaded = []
            FakeYoutubeDL.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False, process=True):
            if error is not None:
                raise error
            return result

        def download(self, urls):
            self.downloaded.extend(urls)
            if on_download is not None:
                on_download(self, urls)

        def process_ie_result(self, info, download=False):
            if on_download is not None:
                on_download(self, [info])

    return FakeYoutubeDL


def download_error(message: str) -> Exception:
    return yt_dlp.utils.DownloadError(message)
