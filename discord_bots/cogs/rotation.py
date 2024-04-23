import discord
from discord.ext.commands import Bot, Context, check, command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound

from discord_bots.checks import is_admin
from discord_bots.cogs.base import BaseCog
from discord_bots.models import Map, Queue, Rotation, RotationMap
from discord_bots.utils import update_next_map_to_map_after_next


class RotationCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    @command()
    @check(is_admin)
    async def addrotation(self, ctx: Context, rotation_name: str):
        """
        Add a rotation to the rotation pool
        """
        session = ctx.session

        try:
            session.add(Rotation(name=rotation_name))
            session.commit()
        except IntegrityError:
            session.rollback()
            await self.send_error_message(
                f"Error adding rotation {rotation_name}). Does it already exist?"
            )
        else:
            await self.send_success_message(f"Rotation **{rotation_name}** added")

    @command()
    @check(is_admin)
    async def addrotationmap(
        self, ctx: Context, rotation_name: str, map_short_name: str, ordinal: int
    ):
        """
        Add a map to a rotation at a specific ordinal (position)
        """
        session = ctx.session

        if ordinal < 1:
            await self.send_error_message("Ordinal must be a positive number")
            return

        try:
            rotation = (
                session.query(Rotation).filter(Rotation.name.ilike(rotation_name)).one()
            )
        except NoResultFound:
            await self.send_error_message(
                f"Could not find rotation **{rotation_name}**"
            )
            return

        try:
            map = session.query(Map).filter(Map.short_name.ilike(map_short_name)).one()
        except NoResultFound:
            await self.send_error_message(f"Could not find map **{map_short_name}**")
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

        is_next = True if not rotation_maps else False

        try:
            session.add(
                RotationMap(
                    rotation_id=rotation.id,
                    map_id=map.id,
                    ordinal=ordinal,
                    is_next=is_next,
                )
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            await self.send_error_message(
                f"Error adding {map_short_name} to {rotation_name} at ordinal {ordinal}"
            )
        else:
            await self.send_success_message(
                f"{map.short_name} added to {rotation.name} at ordinal {ordinal}"
            )

    @command()
    async def listrotations(self, ctx: Context):
        """
        List all rotations in the rotation pool
        """
        session = ctx.session

        rotations: list[Rotation] | None = (
            session.query(Rotation).order_by(Rotation.created_at.asc()).all()
        )
        if not rotations:
            await self.send_info_message("_-- No Rotations-- _")
            return

        output = ""

        for rotation in rotations:
            output += f"### {rotation.name}\n"

            map_names = [
                x[0]
                for x in (
                    session.query(Map.short_name)
                    .join(RotationMap, RotationMap.map_id == Map.id)
                    .filter(RotationMap.rotation_id == rotation.id)
                    .order_by(RotationMap.ordinal.asc())
                    .all()
                )
            ]
            if not map_names:
                output += f" - Maps:  None\n"
            else:
                output += f" - Maps:  {', '.join(map_names)}\n"

            queue_names = [
                x[0]
                for x in (
                    session.query(Queue.name)
                    .filter(Queue.rotation_id == rotation.id)
                    .order_by(Queue.ordinal.asc())
                    .all()
                )
            ]
            if not queue_names:
                output += f" - Queues:  None\n"
            else:
                output += f" - Queues:  {', '.join(queue_names)}\n"

        await self.send_info_message(output)

    @command()
    @check(is_admin)
    async def removerotation(self, ctx: Context, rotation_name: str):
        """
        Remove a rotation from the rotation pool
        TODO: Add confirmation for deleting rotation still associated with a queue
        """
        session = ctx.session

        try:
            rotation = (
                session.query(Rotation).filter(Rotation.name.ilike(rotation_name)).one()
            )
        except NoResultFound:
            await self.send_error_message(
                f"Could not find rotation **{rotation_name}**"
            )
            return

        session.delete(rotation)
        session.commit()
        await self.send_success_message(f"Rotation **{rotation.name}** removed")

    @command()
    @check(is_admin)
    async def removerotationmap(
        self, ctx: Context, rotation_name: str, map_short_name: str
    ):
        """
        Remove a map from a rotation
        """
        session = ctx.session

        try:
            rotation = (
                session.query(Rotation).filter(Rotation.name.ilike(rotation_name)).one()
            )
        except NoResultFound:
            await self.send_error_message(
                f"Could not find rotation **{rotation_name}**"
            )
            return

        try:
            map = session.query(Map).filter(Map.short_name.ilike(map_short_name)).one()
        except NoResultFound:
            await self.send_error_message(f"Could not find map **{map_short_name}**")
            return

        rotation_map = (
            session.query(RotationMap)
            .filter(
                rotation.id == RotationMap.rotation_id, map.id == RotationMap.map_id
            )
            .first()
        )
        if not rotation_map:
            await self.send_error_message(
                f"Could not find map **{map.short_name}** in rotation **{rotation.name}**"
            )
            return

        if rotation_map.is_next:
            await update_next_map_to_map_after_next(rotation.id, True)

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
        await self.send_success_message(
            f"**{map.short_name}** removed from rotation **{rotation.name}**"
        )

    @command()
    @check(is_admin)
    async def setrotationmapordinal(
        self, ctx: Context, rotation_name: str, map_short_name: str, new_ordinal: int
    ):
        """
        Set the ordinal (position) for a map in a rotation
        """
        session = ctx.session

        if new_ordinal < 1:
            await self.send_error_message("Ordinal must be a positive number")
            return

        try:
            rotation = (
                session.query(Rotation).filter(Rotation.name.ilike(rotation_name)).one()
            )
        except NoResultFound:
            await self.send_error_message(
                f"Could not find rotation **{rotation_name}**"
            )
            return

        try:
            map = session.query(Map).filter(Map.short_name.ilike(map_short_name)).one()
        except NoResultFound:
            await self.send_error_message(f"Could not find map **{map_short_name}**")
            return

        try:
            rotation_map_to_set = (
                session.query(RotationMap)
                .filter(RotationMap.rotation_id == rotation.id)
                .filter(RotationMap.map_id == map.id)
                .one()
            )
        except NoResultFound:
            await self.send_error_message(
                f"Map **{map.short_name}** is not in rotation **{rotation.name}**\nPlease add it with `!addrotationmap`."
            )
            return

        # logic for organizing ordinals.  ordinals are kept unique and consecutive.
        current_ordinal = rotation_map_to_set.ordinal

        if new_ordinal == current_ordinal:
            await self.send_info_message(
                f"Map **{map.short_name}** in rotation **{rotation.name}** is already set to ordinal **{new_ordinal}**."
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
            for rotation_map in rotation_maps[new_ordinal - 1 : current_ordinal - 1]:
                rotation_map.ordinal += 1

        rotation_map_to_set.ordinal = new_ordinal

        session.commit()

        await self.send_success_message(
            f"Map **{map.short_name}** in rotation **{rotation.name}** set to ordinal **{rotation_map_to_set.ordinal}**."
        )

    @command()
    @check(is_admin)
    async def setrotationname(
        self, ctx: Context, old_rotation_name: str, new_rotation_name: str
    ):
        """
        Set rotation name
        """
        await self.setname(ctx, Rotation, old_rotation_name, new_rotation_name)

    @command()
    @check(is_admin)
    async def setrotationrandom(self, ctx: Context, rotation_name: str):
        """
        Chooses rotation's maps at random
        """
        session = ctx.session

        rotation: Rotation | None = (
            session.query(Rotation).filter(Rotation.name.ilike(rotation_name)).first()
        )
        if not rotation:
            await self.send_error_message(
                f"Could not find rotation **{rotation_name}**"
            )
            return

        rotation.is_random = True
        session.commit()
        await self.send_success_message(f"**{rotation.name}** rotation set to random")

    @command()
    @check(is_admin)
    async def unsetrotationrandom(self, ctx: Context, rotation_name: str):
        """
        Chooses rotation's maps in order
        """
        session = ctx.session

        rotation: Rotation | None = (
            session.query(Rotation).filter(Rotation.name.ilike(rotation_name)).first()
        )
        if not rotation:
            await self.send_error_message(
                f"Could not find rotation **{rotation_name}**"
            )
            return

        rotation.is_random = False
        session.commit()
        await self.send_success_message(
            f"**{rotation.name}** rotation unset from random"
        )
