import logging
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord import (
    app_commands,
    Colour,
    Embed,
    Interaction,
    Member,
    Role,
)
from discord.ext.commands import Bot

from discord_bots.checks import is_command_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.config import ENABLE_VOICE_MOVE
from discord_bots.models import (
    Player,
    Session
)

_log = logging.getLogger(__name__)


class PlayerCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(name="player", description="Player commands")

    @group.command(name="toggleleaderboard", description="Enable/disable showing on leaderbaord")
    @app_commands.check(is_command_channel)
    @app_commands.describe(option="True/False")
    async def toggleleaderboard(self, interaction: Interaction, option: bool):
        session: SQLAlchemySession
        with Session() as session:
            player = session.query(Player).filter(Player.id == interaction.user.id).first()
            if player:
                player.leaderboard_enabled = option
                session.commit()
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Player {interaction.user.display_name} not found",
                        colour=Colour.red(),
                    ),
                    ephemeral=True
                )
                return
        if option:
            await interaction.response.send_message(
                embed=Embed(
                    description="You are now visible on the leaderboard",
                    colour=Colour.blue(),
                ),
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=Embed(
                    description="You are no longer visible on the leaderboard",
                    colour=Colour.blue(),
                ),
                ephemeral=True
            )
    
    @group.command(name="togglestats", description="Enable/disable player stats")
    @app_commands.check(is_command_channel)
    @app_commands.describe(option="True/False")
    async def togglestats(self, interaction: Interaction, option: bool):
        session: SQLAlchemySession
        with Session() as session:
            player = session.query(Player).filter(Player.id == interaction.user.id).first()
            if player:
                player.stats_enabled = False
                session.commit()
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Player {interaction.user.display_name} not found",
                        colour=Colour.red(),
                    ),
                    ephemeral=True
                )
                return
        if option:
            await interaction.response.send_message(
                embed=Embed(
                    description="`/Stats` enabled",
                    colour=Colour.blue(),
                ),
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=Embed(
                    description="`/Stats` disabled",
                    colour=Colour.blue(),
                ),
                ephemeral=True
            )
    
    @group.command(name="togglevoicemove", description="Enable/disable voice movement")
    @app_commands.check(is_command_channel)
    @app_commands.describe(option="True/False")
    async def togglevoicemove(self, interaction: Interaction, option: bool):
        if not ENABLE_VOICE_MOVE:
            await interaction.response.send_message(
                embed=Embed(
                    description="Voice movement is disabled",
                    colour=Colour.red(),
                ),
                ephemeral=True
            )
            return
        
        session: SQLAlchemySession
        with Session() as session:
            player = session.query(Player).filter(Player.id == interaction.user.id).first()
            if player:
                player.move_enabled = option
                session.commit()
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Player {interaction.user.display_name} not found",
                        colour=Colour.red(),
                    ),
                    ephemeral=True
                )
                return

        if option:
            await interaction.response.send_message(
                embed=Embed(
                    description="Player moving enabled",
                    colour=Colour.blue(),
                ),
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=Embed(
                    description="Player moving disabled",
                    colour=Colour.blue(),
                ),
                ephemeral=True
            )
