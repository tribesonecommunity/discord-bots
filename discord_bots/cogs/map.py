from discord import Colour
from discord.ext.commands import Bot, Context, check, command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound

from discord_bots.checks import is_admin
from discord_bots.cogs.base import BaseCog
from discord_bots.models import Map, MapVote
from discord_bots.utils import send_message


class MapCog(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    @command()
    @check(is_admin)
    async def addmap(self, ctx: Context, map_full_name: str, map_short_name: str):
        session = ctx.session
        map_short_name = map_short_name.upper()

        try:
            session.add(Map(map_full_name, map_short_name))
            session.commit()
        except IntegrityError:
            session.rollback()
            await self.send_error_message(
                f"Error adding map {map_full_name} ({map_short_name}). Does it already exist?"
            )
        else:
            await self.send_success_message(
                f"**{map_full_name} ({map_short_name})** added to maps"
            )

    @command()
    async def listmaps(self, ctx: Context):
        session = ctx.session
        maps = session.query(Map).order_by(Map.created_at.asc()).all()

        if not maps:
            output = "_-- No Maps --_"
        else:
            output = ""
            for map in maps:
                output += f"- {map.full_name} ({map.short_name})\n"

        await self.send_info_message(output)

    @command()
    @check(is_admin)
    async def removemap(self, ctx: Context, map_short_name: str):
        session = ctx.session

        try:
            map = session.query(Map).filter(Map.short_name.ilike(map_short_name)).one()
        except NoResultFound:
            await self.send_error_message(f"Could not find map **{map_short_name}**")
            return

        session.delete(map)
        session.commit()
        await self.send_success_message(
            f"**{map.full_name} ({map.short_name})** removed from maps"
        )
