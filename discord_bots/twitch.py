import discord_bots.config as config
from twitchAPI.twitch import Twitch

twitch: Twitch | None = None
if config.TWITCH_GAME_NAME and config.TWITCH_CLIENT_ID and config.TWITCH_CLIENT_SECRET:
    twitch = Twitch(
        app_id=config.TWITCH_CLIENT_ID, app_secret=config.TWITCH_CLIENT_SECRET
    )
