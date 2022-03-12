import os
from dotenv import load_dotenv


load_dotenv()

SHOW_TRUESKILL = bool(os.getenv("SHOW_TRUESKILL"))