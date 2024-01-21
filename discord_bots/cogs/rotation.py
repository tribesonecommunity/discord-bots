from discord import Colour
from discord.ext.commands import Bot, Cog, Context, check, command
from sqlalchemy.exc import IntegrityError

from discord_bots.checks import is_admin
from discord_bots.models import Map, Rotation, RotationMap
from discord_bots.utils import send_message


class RotationCog(Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    @command()
    @check(is_admin)
    async def addrotation(self, ctx: Context, rotation_name: str):
        message = ctx.message
        session = ctx.session
        session.add(Rotation(name=rotation_name))

        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            await send_message(
                message.channel,
                embed_description=f"Error adding rotation {rotation_name}). Does it already exist?",
                colour=Colour.red(),
            )
        else:
            await send_message(
                message.channel,
                embed_description=f"{rotation_name} added to rotation pool",
                colour=Colour.green(),
            )

    @command(usage="<rotation_name> <map_short_name> <position>")
    @check(is_admin)
    async def addrotationmap(
        self, ctx: Context, rotation_name: str, map_short_name: str, ordinal: int
    ):
        message = ctx.message
        session = ctx.session
        rotation: Rotation | None = (
            session.query(Rotation).filter(Rotation.name.ilike(rotation_name)).first()
        )
        map: Map | None = (
            session.query(Map).filter(Map.short_name.ilike(map_short_name)).first()
        )

        if ordinal < 1:
            await send_message(
                message.channel,
                embed_description="Position must be a positive number",
                colour=Colour.red(),
            )
            return
        if not rotation:
            await send_message(
                message.channel,
                embed_description=f"Could not find rotation: {rotation_name}",
                colour=Colour.red(),
            )
            return
        if not map:
            await send_message(
                message.channel,
                embed_description=f"Could not find map: {map_short_name}",
                colour=Colour.red(),
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

        session.add(
            RotationMap(rotation_id=rotation.id, map_id=map.id, ordinal=ordinal)
        )

        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            await send_message(
                message.channel,
                embed_description=f"Error adding {map_short_name} to {rotation_name} at position {ordinal}",
                colour=Colour.red(),
            )
        else:
            await send_message(
                message.channel,
                embed_description=f"{map.short_name} added to {rotation.name} at position {ordinal}",
                colour=Colour.green(),
            )

    @command()
    async def listrotations(self, ctx: Context):
        message = ctx.message
        session = ctx.session
        data = (
            session.query(Rotation.name, RotationMap.ordinal, Map.short_name)
            .join(RotationMap, Rotation.id == RotationMap.rotation_id)
            .join(Map, Map.id == RotationMap.map_id)
            .order_by(RotationMap.ordinal.asc())
            .all()
        )
        grouped_data = {}

        for row in data:
            if row[0] in grouped_data:
                grouped_data[row[0]].append(row[2])
            else:
                grouped_data[row[0]] = [row[2]]

        output = ""

        for key, value in grouped_data.items():
            output += f"**- {key}**"
            output += f"_{', '.join(value)}_\n\n"

        await send_message(
            message.channel, embed_description=output, colour=Colour.blue()
        )
