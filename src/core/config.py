"""Runtime configuration, read once from the environment."""

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from src.core.limits import TELEGRAM_UPLOAD_LIMIT_MB

log = logging.getLogger(__name__)

def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def _id_set_env(name: str) -> set[int]:
    raw = os.environ.get(name, "").strip()
    return {int(part) for part in raw.replace(",", " ").split()} if raw else set()


@dataclass(frozen=True)
class Config:
    bot_token: str
    allowed_user_ids: set[int] = field(default_factory=set)
    allowed_chat_ids: set[int] = field(default_factory=set)
    admin_user_ids: set[int] = field(default_factory=set)
    alert_chat_id: int | None = None
    max_duration_seconds: int = 300
    max_filesize_mb: int = TELEGRAM_UPLOAD_LIMIT_MB
    max_concurrent_downloads: int = 3
    max_concurrent_transcodes: int = 1
    chat_send_interval_seconds: float = 3.0
    platform_interval_seconds: float = 2.0
    platform_cooldown_seconds: float = 60.0
    min_free_disk_mb: int = 500
    min_free_memory_mb: int = 200
    cache_ttl_days: int = 30
    stats_retention_days: int = 90
    rate_limit_per_hour: int = 30
    download_attempts: int = 3
    data_dir: Path = Path("data")
    cookies_file: Path | None = None
    health_port: int = 7860
    health_host: str = "0.0.0.0"
    event_salt: str = ""

    @property
    def max_filesize_bytes(self) -> int:
        return self.max_filesize_mb * 1024 * 1024

    @property
    def is_open_to_everyone(self) -> bool:
        return not self.allowed_user_ids and not self.allowed_chat_ids

    def is_admin(self, user_id: int) -> bool:
        """Admin commands fail closed: with no admins set, nobody qualifies.

        The bot may be open to everyone, so an ungated /stats would hand usage
        data to strangers.
        """
        return user_id in self.admin_user_ids

    def is_allowed(self, user_id: int, chat_id: int) -> bool:
        """A user may be trusted directly, or by being in a trusted chat."""
        if self.is_open_to_everyone:
            return True
        return user_id in self.allowed_user_ids or chat_id in self.allowed_chat_ids


def _resolve_cookies(configured_path: str) -> Path | None:
    """A cookies file that was asked for but is missing must not pass silently."""
    if not configured_path:
        return None
    path = Path(configured_path)
    if not path.is_file():
        log.warning("COOKIES_FILE is set to %s but there is no file there; "
                    "continuing without cookies", path)
        return None
    return path


def load_config() -> Config:
    # Docker and systemd load .env for us; PM2 and a bare shell do not.
    # Real environment variables still win over the file.
    load_dotenv()

    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        # The first thing a new user can get wrong, so say it plainly rather
        # than showing them a traceback.
        print("BOT_TOKEN is not set.\n"
              "  cp .env.example .env   then put your @BotFather token in it",
              file=sys.stderr)
        raise SystemExit(1)

    cookies = _resolve_cookies(os.environ.get("COOKIES_FILE", "").strip())

    admins = _id_set_env("ADMIN_USER_IDS")
    requested_mb = _int_env("MAX_FILESIZE_MB", TELEGRAM_UPLOAD_LIMIT_MB)

    return Config(
        bot_token=token,
        allowed_user_ids=_id_set_env("ALLOWED_USER_IDS"),
        allowed_chat_ids=_id_set_env("ALLOWED_CHAT_IDS"),
        admin_user_ids=admins,
        # Alerts go to ALERT_CHAT_ID, or to the single admin if there is
        # one, so the common case needs no extra configuration.
        alert_chat_id=_int_env("ALERT_CHAT_ID", 0) or (min(admins) if admins else None),
        max_duration_seconds=_int_env("MAX_DURATION_SECONDS", 300),
        max_filesize_mb=min(requested_mb, TELEGRAM_UPLOAD_LIMIT_MB),
        max_concurrent_downloads=_int_env("MAX_CONCURRENT_DOWNLOADS", 3),
        max_concurrent_transcodes=_int_env("MAX_CONCURRENT_TRANSCODES", 1),
        chat_send_interval_seconds=_float_env("CHAT_SEND_INTERVAL_SECONDS", 3.0),
        platform_interval_seconds=_float_env("PLATFORM_INTERVAL_SECONDS", 2.0),
        platform_cooldown_seconds=_float_env("PLATFORM_COOLDOWN_SECONDS", 60.0),
        min_free_disk_mb=_int_env("MIN_FREE_DISK_MB", 500),
        min_free_memory_mb=_int_env("MIN_FREE_MEMORY_MB", 200),
        cache_ttl_days=_int_env("CACHE_TTL_DAYS", 30),
        stats_retention_days=_int_env("STATS_RETENTION_DAYS", 90),
        rate_limit_per_hour=_int_env("RATE_LIMIT_PER_HOUR", 30),
        download_attempts=_int_env("DOWNLOAD_ATTEMPTS", 3),
        data_dir=Path(os.environ.get("DATA_DIR", "data")),
        cookies_file=cookies,
        health_port=_int_env("PORT", 7860),
        health_host=os.environ.get("HEALTH_HOST", "0.0.0.0").strip() or "0.0.0.0",
        # Identifiers in the event log are hashed, never stored raw. Deriving the
        # salt from the token keeps it stable across restarts with no extra config.
        event_salt=os.environ.get("EVENT_SALT", "").strip() or token,
    )
