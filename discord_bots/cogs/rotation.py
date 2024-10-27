import logging
from datetime import datetime, timezone

import discord
from discord import Colour, Embed, Interaction, app_commands
from discord.ext.commands import Bot
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.checks import is_admin_app_command, is_command_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.models import Map, Rotation, RotationMap, Session, RotationMapHistory
from discord_bots.utils import (
    execute_map_rotation,
    map_short_name_autocomplete,
    rotation_autocomplete,
)

_log = logging.getLogger(__name__)


class RotationCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    _show_rotation_limit = 20

    group = app_commands.Group(name="rotation", description="Rotation commands")

    @group.command(name="add", description="Add a rotation to the rotation pool")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(rotation_name="Existing rotation")
    @app_commands.autocomplete(rotation_name=rotation_autocomplete)
    @app_commands.rename(rotation_name="rotation")
    async def addrotation(self, interaction: Interaction, rotation_name: str):
        """
        Add a rotation to the rotation pool
        """
        session: SQLAlchemySession
        with Session() as session:
            try:
                session.add(Rotation(name=rotation_name))
                session.commit()
            except IntegrityError:
                session.rollback()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Error adding rotation {rotation_name}). Does it already exist?",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Rotation **{rotation_name}** added",
                        colour=Colour.green(),
                    )
                )

    @group.command(
        name="addmap",
        description="Add a map to a rotation at a specific ordinal (position)",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        rotation_name="Existing rotation",
        map_short_name="Existing map",
        ordinal="Map ordinal",
        random_weight="Random weight (default: 1)"
    )
    @app_commands.autocomplete(
        rotation_name=rotation_autocomplete, map_short_name=map_short_name_autocomplete
    )
    @app_commands.rename(rotation_name="rotation", map_short_name="map")
    async def addrotationmap(
        self,
        interaction: Interaction,
        rotation_name: str,
            map_short_name: str,
            ordinal: int,
            random_weight: int | None,
    ):
        """
        Add a map to a rotation at a specific ordinal (position)
        """
        if ordinal < 1:
            await interaction.response.send_message(
                embed=Embed(
                    description="Ordinal must be a positive number",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        if random_weight is None:
            random_weight = 1
        if random_weight < 1:
            await interaction.response.send_message(
                embed=Embed(
                    description="Random Weight must be a positive number",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        session: SQLAlchemySession
        with Session() as session:
            try:
                rotation = (
                    session.query(Rotation)
                    .filter(Rotation.name.ilike(rotation_name))
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find rotation **{rotation_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            try:
                map = (
                    session.query(Map)
                    .filter(Map.short_name.ilike(map_short_name))
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find map **{map_short_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            # logic for organizing ordinals.  ordinals are kept unique and consecutive.
            # we insert a map directly at an ordinal and increment every one after that.
            rotation_maps = (
                session.query(RotationMap)
                .join(Rotation, RotationMap.rotation_id == rotation.id)
                .order_by(RotationMap.ordinal.asc())
                .all()
            )

            if ordinal > len(rotation_maps):
                ordinal = len(rotation_maps) + 1
            else:
                for rotation_map in rotation_maps[ordinal - 1 :]:
                    rotation_map.ordinal += 1

            try:
                session.add(
                    RotationMap(
                        rotation_id=rotation.id,
                        map_id=map.id,
                        ordinal=ordinal,
                        random_weight=random_weight,
                    )
                )
                session.commit()

                if not rotation_maps:
                    # ensure there is a "next map" to start rotating
                    await execute_map_rotation(rotation.id, False)
            except IntegrityError:
                session.rollback()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Error adding {map_short_name} to {rotation_name} at ordinal {ordinal}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"{map.short_name} added to {rotation.name} at ordinal {ordinal}",
                        colour=Colour.green(),
                    )
                )

    @group.command(
        name="remove", description="Remove a rotation from the rotation pool"
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(rotation_name="Existing rotation")
    @app_commands.autocomplete(rotation_name=rotation_autocomplete)
    @app_commands.rename(rotation_name="rotation")
    async def removerotation(self, interaction: Interaction, rotation_name: str):
        """
        Remove a rotation from the rotation pool
        TODO: Add confirmation for deleting rotation still associated with a queue
        """
        session: SQLAlchemySession
        with Session() as session:
            try:
                rotation = (
                    session.query(Rotation)
                    .filter(Rotation.name.ilike(rotation_name))
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find rotation **{rotation_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            session.delete(rotation)
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Rotation **{rotation.name}** removed",
                    colour=Colour.green(),
                )
            )

    @group.command(name="removemap", description="Remove a map from a rotation")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.autocomplete(
        rotation_name=rotation_autocomplete, map_short_name=map_short_name_autocomplete
    )
    @app_commands.rename(rotation_name="rotation", map_short_name="map")
    @app_commands.describe(
        rotation_name="Existing rotation", map_short_name="Existing map"
    )
    async def removerotationmap(
        self, interaction: Interaction, rotation_name: str, map_short_name: str
    ):
        """
        Remove a map from a rotation
        """
        session: SQLAlchemySession
        with Session() as session:
            try:
                rotation = (
                    session.query(Rotation)
                    .filter(Rotation.name.ilike(rotation_name))
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find rotation **{rotation_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            try:
                map = (
                    session.query(Map)
                    .filter(Map.short_name.ilike(map_short_name))
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find map **{map_short_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            rotation_map = (
                session.query(RotationMap)
                .filter(
                    rotation.id == RotationMap.rotation_id, map.id == RotationMap.map_id
                )
                .first()
            )
            if not rotation_map:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find map **{map.short_name}** in rotation **{rotation.name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            if rotation_map.is_next:
                await execute_map_rotation(rotation.id, True)

            # adjust the rest of the ordinals in the rotation
            rotation_maps = (
                session.query(RotationMap)
                .filter(RotationMap.rotation_id == rotation.id)
                .order_by(RotationMap.ordinal.asc())
                .all()
            )
            for entry in rotation_maps[rotation_map.ordinal :]:
                entry.ordinal -= 1

            session.delete(rotation_map)
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"**{map.short_name}** removed from rotation **{rotation.name}**",
                    colour=Colour.green(),
                )
            )

    @group.command(
        name="setmapordinal",
        description="Set the ordinal (position) for a map in a rotation",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        rotation_name="Existing rotation",
        map_short_name="Existing map",
        new_ordinal="New map ordinal",
    )
    @app_commands.autocomplete(
        rotation_name=rotation_autocomplete, map_short_name=map_short_name_autocomplete
    )
    @app_commands.rename(
        rotation_name="rotation", map_short_name="map", new_ordinal="ordinal"
    )
    async def setrotationmapordinal(
        self,
        interaction: Interaction,
        rotation_name: str,
        map_short_name: str,
        new_ordinal: int,
    ):
        """
        Set the ordinal (position) for a map in a rotation
        """
        session: SQLAlchemySession
        with Session() as session:
            if new_ordinal < 1:
                await interaction.response.send_message(
                    embed=Embed(
                        description="Ordinal must be a positive number",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            try:
                rotation = (
                    session.query(Rotation)
                    .filter(Rotation.name.ilike(rotation_name))
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find rotation **{rotation_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            try:
                map = (
                    session.query(Map)
                    .filter(Map.short_name.ilike(map_short_name))
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find map **{map_short_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            try:
                rotation_map_to_set = (
                    session.query(RotationMap)
                    .filter(RotationMap.rotation_id == rotation.id)
                    .filter(RotationMap.map_id == map.id)
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Map **{map.short_name}** is not in rotation **{rotation.name}**\nPlease add it with `!addrotationmap`.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            # logic for organizing ordinals.  ordinals are kept unique and consecutive.
            current_ordinal = rotation_map_to_set.ordinal

            if new_ordinal == current_ordinal:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Map **{map.short_name}** in rotation **{rotation.name}** is already set to ordinal **{new_ordinal}**.",
                        colour=Colour.blue(),
                    )
                )
                return

            rotation_maps = (
                session.query(RotationMap)
                .join(Rotation, RotationMap.rotation_id == rotation.id)
                .order_by(RotationMap.ordinal.asc())
                .all()
            )

            if new_ordinal > len(rotation_maps):
                new_ordinal = len(rotation_maps)

            if new_ordinal > current_ordinal:
                for rotation_map in rotation_maps[current_ordinal:new_ordinal]:
                    rotation_map.ordinal -= 1
            else:
                for rotation_map in rotation_maps[
                    new_ordinal - 1 : current_ordinal - 1
                ]:
                    rotation_map.ordinal += 1

            rotation_map_to_set.ordinal = new_ordinal

            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=f"Map **{map.short_name}** in rotation **{rotation.name}** set to ordinal **{rotation_map_to_set.ordinal}**.",
                    colour=Colour.green(),
                )
            )

    @group.command(
        name="setrotationmaprandomweight",
        description="Set the random weight for a map in a rotation",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        rotation_name="Existing rotation",
        map_short_name="Existing map",
        new_random_weight="New random weight",
    )
    @app_commands.autocomplete(
        rotation_name=rotation_autocomplete, map_short_name=map_short_name_autocomplete
    )
    @app_commands.rename(
        rotation_name="rotation", map_short_name="map", new_random_weight="random_weight"
    )
    async def setrotationmaprandomweight(
            self,
            interaction: Interaction,
            rotation_name: str,
            map_short_name: str,
            new_random_weight: int,
    ):
        """
        Set the random weight for a map in a rotation
        """
        session: SQLAlchemySession
        with Session() as session:
            if new_random_weight < 1:
                await interaction.response.send_message(
                    embed=Embed(
                        description="Random weight must be a positive number",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            try:
                rotation = (
                    session.query(Rotation)
                    .filter(Rotation.name.ilike(rotation_name))
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find rotation **{rotation_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            try:
                map = (
                    session.query(Map)
                    .filter(Map.short_name.ilike(map_short_name))
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find map **{map_short_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            try:
                rotation_map_to_set = (
                    session.query(RotationMap)
                    .filter(RotationMap.rotation_id == rotation.id)
                    .filter(RotationMap.map_id == map.id)
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Map **{map.short_name}** is not in rotation **{rotation.name}**\nPlease add it with `!addrotationmap`.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            rotation_map_to_set.random_weight = new_random_weight

            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=f"Map **{map.short_name}** in rotation **{rotation.name}** set to random weight **{rotation_map_to_set.random_weight}**.",
                    colour=Colour.green(),
                )
            )

    @group.command(
        name="set-stop",
        description="Whether to stop auto-rotation when reaching this map",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        rotation_name="Existing rotation",
        map_short_name="Existing map",
        value="Whether to stop auto-rotation",
    )
    @app_commands.autocomplete(
        rotation_name=rotation_autocomplete, map_short_name=map_short_name_autocomplete
    )
    @app_commands.rename(
        rotation_name="rotation", map_short_name="map", value="value"
    )
    async def set_stop(
            self,
            interaction: Interaction,
            rotation_name: str,
            map_short_name: str,
            value: bool,
    ):
        session: SQLAlchemySession
        with Session() as session:
            try:
                rotation = (
                    session.query(Rotation)
                    .filter(Rotation.name.ilike(rotation_name))
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find rotation **{rotation_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            try:
                map = (
                    session.query(Map)
                    .filter(Map.short_name.ilike(map_short_name))
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find map **{map_short_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            try:
                rotation_map_to_set: RotationMap = (
                    session.query(RotationMap)
                    .filter(RotationMap.rotation_id == rotation.id)
                    .filter(RotationMap.map_id == map.id)
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Map **{map.short_name}** is not in rotation **{rotation.name}**\nPlease add it with `!addrotationmap`.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            rotation_map_to_set.stop_rotation = value
            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=f"Map **{map.short_name}** in rotation **{rotation.name}** set to ordinal stop rotation: **{value}**.",
                    colour=Colour.green(),
                )
            )

    @group.command(name="setname", description="Set rotation name")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.autocomplete(old_rotation_name=rotation_autocomplete)
    @app_commands.rename(old_rotation_name="old_name", new_rotation_name="new_name")
    @app_commands.describe(
        old_rotation_name="Existing rotation", new_rotation_name="New rotation name"
    )
    async def setrotationname(
        self, interaction: Interaction, old_rotation_name: str, new_rotation_name: str
    ):
        """
        Set rotation name
        """
        await self.setname(interaction, Rotation, old_rotation_name, new_rotation_name)

    @group.command(name="configure-random", description="Configures the rotations random map properties")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        rotation_name="Existing rotation",
        is_random="Whether to chooses rotation's maps at random",
        min_maps_before_requeue="Minimum number of maps before requeuing is allowed",
        weight_increase="Linear weight increase every time the map isn't picked"
    )
    @app_commands.autocomplete(rotation_name=rotation_autocomplete)
    @app_commands.rename(
        rotation_name="rotation",
        is_random="random",
        min_maps_before_requeue="min_maps_before_requeue",
        weight_increase="weight_increase"
    )
    async def configure_random(
            self,
            interaction: Interaction,
            rotation_name: str,
            is_random: bool | None,
            min_maps_before_requeue: int | None,
            weight_increase: float | None
    ):
        """
        Configures the rotations random map properties
        """
        session: SQLAlchemySession
        with Session() as session:
            rotation: Rotation | None = (
                session.query(Rotation)
                .filter(Rotation.name.ilike(rotation_name))
                .first()
            )
            if not rotation:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find rotation **{rotation_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            if is_random is not None:
                rotation.is_random = is_random
            if min_maps_before_requeue is not None:
                rotation.min_maps_before_requeue = min_maps_before_requeue
            if weight_increase is not None:
                rotation.weight_increase = weight_increase

            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"**{rotation.name}** rotation set to random {rotation.is_random}, min maps before requeue {rotation.min_maps_before_requeue}, weight increase {rotation.weight_increase}",
                    colour=Colour.green(),
                )
            )

    @group.command(name="show-history", description="Shows the history of selected maps")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        rotation_name="Existing rotation",
        limit=f"How many entries to show. Max: {_show_rotation_limit}",
    )
    @app_commands.autocomplete(rotation_name=rotation_autocomplete)
    @app_commands.rename(
        rotation_name="rotation",
        limit="limit",
    )
    async def show_history(
            self,
            interaction: Interaction,
            rotation_name: str,
            limit: int | None,
    ):
        """
        Shows the last selected maps for a rotation
        """
        session: SQLAlchemySession
        with Session() as session:
            rotation: Rotation | None = (
                session.query(Rotation)
                .filter(Rotation.name.ilike(rotation_name))
                .first()
            )
            if not rotation:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find rotation **{rotation_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            if limit is None or limit <= 0 or limit > self._show_rotation_limit:
                limit = self._show_rotation_limit

            map_history: list[tuple[Map, datetime]] = (
                session.query(Map, RotationMapHistory.selected_at)
                .join(RotationMap, RotationMap.map_id == Map.id)
                .join(RotationMapHistory, RotationMapHistory.rotation_map_id == RotationMap.id)
                .filter(RotationMapHistory.rotation_id == rotation.id)
                .order_by(RotationMapHistory.selected_at.desc())
                .limit(limit)
                .all()
            )

            history = []
            for m in map_history:
                # timezones aren't stored in the DB, so add it ourselves
                aware_db_datetime: datetime = m[1].replace(tzinfo=timezone.utc)
                timestamp = discord.utils.format_dt(aware_db_datetime, style="R")
                history.append(f"* {m[0].full_name} ({m[0].short_name}) {timestamp}")

            newline = "\n"
            embed = Embed(
                title=f"History of {rotation.name}",
                description=f"{newline.join(history)}" if history else "*None*",
                colour=Colour.blue(),
            )
            embed.set_footer(text=f"Limited to last {self._show_rotation_limit} maps")
            await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
            )
