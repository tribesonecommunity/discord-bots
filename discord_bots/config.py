import os
from dotenv import load_dotenv


load_dotenv()

ALLOW_VULGAR_NAMES = bool(os.getenv("ALLOW_VULGAR_NAMES"))
ENABLE_DEBUG = bool(os.getenv("ENABLE_DEBUG"))
ENABLE_RAFFLE = bool(os.getenv("ENABLE_RAFFLE"))
SHOW_TRUESKILL = bool(os.getenv("SHOW_TRUESKILL"))
SHOW_LEFT_RIGHT_TEAM = bool(os.getenv("SHOW_LEFT_RIGHT_TEAM"))
DISABLE_MAP_ROTATION = bool(os.getenv("DISABLE_MAP_ROTATION"))

# Number of team combinations to try. Needed to prune large game modes like 12v12
MAXIMUM_TEAM_COMBINATIONS = int(os.getenv("MAXIMUM_TEAM_COMBINATIONS") or "0")

LEADERBOARD_CHANNEL = int(os.getenv("LEADERBOARD_CHANNEL") or "0")

# The time between games
RE_ADD_DELAY = int(os.getenv("RE_ADD_DELAY") or "30")

# Requiring specifying a queue to add to
REQUIRE_ADD_TARGET = bool(os.getenv("REQUIRE_ADD_TARGET"))
