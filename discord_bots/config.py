import os
from dotenv import load_dotenv


load_dotenv()

# Convenience function to print the id of a channel
ALLOW_VULGAR_NAMES = bool(os.getenv("ALLOW_VULGAR_NAMES"))
ENABLE_DEBUG = bool(os.getenv("ENABLE_DEBUG"))
SHOW_TRUESKILL = bool(os.getenv("SHOW_TRUESKILL"))
DISABLE_MAP_ROTATION = bool(os.getenv("DISABLE_MAP_ROTATION"))
LEADERBOARD_CHANNEL = int(os.getenv("LEADERBOARD_CHANNEL") or "0")