import os
from dotenv import load_dotenv


load_dotenv()

SHOW_TRUESKILL = bool(os.getenv("SHOW_TRUESKILL"))
DISABLE_MAP_ROTATION = bool(os.getenv("DISABLE_MAP_ROTATION"))
print("get_env:", os.getenv("LEADERBOARD_CHANNEL"))
LEADERBOARD_CHANNEL = int(os.getenv("LEADERBOARD_CHANNEL") or "0")
print("channel:", os.getenv("LEADERBOARD_CHANNEL"))