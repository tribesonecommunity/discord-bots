import logging
from typing import Optional

from discord import Colour, Embed, Interaction, app_commands
from discord.ext.commands import Bot
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.checks import is_admin_app_command, is_command_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.models import Position, Session

_log = logging.getLogger(__name__)


class PositionCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(name="position", description="Position commands")

    async def position_autocomplete(self, interaction: Interaction, current: str):
        """
        Autocomplete for position names
        """
        session: SQLAlchemySession
        with Session() as session:
            positions = (
                session.query(Position)
                .filter(Position.name.ilike(f"%{current}%"))
                .order_by(Position.name)
                .limit(24)
                .all()
            )
            return [
                app_commands.Choice(name=position.name, value=position.name)
                for position in positions
            ]

    @group.command(name="add", description="Add a new position")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(name="Name of the position")
    async def add(self, interaction: Interaction, name: str):
        """
        Add a new position
        """
        session: SQLAlchemySession
        with Session() as session:
            try:
                position = Position(name=name)
                session.add(position)
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Added position **{name}**",
                        colour=Colour.green(),
                    )
                )
            except Exception as e:
                session.rollback()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Error adding position **{name}**: {str(e)}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )

    @group.command(name="remove", description="Remove a position")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(name="Name of the position")
    async def remove(self, interaction: Interaction, name: str):
        """
        Remove a position
        """
        session: SQLAlchemySession
        with Session() as session:
            try:
                position = session.query(Position).filter(Position.name == name).one()
                session.delete(position)
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Removed position **{name}**",
                        colour=Colour.green(),
                    )
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find position **{name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
            except Exception as e:
                session.rollback()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Error removing position **{name}**: {str(e)}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )

    @group.command(name="list", description="List all positions")
    async def list_positions(self, interaction: Interaction):
        """
        List all positions
        """
        session: SQLAlchemySession
        with Session() as session:
            positions = session.query(Position).order_by(Position.name).all()
            if not positions:
                await interaction.response.send_message(
                    embed=Embed(
                        description="No positions found",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

            position_list = "\n".join([f"• {position.name}" for position in positions])
            await interaction.response.send_message(
                embed=Embed(
                    title="Positions",
                    description=position_list,
                    colour=Colour.blue(),
                ),
                ephemeral=True,
            )
