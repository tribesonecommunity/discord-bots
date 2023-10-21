import os
from dotenv import load_dotenv


load_dotenv()

SHOW_TRUESKILL = bool(os.getenv("SHOW_TRUESKILL"))
DISABLE_MAP_ROTATION = bool(os.getenv("DISABLE_MAP_ROTATION"))
LEADERBOARD_CHANNEL = int(os.getenv("LEADERBOARD_CHANNEL"))