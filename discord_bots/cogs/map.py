from discord import Colour
from discord.ext.commands import Bot, Context, check, command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound

from discord_bots.checks import is_admin
from discord_bots.cogs.base import BaseCog
from discord_bots.models import FinishedGame, InProgressGame, Map, MapVote, RotationMap
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
    async def changegamemap(self, ctx: Context, game_id: str, map_short_name: str):
        """
        TODO: tests
        """
        session = ctx.session

        ipg = (
            session.query(InProgressGame)
            .filter(InProgressGame.id.startswith(game_id))
            .first()
        )
        finished_game = (
            session.query(FinishedGame)
            .filter(FinishedGame.game_id.startswith(game_id))
            .first()
        )
        if ipg:
            game = ipg
        elif finished_game:
            game = finished_game
        else:
            await self.send_error_message(f"Could not find game: **{game_id}**")
            return

        map: Map | None = (
            session.query(Map).filter(Map.short_name.ilike(map_short_name)).first()
        )
        if not map:
            await self.send_error_message(
                f"Could not find map: **{map_short_name}**. Add to map pool first."
            )
            return

        game.map_full_name = map.full_name
        game.map_short_name = map.short_name
        session.commit()
        await self.send_success_message(
            f"Map for game **{game_id}** changed to **{map.short_name}**"
        )

    @command()
    @check(is_admin)
    async def changenextmap(
        self, ctx: Context, rotation_name: str, map_short_name: str
    ):
        """
        TODO: tests
        """

        session = ctx.session
        current_map: CurrentMap = session.query(CurrentMap).first()
        rotation_map: RotationMap | None = (
            session.query(RotationMap).filter(RotationMap.short_name.ilike(map_short_name)).first()  # type: ignore
        )
        if rotation_map:
            rotation_maps: list[RotationMap] = (
                session.query(RotationMap).order_by(RotationMap.created_at.asc()).all()  # type: ignore
            )
            rotation_map_index = rotation_maps.index(rotation_map)
            if current_map:
                current_map.full_name = rotation_map.full_name
                current_map.short_name = rotation_map.short_name
                current_map.map_rotation_index = rotation_map_index
                current_map.updated_at = datetime.now(timezone.utc)
                session.commit()
            else:
                session.add(
                    CurrentMap(
                        map_rotation_index=0,
                        full_name=rotation_map.full_name,
                        short_name=rotation_map.short_name,
                    )
                )
                session.commit()
        else:
            map: Map | None = (
                session.query(Map)
                .filter(Map.short_name.ilike(map_short_name))  # type: ignore
                .first()
            )
            if map:
                if current_map:
                    current_map.full_name = map.full_name
                    current_map.short_name = map.short_name
                    current_map.updated_at = datetime.now(timezone.utc)
                    session.commit()
                else:
                    session.add(
                        CurrentMap(
                            map_rotation_index=0,
                            full_name=rotation_map.full_name,
                            short_name=rotation_map.short_name,
                        )
                    )
                    session.commit()
            else:
                await send_message(
                    message.channel,
                    embed_description=f"Could not find map: {map_short_name}. Add to rotation or map pool first.",
                    colour=Colour.red(),
                )
                return
        session.commit()
        await send_message(
            message.channel,
            embed_description=f"Queue map changed to {map_short_name}",
            colour=Colour.green(),
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
