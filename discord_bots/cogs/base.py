from discord import Colour, Message
from discord.ext.commands import Cog, Context

from discord_bots.utils import send_message


class BaseCog(Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_before_invoke(self, ctx: Context):
        self.message = ctx.message

    async def send_success_message(self, success_message):
        await send_message(
            self.message.channel,
            embed_description=success_message,
            colour=Colour.green(),
        )

    async def send_error_message(self, error_message):
        await send_message(
            self.message.channel, embed_description=error_message, colour=Colour.red()
        )
