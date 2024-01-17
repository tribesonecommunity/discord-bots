import os
from dotenv import load_dotenv


load_dotenv()

# Convenience function to print the id of a channel
SHOW_CHANNEL_ID = bool(os.getenv("SHOW_CHANNEL_ID"))
SHOW_TRUESKILL = bool(os.getenv("SHOW_TRUESKILL"))
DISABLE_MAP_ROTATION = bool(os.getenv("DISABLE_MAP_ROTATION"))
LEADERBOARD_CHANNEL = int(os.getenv("LEADERBOARD_CHANNEL") or "0")