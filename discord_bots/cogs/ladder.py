import logging

from discord import Colour, Embed, Interaction, TextChannel, app_commands
from discord.ext.commands import Bot
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.checks import is_admin_app_command, is_ladder_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.models import Ladder, Rotation, Session
from discord_bots.utils import ladder_autocomplete, rotation_autocomplete

_log = logging.getLogger(__name__)

MAX_MAPS_PER_MATCH = 5


class LadderCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    ladder_group = app_commands.Group(
        name="ladder", description="Challenge ladder commands"
    )
    admin_group = app_commands.Group(
        name="admin", parent=ladder_group, description="Ladder admin commands"
    )

    @ladder_group.command(name="list", description="List all ladders")
    @app_commands.check(is_ladder_channel)
    async def list_ladders(self, interaction: Interaction):
        session: SQLAlchemySession
        with Session() as session:
            ladders: list[Ladder] = session.query(Ladder).order_by(Ladder.name).all()
            if not ladders:
                await interaction.response.send_message(
                    embed=Embed(
                        description="No ladders configured.",
                        colour=Colour.blue(),
                    )
                )
                return

            embed = Embed(title="Ladders", colour=Colour.blue())
            for ladder in ladders:
                rotation: Rotation | None = (
                    session.query(Rotation)
                    .filter(Rotation.id == ladder.rotation_id)
                    .first()
                )
                rotation_name = rotation.name if rotation else "(missing)"
                lines = [
                    f"Rotation: **{rotation_name}**",
                    f"Maps per match: **{ladder.maps_per_match}**",
                    f"Max team size: **{ladder.max_team_size}**",
                    f"Challenge distance: **{ladder.max_challenge_distance}**",
                    f"Active: **{ladder.is_active}**",
                ]
                embed.add_field(name=ladder.name, value="\n".join(lines), inline=False)
            await interaction.response.send_message(embed=embed)

    @admin_group.command(name="create", description="Create a new ladder")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.describe(
        name="Unique ladder name",
        rotation="Existing rotation that supplies maps for matches",
        maps_per_match=f"Number of maps per match (1-{MAX_MAPS_PER_MATCH})",
        max_team_size="Required roster size for matches",
    )
    @app_commands.autocomplete(rotation=rotation_autocomplete)
    async def create_ladder(
        self,
        interaction: Interaction,
        name: str,
        rotation: str,
        maps_per_match: int,
        max_team_size: int,
    ):
        if maps_per_match < 1 or maps_per_match > MAX_MAPS_PER_MATCH:
            await interaction.response.send_message(
                embed=Embed(
                    description=(
                        f"`maps_per_match` must be between 1 and "
                        f"{MAX_MAPS_PER_MATCH}."
                    ),
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        if max_team_size < 1:
            await interaction.response.send_message(
                embed=Embed(
                    description="`max_team_size` must be at least 1.",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        session: SQLAlchemySession
        with Session() as session:
            rotation_row: Rotation | None = (
                session.query(Rotation).filter(Rotation.name == rotation).first()
            )
            if not rotation_row:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Rotation **{rotation}** not found.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            try:
                ladder = Ladder(
                    name=name,
                    rotation_id=rotation_row.id,
                    maps_per_match=maps_per_match,
                    max_team_size=max_team_size,
                )
                session.add(ladder)
                session.commit()
            except IntegrityError:
                session.rollback()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Ladder **{name}** already exists.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                embed=Embed(
                    description=(
                        f"Ladder **{name}** created on rotation "
                        f"**{rotation}** with {maps_per_match} maps per "
                        f"match and team size {max_team_size}."
                    ),
                    colour=Colour.green(),
                )
            )

    @admin_group.command(name="delete", description="Delete a ladder")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.describe(ladder="Ladder to delete")
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    async def delete_ladder(self, interaction: Interaction, ladder: str):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row: Ladder | None = (
                session.query(Ladder).filter(Ladder.name == ladder).first()
            )
            if not ladder_row:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Ladder **{ladder}** not found.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            session.delete(ladder_row)
            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=f"Ladder **{ladder}** deleted.",
                    colour=Colour.green(),
                )
            )

    @admin_group.command(
        name="setchannels",
        description="Set leaderboard and history channels for a ladder",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.describe(
        ladder="Ladder to configure",
        leaderboard_channel="Channel where the leaderboard message will be posted",
        history_channel="Channel where match history will be posted",
    )
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    async def set_channels(
        self,
        interaction: Interaction,
        ladder: str,
        leaderboard_channel: TextChannel,
        history_channel: TextChannel,
    ):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row: Ladder | None = (
                session.query(Ladder).filter(Ladder.name == ladder).first()
            )
            if not ladder_row:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Ladder **{ladder}** not found.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            ladder_row.leaderboard_channel_id = leaderboard_channel.id
            ladder_row.leaderboard_message_id = None
            ladder_row.history_channel_id = history_channel.id
            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=(
                        f"Ladder **{ladder}** channels set. "
                        f"Leaderboard: {leaderboard_channel.mention}, "
                        f"History: {history_channel.mention}."
                    ),
                    colour=Colour.green(),
                )
            )

    @admin_group.command(
        name="setmapspermatch",
        description="Set the number of maps per match for a ladder",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(
        ladder="Ladder to configure",
        value=f"Maps per match (1-{MAX_MAPS_PER_MATCH})",
    )
    async def set_maps_per_match(
        self, interaction: Interaction, ladder: str, value: int
    ):
        if value < 1 or value > MAX_MAPS_PER_MATCH:
            await interaction.response.send_message(
                embed=Embed(
                    description=(
                        f"`maps_per_match` must be between 1 and "
                        f"{MAX_MAPS_PER_MATCH}."
                    ),
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        await self._set_int_field(
            interaction, ladder, "maps_per_match", value, "Maps per match"
        )

    @admin_group.command(
        name="setmaxteamsize",
        description="Set the required roster size for a ladder",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder to configure", value="Roster size")
    async def set_max_team_size(
        self, interaction: Interaction, ladder: str, value: int
    ):
        if value < 1:
            await interaction.response.send_message(
                embed=Embed(
                    description="`max_team_size` must be at least 1.",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        await self._set_int_field(
            interaction, ladder, "max_team_size", value, "Max team size"
        )

    @admin_group.command(
        name="setchallengedistance",
        description="Set the maximum challenge distance for a ladder",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(
        ladder="Ladder to configure",
        value="Max positions a team may challenge above itself",
    )
    async def set_challenge_distance(
        self, interaction: Interaction, ladder: str, value: int
    ):
        if value < 1:
            await interaction.response.send_message(
                embed=Embed(
                    description="`max_challenge_distance` must be at least 1.",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        await self._set_int_field(
            interaction,
            ladder,
            "max_challenge_distance",
            value,
            "Max challenge distance",
        )

    @admin_group.command(
        name="setactive",
        description="Enable or disable writes for a ladder",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder to configure", value="True or False")
    async def set_active(self, interaction: Interaction, ladder: str, value: bool):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row: Ladder | None = (
                session.query(Ladder).filter(Ladder.name == ladder).first()
            )
            if not ladder_row:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Ladder **{ladder}** not found.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            ladder_row.is_active = value
            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=(f"Ladder **{ladder}** is_active set to **{value}**."),
                    colour=Colour.green(),
                )
            )

    async def _set_int_field(
        self,
        interaction: Interaction,
        ladder_name: str,
        column: str,
        value: int,
        display_name: str,
    ) -> None:
        session: SQLAlchemySession
        with Session() as session:
            ladder_row: Ladder | None = (
                session.query(Ladder).filter(Ladder.name == ladder_name).first()
            )
            if not ladder_row:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Ladder **{ladder_name}** not found.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            setattr(ladder_row, column, value)
            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=(
                        f"Ladder **{ladder_name}** {display_name} set to "
                        f"**{value}**."
                    ),
                    colour=Colour.green(),
                )
            )
