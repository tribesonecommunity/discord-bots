import os
import dotenv
from twitchAPI.twitch import Twitch

dotenv.load_dotenv()

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

twitch = None
if TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET:
    twitch = Twitch(app_id=TWITCH_CLIENT_ID, app_secret=TWITCH_CLIENT_SECRET)
