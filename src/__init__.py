"""social-download-tg."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("social-download-tg")
except PackageNotFoundError:      # running from a checkout, not installed
    __version__ = "1.0.0"
