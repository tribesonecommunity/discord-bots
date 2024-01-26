from discord.ext.commands import Bot, Context, check, command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound

from discord_bots.checks import is_admin
from discord_bots.cogs.base import BaseCog


class Vote(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    def get_maps_str():
        maps: list[Map] = Session().query(Map).all()
        return ", ".join([map.short_name for map in maps])

    @command()
    @check(is_admin)
    async def setmapvotethreshold(self, ctx: Context, threshold: int):
        global MAP_VOTE_THRESHOLD
        MAP_VOTE_THRESHOLD = threshold

        await self.send_success_message(
            f"Map vote threshold set to {MAP_VOTE_THRESHOLD}"
        )

    @command()
    async def unvote(self, ctx: Context):
        """
        Remove all of a player's votes
        """
        session = ctx.session
        session.query(MapVote).filter(MapVote.player_id == message.author.id).delete()
        session.query(SkipMapVote).filter(
            SkipMapVote.player_id == message.author.id
        ).delete()
        session.commit()
        await self.send_success_message("All map votes deleted")

    @command()
    async def unvotemap(self, ctx: Context, map_short_name: str):
        """
        TODO: Unvote for many maps at once
        """
        session = ctx.session
        map: Map | None = session.query(Map).filter(Map.short_name.ilike(args[0])).first()  # type: ignore
        if not map:
            await self.send_error_message(
                f"Could not find voteable map: {map_short_name}"
            )
            return
        map_vote: MapVote | None = (
            session.query(MapVote)
            .filter(
                MapVote.player_id == message.author.id,
                MapVote.map_id == map.id,
            )
            .first()
        )
        if not map_vote:
            await self.send_error_message(
                f"You don't have a vote for: {map_short_name}"
            )
            return
        else:
            session.delete(map_vote)
        session.commit()
        await self.send_success_message(f"Your vote for {map_short_name} was removed")

    @command()
    async def unvoteskip(self, ctx: Context):
        """
        A player votes to go to the next map in rotation
        """
        session = ctx.session
        skip_map_vote: SkipMapVote | None = (
            session.query(SkipMapVote)
            .filter(SkipMapVote.player_id == message.author.id)
            .first()
        )
        if skip_map_vote:
            session.delete(skip_map_vote)
            session.commit()
            await self.send_success_message(
                "Your vote to skip the current map was removed."
            )
        else:
            await self.send_error_message(
                "You don't have a vote to skip the current map."
            )

    @command(usage=f"<map_short_name>\nMaps:{get_maps_str()}")
    async def votemap(self, ctx: Context, map_short_name: str):
        """
        TODO: Vote for many maps at once
        """
        session = ctx.session
        map: Map | None = session.query(Map).filter(Map.short_name.ilike(map_short_name)).first()  # type: ignore
        if not map:
            await self.send_error_message(
                f"Could not find voteable map: {map_short_name}\nMaps: {get_maps_str()}"
            )
            return

        session.add(MapVote(message.channel.id, message.author.id, map_id=map.id))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()

        map_votes: list[MapVote] = (
            Session().query(MapVote).filter(MapVote.map_id == map.id).all()
        )
        if len(map_votes) == MAP_VOTE_THRESHOLD:
            current_map: CurrentMap | None = session.query(CurrentMap).first()
            if current_map:
                current_map.full_name = map.full_name
                current_map.short_name = map.short_name
                current_map.updated_at = datetime.now(timezone.utc)
            else:
                session.add(
                    CurrentMap(
                        full_name=map.full_name,
                        map_rotation_index=0,
                        short_name=map.short_name,
                    )
                )
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()

            await self.send_success_message(
                f"Vote for {map.full_name} ({map.short_name}) passed!\n**New map: {map.full_name} ({map.short_name})**"
            )
            session.query(MapVote).delete()
            session.query(SkipMapVote).delete()
            if message.guild:
                # TODO: Check if another vote already exists
                session.add(
                    VotePassedWaitlist(
                        channel_id=message.channel.id,
                        guild_id=message.guild.id,
                        end_waitlist_at=datetime.now(timezone.utc)
                        + timedelta(seconds=RE_ADD_DELAY),
                    )
                )
            session.commit()
        else:
            map_votes: list[MapVote] = session.query(MapVote).all()
            voted_map_ids: list[str] = [map_vote.map_id for map_vote in map_votes]
            voted_maps: list[Map] = (
                session.query(Map).filter(Map.id.in_(voted_map_ids)).all()  # type: ignore
            )
            voted_maps_str = ", ".join(
                [
                    f"{voted_map.short_name} [{voted_map_ids.count(voted_map.id)}/{MAP_VOTE_THRESHOLD}]"
                    for voted_map in voted_maps
                ]
            )
            await self.send_success_message(
                f"Added map vote for {map_short_name}.\n!unvotemap to remove your vote.\nVotes: {voted_maps_str}"
            )

        session.commit()

    @command()
    async def voteskip(self, ctx: Context):
        """
        A player votes to go to the next map in rotation
        """
        session = ctx.session
        session.add(SkipMapVote(message.channel.id, message.author.id))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()

        skip_map_votes: list[SkipMapVote] = Session().query(SkipMapVote).all()
        if len(skip_map_votes) >= MAP_VOTE_THRESHOLD:
            await update_current_map_to_next_map_in_rotation(False)
            current_map: CurrentMap = Session().query(CurrentMap).first()
            await self.send_success_message(
                f"Vote to skip the current map passed!\n**New map: {current_map.full_name} ({current_map.short_name})**"
            )

            session.query(MapVote).delete()
            session.query(SkipMapVote).delete()
            if message.guild:
                # TODO: Might be bugs if two votes pass one after the other
                vpw: VotePassedWaitlist | None = session.query(
                    VotePassedWaitlist
                ).first()
                if not vpw:
                    session.add(
                        VotePassedWaitlist(
                            channel_id=message.channel.id,
                            guild_id=message.guild.id,
                            end_waitlist_at=datetime.now(timezone.utc)
                            + timedelta(seconds=RE_ADD_DELAY),
                        )
                    )
            session.commit()
        else:
            skip_map_votes: list[SkipMapVote] = session.query(SkipMapVote).all()
            await self.send_success_message(
                f"Added vote to skip the current map.\n!unvoteskip to remove vote.\nVotes to skip: [{len(skip_map_votes)}/{MAP_VOTE_THRESHOLD}]"
            )
