import logging
from math import floor
from random import randint, random

from discord import Colour, Embed, Interaction, app_commands
from discord.ext.commands import Bot

from discord_bots.checks import is_command_channel
from discord_bots.cogs.base import BaseCog

_log = logging.getLogger(__name__)


class RandomCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(name="random", description="Random commands")

    @group.command(name="coinflip", description="Flip a coin")
    @app_commands.check(is_command_channel)
    async def coinflip(self, interaction: Interaction):
        result = "HEADS" if floor(random() * 2) == 0 else "TAILS"
        await interaction.response.send_message(
            embed=Embed(
                description=result,
                colour=Colour.blue(),
            )
        )

    @group.command(name="roll", description="Roll a random number")
    @app_commands.check(is_command_channel)
    @app_commands.describe(low_range="Minimum roll", high_range="Maximum roll")
    async def roll(self, interaction: Interaction, low_range: int, high_range: int):
        await interaction.response.send_message(
            embed=Embed(
                description=f"You rolled: {randint(low_range, high_range)}",
                colour=Colour.blue(),
            )
        )
