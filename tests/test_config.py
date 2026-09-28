"""Configuration parsing and who is allowed to use the bot."""

import base64
import stat

import pytest

from src.core.config import Config, load_config

ME, FRIEND = 111111111, 111111
GROUP, OTHER_GROUP = -1001234567890, -1009999999999


def test_missing_token_exits_with_a_readable_message(capsys, monkeypatch):
    """The first thing a new user can get wrong; it must not be a traceback."""
    monkeypatch.setenv("BOT_TOKEN", "")
    with pytest.raises(SystemExit) as caught:
        load_config()
    assert caught.value.code == 1
    message = capsys.readouterr().err
    assert "BOT_TOKEN is not set" in message
    assert ".env.example" in message


def test_filesize_is_clamped_to_telegrams_ceiling(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("MAX_FILESIZE_MB", "500")
    assert load_config().max_filesize_mb == 50


def test_ids_parse_from_mixed_separators_including_negatives(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("ALLOWED_USER_IDS", "1, 2 3")
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "-1001234567890,-1009999999999")
    config = load_config()
    assert config.allowed_user_ids == {1, 2, 3}
    assert config.allowed_chat_ids == {-1001234567890, -1009999999999}


def test_missing_cookies_file_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("COOKIES_FILE", str(tmp_path / "nope.txt"))
    assert load_config().cookies_file is None


def test_pending_updates_are_dropped_by_default(monkeypatch):
    """The Pi's behaviour: a long outage's backlog is stale by the time it is back."""
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.delenv("DROP_PENDING_UPDATES", raising=False)
    assert load_config().drop_pending_updates is True


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "FALSE"])
def test_pending_updates_can_be_kept(monkeypatch, raw):
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("DROP_PENDING_UPDATES", raw)
    assert load_config().drop_pending_updates is False


class TestCookiesFromEnvironment:
    """Hosts with an ephemeral disk pass the cookies in an environment variable."""

    CONTENT = "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc\n"

    @pytest.fixture
    def env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BOT_TOKEN", "1:x")
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("COOKIES_FILE", "")
        monkeypatch.setenv("COOKIES_B64", base64.b64encode(self.CONTENT.encode()).decode())
        return tmp_path

    def test_is_written_into_the_data_dir(self, env):
        path = load_config().cookies_file
        assert path == env / "data" / "cookies.txt"
        assert path.read_text() == self.CONTENT

    def test_file_is_private(self, env):
        mode = stat.S_IMODE(load_config().cookies_file.stat().st_mode)
        assert mode == 0o600

    def test_an_explicit_file_wins(self, env, monkeypatch):
        explicit = env / "mine.txt"
        explicit.write_text("# Netscape HTTP Cookie File\n")
        monkeypatch.setenv("COOKIES_FILE", str(explicit))
        assert load_config().cookies_file == explicit
        assert not (env / "data" / "cookies.txt").exists()

    def test_bad_base64_is_ignored_not_fatal(self, env, monkeypatch):
        monkeypatch.setenv("COOKIES_B64", "not base64!")
        assert load_config().cookies_file is None

    def test_neither_set_means_no_cookies(self, env, monkeypatch):
        monkeypatch.setenv("COOKIES_B64", "")
        assert load_config().cookies_file is None


class TestAccessControl:
    def test_both_lists_empty_is_open_to_everyone(self):
        config = Config(bot_token="x")
        assert config.is_open_to_everyone
        assert config.is_allowed(FRIEND, OTHER_GROUP)

    def test_user_list_grants_access_in_any_chat(self):
        config = Config(bot_token="x", allowed_user_ids={ME})
        assert config.is_allowed(ME, GROUP)
        assert config.is_allowed(ME, ME)
        assert not config.is_allowed(FRIEND, GROUP)

    def test_chat_list_grants_access_to_everyone_in_that_chat(self):
        config = Config(bot_token="x", allowed_chat_ids={GROUP})
        assert config.is_allowed(FRIEND, GROUP)
        assert not config.is_allowed(FRIEND, OTHER_GROUP)

    def test_chat_access_does_not_leak_into_dms(self):
        """A group member may use it in the group, but cannot DM the bot."""
        config = Config(bot_token="x", allowed_chat_ids={GROUP})
        assert config.is_allowed(FRIEND, GROUP)
        assert not config.is_allowed(FRIEND, FRIEND)

    def test_either_list_grants_access(self):
        config = Config(bot_token="x", allowed_user_ids={ME}, allowed_chat_ids={GROUP})
        assert config.is_allowed(ME, OTHER_GROUP)
        assert config.is_allowed(FRIEND, GROUP)
        assert not config.is_allowed(FRIEND, OTHER_GROUP)
        assert not config.is_open_to_everyone


class TestAdminGating:
    def test_no_admins_means_nobody_is_admin(self):
        """Fails closed: the bot may be open, usage data must not be."""
        config = Config(bot_token="x")
        assert not config.is_admin(ME)
        assert not config.is_admin(FRIEND)

    def test_only_listed_admins_qualify(self):
        config = Config(bot_token="x", admin_user_ids={ME})
        assert config.is_admin(ME)
        assert not config.is_admin(FRIEND)

    def test_being_allowed_does_not_make_you_an_admin(self):
        config = Config(bot_token="x", allowed_user_ids={ME, FRIEND},
                        admin_user_ids={ME})
        assert config.is_allowed(FRIEND, GROUP)
        assert not config.is_admin(FRIEND)

    def test_admins_parse_from_env(self, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN", "1:x")
        monkeypatch.setenv("ADMIN_USER_IDS", "111111111")
        assert load_config().admin_user_ids == {111111111}

    def test_salt_defaults_to_the_token_so_hashes_are_stable(self, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN", "1:secret")
        monkeypatch.delenv("EVENT_SALT", raising=False)
        assert load_config().event_salt == "1:secret"
