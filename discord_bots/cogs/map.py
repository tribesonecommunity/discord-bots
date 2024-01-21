from discord import Colour
from discord.ext.commands import Bot, Cog, Context, check, command
from sqlalchemy.exc import IntegrityError

from discord_bots.checks import is_admin
from discord_bots.models import Map
from discord_bots.utils import send_message


class MapCog(Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    @command()
    @check(is_admin)
    async def addmap(self, ctx: Context, map_full_name: str, map_short_name: str):
        message = ctx.message
        session = ctx.session
        map_short_name = map_short_name.upper()
        session.add(Map(map_full_name, map_short_name))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            await send_message(
                message.channel,
                embed_description=f"Error adding map {map_full_name} ({map_short_name}). Does it already exist?",
                colour=Colour.red(),
            )
        else:
            await send_message(
                message.channel,
                embed_description=f"{map_full_name} ({map_short_name}) added to map pool",
                colour=Colour.green(),
            )

    @command()
    async def listmaps(self, ctx: Context):
        message = ctx.message
        session = ctx.session
        maps = session.query(Map).all()

        output = ""
        for map in maps:
            output += f"- {map.full_name} ({map.short_name})"

        await send_message(
            message.channel,
            embed_description=output,
            colour=Colour.blue(),
        )
