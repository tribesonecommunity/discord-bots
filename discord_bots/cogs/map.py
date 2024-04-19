import logging
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord import (
    app_commands,
    Colour,
    Embed,
    Interaction,
)

from discord.ext.commands import Bot
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound

from discord_bots.checks import is_admin_app_command
from discord_bots.cogs.base import BaseCog
from discord_bots.models import (
    FinishedGame,
    InProgressGame,
    Map,
    MapVote,
    Queue,
    Rotation,
    RotationMap,
    Session,
)
from discord_bots.utils import short_uuid

_log = logging.getLogger(__name__)


class MapCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(name="map", description="Map commands")

    @group.command(name="add", description="Add a map to the map pool")
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(full_name="Long name of map", short_name="Short name of map")
    async def addmap(self, interaction: Interaction, full_name: str, short_name: str):
        """
        Add a map to the map pool
        """
        session: SQLAlchemySession
        short_name = short_name.upper()

        with Session() as session:
            try:
                session.add(Map(full_name, short_name))
                session.commit()
            except IntegrityError:
                session.rollback()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Error adding map {full_name} ({short_name}). Does it already exist?",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"**{full_name} ({short_name})** added to maps",
                        colour=Colour.green(),
                    )
                )

    @group.command(name="changegame", description="Change the map for a game")
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(
        game_id="In progress game id", short_name="Short name of map"
    )
    async def changegamemap(
        self, interaction: Interaction, game_id: str, short_name: str
    ):
        """
        Change the map for a game
        TODO: tests
        """
        session: SQLAlchemySession
        with Session() as session:
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
            game: InProgressGame | FinishedGame
            if ipg:
                game = ipg
            elif finished_game:
                game = finished_game
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find game: **{game_id}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            map: Map | None = (
                session.query(Map).filter(Map.short_name.ilike(short_name)).first()
            )
            if not map:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find map: **{short_name}**. Add to map pool first.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            game.map_full_name = map.full_name
            game.map_short_name = map.short_name
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Map for game **{game_id}** changed to **{map.short_name}**",
                    colour=Colour.green(),
                )
            )

    @group.command(
        name="changequeue",
        description="Change the next map for a queue (note: affects all queues sharing that rotation)",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(queue_name="Name of queue", short_name="Short name of map")
    async def changequeuemap(
        self, interaction: Interaction, queue_name: str, short_name: str
    ):
        """
        Change the next map for a queue (note: affects all queues sharing that rotation)
        TODO: tests
        """

        session: SQLAlchemySession
        with Session() as session:
            queue: Queue | None = (
                session.query(Queue).filter(Queue.name.ilike(queue_name)).first()
            )
            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue: **{queue_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            map: Map | None = (
                session.query(Map).filter(Map.short_name.ilike(short_name)).first()
            )
            if not map:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find map: **{short_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            rotation: Rotation | None = (
                session.query(Rotation).filter(queue.rotation_id == Rotation.id).first()
            )
            if not rotation:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"**{queue.name}** has not been assigned a rotation.\nPlease assign one with `/setqueuerotation`.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            next_rotation_map: RotationMap | None = (
                session.query(RotationMap)
                .filter(
                    RotationMap.rotation_id == rotation.id, RotationMap.map_id == map.id
                )
                .first()
            )
            if not next_rotation_map:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"The rotation for **{queue.name}** doesn't have that map.\nPlease add it to the **{rotation.name}** rotation with `/addrotationmap`.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            session.query(RotationMap).filter(
                RotationMap.rotation_id == rotation.id
            ).filter(RotationMap.is_next == True).update({"is_next": False})
            next_rotation_map.is_next = True
            session.commit()

            output = f"**{queue.name}** next map changed to **{map.short_name}**"
            affected_queues = (
                session.query(Queue.name)
                .filter(Queue.rotation_id == rotation.id)
                .filter(Queue.name != queue.name)
                .all()
            )
            if affected_queues:
                output += "\n\nQueues also affected:"
                for name_tuple in affected_queues:
                    output += f"\n- {name_tuple[0]}"

            await interaction.response.send_message(
                embed=Embed(
                    description=output,
                    colour=Colour.green(),
                )
            )

    @group.command(name="list", description="List all maps in the map pool")
    async def listmaps(self, interaction: Interaction):
        """
        List all maps in the map pool
        """
        session: SQLAlchemySession
        with Session() as session:
            maps = session.query(Map).order_by(Map.created_at.asc()).all()

            if not maps:
                output = "_-- No Maps --_"
            else:
                output = ""
                for map in maps:
                    output += f"- {map.full_name} ({map.short_name})\n"

            await interaction.response.send_message(
                embed=Embed(
                    description=output,
                    colour=Colour.blue(),
                )
            )

    # broken commands

    # TODO: update to !map <queue_name>

    # @command(name="map")
    # async def map_(ctx: Context):
    #     # TODO: This is duplicated
    #     session = ctx.session
    #     output = ""
    #     current_map: CurrentMap | None = session.query(CurrentMap).first()
    #     if current_map:
    #         rotation_maps: list[RotationMap] = session.query(RotationMap).order_by(RotationMap.created_at.asc()).all()  # type: ignore
    #         next_rotation_map_index = (current_map.map_rotation_index + 1) % len(
    #             rotation_maps
    #         )
    #         next_map = rotation_maps[next_rotation_map_index]

    #         time_since_update: timedelta = datetime.now(
    #             timezone.utc
    #         ) - current_map.updated_at.replace(tzinfo=timezone.utc)
    #         time_until_rotation = MAP_ROTATION_MINUTES - (time_since_update.seconds // 60)
    #         if current_map.map_rotation_index == 0:
    #             output += f"**Next map: {current_map.full_name} ({current_map.short_name})**\n_Map after next: {next_map.full_name} ({next_map.short_name})_\n"
    #         else:
    #             output += f"**Next map: {current_map.full_name} ({current_map.short_name})**\n_Map after next (auto-rotates in {time_until_rotation} minutes): {next_map.full_name} ({next_map.short_name})_\n"
    #     skip_map_votes: list[SkipMapVote] = session.query(SkipMapVote).all()
    #     output += (
    #         f"_Votes to skip (voteskip): [{len(skip_map_votes)}/{MAP_VOTE_THRESHOLD}]_\n"
    #     )

    #     # TODO: This is duplicated
    #     map_votes: list[MapVote] = session.query(MapVote).all()
    #     voted_map_ids: list[str] = [map_vote.map_id for map_vote in map_votes]
    #     voted_maps: list[Map] = (
    #         session.query(Map).filter(Map.id.in_(voted_map_ids)).all()  # type: ignore
    #     )
    #     voted_maps_str = ", ".join(
    #         [
    #             f"{voted_map.short_name} [{voted_map_ids.count(voted_map.id)}/{MAP_VOTE_THRESHOLD}]"
    #             for voted_map in voted_maps
    #         ]
    #     )
    #     output += f"_Votes to change map (votemap): {voted_maps_str}_\n\n"
    #     session.close()
    #     await ctx.send(embed=Embed(description=output, colour=Colour.blue()))

    # TODO: decide where the random map comes from

    # @bot.command()
    # async def randommap(ctx: Context):
    #     session = ctx.session
    #     maps: list[Map] = session.query(Map).all()
    #     map = choice(maps)
    #     await send_message(
    #         ctx.message.channel,
    #         embed_description=f"Random map selected: **{map.full_name} ({map.short_name})**",
    #         colour=Colour.blue(),
    #     )

    # TODO: change to !setrandommap <map_short_name> <rotation> <random_probability>
    # random_probability stored in rotation_map

    # @bot.command(usage="<map_full_name> <map_short_name> <random_probability>")
    # @commands.check(is_admin)
    # async def addrandomrotationmap(
    #     ctx: Context, map_full_name: str, map_short_name: str, random_probability: float
    # ):
    #     """
    #     Adds a special map to the rotation that is random each time it comes up
    #     """
    #     message = ctx.message
    #     if random_probability < 0 or random_probability > 1:
    #         await send_message(
    #             message.channel,
    #             embed_description=f"Random map probability must be between 0 and 1!",
    #             colour=Colour.red(),
    #         )
    #         return

    #     session = ctx.session
    #     session.add(
    #         RotationMap(
    #             f"{map_full_name} (R)",
    #             f"{map_short_name}R",
    #             is_random=True,
    #             random_probability=random_probability,
    #         )
    #     )
    #     try:
    #         session.commit()
    #     except IntegrityError:
    #         session.rollback()
    #         await send_message(
    #             message.channel,
    #             embed_description=f"Error adding random map {map_full_name} ({map_short_name}) to rotation. Does it already exist?",
    #             colour=Colour.red(),
    #         )
    #         return

    #     await send_message(
    #         message.channel,
    #         embed_description=f"{map_full_name} (R) ({map_short_name}R) added to map rotation",
    #         colour=Colour.green(),
    #     )

    @group.command(name="remove", description="Remove a map from the map pool")
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(short_name="Short name of map")
    async def removemap(self, interaction: Interaction, short_name: str):
        """
        Remove a map from the map pool
        """
        session: SQLAlchemySession
        with Session() as session:
            try:
                map = session.query(Map).filter(Map.short_name.ilike(short_name)).one()
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find map **{short_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            map_rotations = (
                session.query(Rotation)
                .join(RotationMap, RotationMap.rotation_id == Rotation.id)
                .filter(RotationMap.map_id == map.id)
                .all()
            )

            if map_rotations:
                error = f"Please remove map from rotation first.\n\n**{map.short_name}** belongs to the following rotations:"
                for map_rotation in map_rotations:
                    error += f"\n- {map_rotation.name}"
                await interaction.response.send_message(
                    embed=Embed(
                        description=error,
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
            else:
                session.delete(map)
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"**{map.full_name} ({map.short_name})** removed from maps",
                        colour=Colour.green(),
                    )
                )

    @changequeuemap.autocomplete("queue_name")
    async def queue_autocomplete(self, interaction: Interaction, current: str):
        result = []
        session: SQLAlchemySession
        with Session() as session:
            queues: list[Queue] | None = session.query(Queue).limit(25).all()
            if queues:
                for queue in queues:
                    if current in queue.name:
                        result.append(
                            app_commands.Choice(name=queue.name, value=queue.name)
                        )
        return result

    @changequeuemap.autocomplete("short_name")
    @changegamemap.autocomplete("short_name")
    @removemap.autocomplete("short_name")
    async def map_autocomplete(self, interaction: Interaction, current: str):
        result = []
        session: SQLAlchemySession
        with Session() as session:
            maps: list[Map] | None = (
                session.query(Map).order_by(Map.full_name).limit(25).all()
            )
            if maps:
                for map in maps:
                    if current in map.short_name:
                        result.append(
                            app_commands.Choice(
                                name=map.full_name, value=map.short_name
                            )
                        )
        return result

    @changegamemap.autocomplete("game_id")
    async def game_autocomplete(self, interaction: Interaction, current: str):
        result = []
        session: SQLAlchemySession
        with Session() as session:
            in_progress_games: list[InProgressGame] | None = (
                session.query(InProgressGame).limit(25).all()
            )  # discord only supports up to 25 choices
            for ipg in in_progress_games:
                short_game_id = short_uuid(ipg.id)
                if current in short_game_id:
                    result.append(
                        app_commands.Choice(name=short_game_id, value=short_game_id)
                    )
        return result
