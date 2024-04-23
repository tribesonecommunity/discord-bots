import contextlib
import logging
import os
from logging.handlers import RotatingFileHandler

import discord
from dotenv import load_dotenv

_log = logging.getLogger(__name__)
load_dotenv()
CONFIG_IS_VALID: bool = True


@contextlib.contextmanager
def setup_logging(log_level: str):
    log = logging.getLogger()

    try:
        discord.utils.setup_logging(level=logging.getLevelName(log_level))
        # __enter__
        max_bytes = 32 * 1024 * 1024  # 32 MiB
        logging.getLogger("discord").setLevel(logging.INFO)
        logging.getLogger("discord.http").setLevel(logging.WARNING)
        if log_level == "DEBUG":
            # set these to WARNING manually if you don't want to see emitted SQL when log_level == DEBUG
            logging.getLogger("sqlalchemy.engine").setLevel(log_level)
            logging.getLogger("sqlalchemy.pool").setLevel(log_level)
        else:
            logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
            logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)

        handler = RotatingFileHandler(
            filename="bot.log",
            encoding="utf-8",
            mode="w",
            maxBytes=max_bytes,
            backupCount=2,
        )
        dt_fmt = "%Y-%m-%d %H:%M:%S"
        fmt = logging.Formatter(
            "[{asctime}] [{levelname:<7}] {name}: {message}", dt_fmt, style="{"
        )
        handler.setFormatter(fmt)
        log.addHandler(handler)

        yield
    finally:
        # __exit__
        handlers = log.handlers[:]
        for hdlr in handlers:
            hdlr.close()
            log.removeHandler(hdlr)

def _to_str(key: str, required: bool = False, default: str | None = None) -> str | None:
    value = os.getenv(key)
    if not value and default is not None:
        return default
    elif required and not value:
        global CONFIG_IS_VALID
        CONFIG_IS_VALID = False
        _log.error(f"{key} must be specified correctly, was '{value}'")
        return None
    else:
        return value


def _to_int(key: str, required: bool = False, default: int | None = None) -> int | None:
    value = os.getenv(key)
    try:
        return int(value)
    except:
        if required and default is None:
            global CONFIG_IS_VALID
            CONFIG_IS_VALID = False
            _log.error(f"{key} must be specified correctly, was '{value}'")
        return default


def _to_float(
    key: str, required: bool = False, default: float | None = None
) -> float | None:
    value = os.getenv(key)
    try:
        return float(value)
    except:
        if required and default is None:
            global CONFIG_IS_VALID
            CONFIG_IS_VALID = False
            _log.error(f"{key} must be specified correctly, was '{value}'")
        return default


def _to_bool(
    key: str, required: bool = False, default: bool | None = None
) -> bool | None:
    value = os.getenv(key)
    if value is not None and value.lower() == "true":
        return True
    elif value is not None and value.lower() == "false":
        return False
    else:
        if required and default is None:
            global CONFIG_IS_VALID
            CONFIG_IS_VALID = False
            _log.error(f"{key} must be specified correctly, was '{value}'")
        return default


def _convert_to_int(value: str) -> int | None:
    try:
        return int(value)
    except:
        return None


# Discord setup

# Use "sqlite:///tribes.db" for sqlite
# Use "postgresql://postgres:password@localhost:5432/postgres" for postgres
LOG_LEVEL: str = _to_str(key="LOG_LEVEL", default="INFO")
DATABASE_URI: str = _to_str(key="DATABASE_URI", required=False)
DB_NAME = "tribes"
API_KEY: str = _to_str(key="DISCORD_API_KEY", required=True)
CHANNEL_ID: int = _to_int(key="CHANNEL_ID", required=True)
TRIBES_VOICE_CATEGORY_CHANNEL_ID: int = _to_int(
    key="TRIBES_VOICE_CATEGORY_CHANNEL_ID", required=True
)
__admin_ids = (
    os.getenv("SEED_ADMIN_IDS").split(",") if os.getenv("SEED_ADMIN_IDS") else []
)
SEED_ADMIN_IDS: list[int] = list(
    filter(lambda x: x is not None, map(_convert_to_int, __admin_ids))
)
ENABLE_VOICE_MOVE: bool = _to_bool(key="ENABLE_VOICE_MOVE", default=False)
DEFAULT_VOICE_MOVE: bool = _to_bool(key="DEFAULT_VOICE_MOVE", default=False)
VOICE_MOVE_LOBBY: int = _to_int(key="VOICE_MOVE_LOBBY", required=False)
ALLOW_VULGAR_NAMES: bool = _to_bool(key="ALLOW_VULGAR_NAMES", default=False)
ENABLE_DEBUG: bool = _to_bool(key="ENABLE_DEBUG", default=False)
ENABLE_RAFFLE: bool = _to_bool(key="ENABLE_RAFFLE", default=False)
SHOW_TRUESKILL: bool = _to_bool(key="SHOW_TRUESKILL", default=False)
SHOW_LEFT_RIGHT_TEAM: bool = _to_bool(key="SHOW_LEFT_RIGHT_TEAM", default=False)
SHOW_CAPTAINS: bool = _to_bool(key="SHOW_CAPTAINS", default=False)
DISABLE_MAP_ROTATION: bool = _to_bool(key="DISABLE_MAP_ROTATION", default=False)
MAXIMUM_TEAM_COMBINATIONS = _to_int("MAXIMUM_TEAM_COMBINATIONS")
LEADERBOARD_CHANNEL = _to_int(key="LEADERBOARD_CHANNEL")
RE_ADD_DELAY: int = _to_int(key="RE_ADD_DELAY", default=30)
REQUIRE_ADD_TARGET: bool = _to_bool(key="REQUIRE_ADD_TARGET", default=False)
COMMAND_PREFIX: str = _to_str(key="COMMAND_PREFIX", default="!")
DEFAULT_TRUESKILL_MU = _to_float(key="DEFAULT_TRUESKILL_MU", default=25)
DEFAULT_TRUESKILL_SIGMA = _to_float(
    key="DEFAULT_TRUESKILL_SIGMA", default=DEFAULT_TRUESKILL_MU / 3
)
AFK_TIME_MINUTES: int = _to_int(key="AFK_TIME_MINUTES", default=45)
MAP_ROTATION_MINUTES: int = _to_int(key="MAP_ROTATION_MINUTES", default=60)
DEFAULT_RAFFLE_VALUE: int = _to_int(key="DEFAULT_RAFFLE_VALUE", default=5)
DISABLE_PRIVATE_MESSAGES: bool = _to_bool(key="DISABLE_PRIVATE_MESSAGES", default=False)
TWITCH_GAME_NAME: str | None = _to_str(key="TWITCH_GAME_NAME")
TWITCH_CLIENT_ID: str | None = _to_str(key="TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET: str | None = _to_str(key="TWITCH_CLIENT_SECRET")
TRIBES_VOICE_CATEGORY_CHANNEL_ID: int = _to_int(
    key="TRIBES_VOICE_CATEGORY_CHANNEL_ID", required=True
)
MAP_VOTE_THRESHOLD: int = _to_int(key="MAP_VOTE_THRESHOLD", default=7)
STATS_DIR: str | None = _to_str(key="STATS_DIR")
STATS_WIDTH = _to_int(key="STATS_WIDTH")
STATS_HEIGHT = _to_int(key="STATS_HEIGHT")
ECONOMY_ENABLED: bool = _to_bool(key="ECONOMY_ENABLED", default=False)
CURRENCY_NAME: str = _to_str(key="CURRENCY_NAME", default="Shazbucks")
STARTING_CURRENCY: int = _to_int(key="STARTING_CURRENCY", default=100)
PREDICTION_TIMEOUT: int = _to_int(key="PREDICTION_TIMEOUT", default=300)
CURRENCY_AWARD: int = _to_int(key="CURRENCY_AWARD", default=25)
GAME_HISTORY_CHANNEL: int = _to_int(key="GAME_HISTORY_CHANNEL", required=True)
ADMIN_AUTOSUB: bool = _to_bool(key="ADMIN_AUTOSUB", default=False)
# TODO grouping here and in docs
