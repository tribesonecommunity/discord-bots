from datetime import datetime, timedelta, timezone

from discord.ext.commands import Bot, Context, check, command
from sqlalchemy.exc import IntegrityError

import discord_bots.config as config
from discord_bots.checks import is_admin
from discord_bots.cogs.base import BaseCog
from discord_bots.config import MAP_VOTE_THRESHOLD
from discord_bots.models import (
    InProgressGame,
    InProgressGamePlayer,
    Map,
    MapVote,
    Player,
    Queue,
    Rotation,
    RotationMap,
    Session,
    SkipMapVote,
    VotePassedWaitlist,
)
from discord_bots.utils import update_next_map_to_map_after_next


class VoteCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    def get_maps_str():
        maps: list[Map] = Session().query(Map).all()
        return ", ".join([map.short_name for map in maps])

    @command()
    @check(is_admin)
    async def setmapvotethreshold(self, ctx: Context, threshold: int):
        """
        Set the number of votes required to pass
        # TODO move to db-config, make dependent on queue size if possible
        """
        global MAP_VOTE_THRESHOLD
        MAP_VOTE_THRESHOLD = threshold

        await self.send_success_message(
            f"Map vote threshold set to {MAP_VOTE_THRESHOLD}"
        )

    @command()
    async def skipgamemap(self, ctx: Context):
        """
        Vote to skip to the next map for an in-progress game
        """
        message = ctx.message
        session = ctx.session

        ipgp: InProgressGamePlayer | None = (
            session.query(InProgressGamePlayer)
            .filter(InProgressGamePlayer.player_id == message.author.id)
            .first()
        )
        if not ipgp:
            await self.send_error_message("You must be in a game to use this")
            return

        session.add(SkipMapVote(message.channel.id, message.author.id))
        try:
            session.commit()
        except IntegrityError:
            await self.send_error_message("You have already voted")
            session.rollback()
            return

        ipg: InProgressGame | None = (
            session.query(InProgressGame)
            .filter(InProgressGame.id == ipgp.in_progress_game_id)
            .first()
        )

        skip_map_votes = (
            session.query(SkipMapVote)
            .join(
                InProgressGamePlayer,
                InProgressGamePlayer.player_id == SkipMapVote.player_id,
            )
            .filter(InProgressGamePlayer.in_progress_game_id == ipg.id)
            .all()
        )

        queue_vote_threshold = (
            session.query(Queue.vote_threshold)
            .join(InProgressGame, InProgressGame.queue_id == Queue.id)
            .filter(InProgressGame.id == ipg.id)
            .scalar()
        )

        if len(skip_map_votes) >= queue_vote_threshold:
            rotation: Rotation | None = (
                session.query(Rotation)
                .join(Queue, Queue.rotation_id == Rotation.id)
                .join(InProgressGame, InProgressGame.queue_id == Queue.id)
                .filter(InProgressGame.id == ipg.id)
                .first()
            )
            new_map: Map | None = (
                session.query(Map)
                .join(RotationMap, RotationMap.map_id == Map.id)
                .join(Rotation, Rotation.id == RotationMap.rotation_id)
                .filter(Rotation.id == rotation.id)
                .filter(RotationMap.is_next == True)
                .first()
            )

            ipg.map_full_name = new_map.full_name
            ipg.map_short_name = new_map.short_name
            for skip_map_vote in skip_map_votes:
                session.delete(skip_map_vote)
            session.commit()

            await self.send_success_message(
                f"Vote to skip the current map passed!  All votes removed.\n\nNew map: **{ipg.map_full_name} ({ipg.map_short_name})**"
            )
            await update_next_map_to_map_after_next(rotation.id, True)
        else:
            await self.send_success_message("Your vote has been cast!")

    @command()
    async def unvote(self, ctx: Context):
        """
        Remove all of a player's votes
        """
        message = ctx.message
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
        Remove all of a player's votes for a map
        Use irrespective of rotation/queue because that seems like a super niche use case
        TODO: Unvote for many maps at once
        """
        session = ctx.session
        message = ctx.message

        map: Map | None = (
            session.query(Map).filter(Map.short_name.ilike(map_short_name)).first()
        )

        map_votes: list[MapVote] | None = (
            session.query(MapVote)
            .join(RotationMap, RotationMap.id == MapVote.rotation_map_id)
            .filter(
                MapVote.player_id == message.author.id,
                RotationMap.map_id == map.id,
            )
            .all()
        )
        if not map_votes:
            await self.send_error_message(
                f"You don't have any votes for {map_short_name}"
            )
            return

        for map_vote in map_votes:
            session.delete(map_vote)
        session.commit()
        await self.send_success_message(f"Your vote for {map.short_name} was removed")

    @command()
    async def unvoteskip(self, ctx: Context):
        """
        Remove all of a player's votes to skip the next map
        Same disregard for rotation/queue as with unvotemap
        """
        session = ctx.session
        message = ctx.message

        skip_map_votes: SkipMapVote | None = (
            session.query(SkipMapVote)
            .filter(SkipMapVote.player_id == message.author.id)
            .all()
        )
        if not skip_map_votes:
            await self.send_error_message(
                "You don't have a vote to skip the current map."
            )
            return

        for skip_map_vote in skip_map_votes:
            session.delete(skip_map_vote)
        session.commit()
        await self.send_success_message(
            "Your vote to skip the current map was removed."
        )

    @command(usage="<map|skip>")
    @check(is_admin)
    async def mockvotes(self, ctx: Context, type: str, count: int):
        """
        Generates 6 mock votes for testing
        Testing must be done quick because afk_timer_task clears the votes every minute

        map: mocks MapVote entries for first rotation_map
        skip: mocks SkipMapVote entries for first rotation
        """

        message = ctx.message
        session = ctx.session

        lesser_gods = [115204465589616646, 347125254050676738, 508003755220926464]

        if message.author.id not in lesser_gods:
            await self.send_error_message("Only special people can use this command")
            return

        if type == "map":
            rotation_map: RotationMap | None = session.query(RotationMap).first()

            player_ids = [
                x[0]
                for x in session.query(Player.id)
                .filter(Player.id.not_in(lesser_gods))
                .limit(count)
                .all()
            ]

            for player_id in player_ids:
                session.add(
                    MapVote(
                        message.channel.id,
                        player_id,
                        rotation_map.id,
                    )
                )

            queue_name = (
                session.query(Queue.name)
                .join(Rotation, Rotation.id == Queue.rotation_id)
                .filter(Rotation.id == rotation_map.rotation_id)
                .first()[0]
            )
            map_short_name = (
                session.query(Map.short_name)
                .filter(Map.id == rotation_map.map_id)
                .first()[0]
            )
            final_vote_command = f"!votemap {queue_name} {map_short_name}"
        elif type == "skip":
            rotation: Rotation | None = session.query(Rotation).first()

            player_ids = [
                x[0]
                for x in session.query(Player.id)
                .filter(Player.id.not_in(lesser_gods))
                .limit(count)
                .all()
            ]

            for player_id in player_ids:
                session.add(
                    SkipMapVote(
                        message.channel.id,
                        player_id,
                        rotation.id,
                    )
                )

            queue_name = (
                session.query(Queue.name)
                .join(Rotation, Rotation.id == Queue.rotation_id)
                .filter(Rotation.id == rotation.id)
                .first()[0]
            )
            final_vote_command = f"!voteskip {queue_name}"
        elif type == "skipgame":
            player_ids = [
                x[0]
                for x in session.query(InProgressGamePlayer.player_id)
                .filter(InProgressGamePlayer.player_id.not_in(lesser_gods))
                .limit(count)
                .all()
            ]
            for player_id in player_ids:
                session.add(SkipMapVote(message.channel.id, player_id))
            final_vote_command = "!skipgamemap"
        else:
            await self.send_error_message("Usage: !mockvotes <map|skip|skipgame>")
            return

        session.commit()

        await self.send_success_message(
            f"Mock votes added!\nTo add your vote use `{final_vote_command}`"
        )

    # @command()
    async def votemap(self, ctx: Context, queue_name: str, map_short_name: str):
        """
        Vote for a map in a queue
        TODO: Vote for many maps at once
        TODO: Decide if/how to list voteable maps for each queue/rotation
        """
        session = ctx.session
        message = ctx.message

        queue: Queue | None = (
            session.query(Queue).filter(Queue.name.ilike(queue_name)).first()
        )
        if not queue:
            await self.send_error_message(f"Could not find queue **{queue_name}**")
            return

        rotation: Rotation | None = (
            session.query(Rotation)
            .join(Queue, Queue.rotation_id == Rotation.id)
            .filter(Queue.id == queue.id)
            .first()
        )

        map: Map | None = (
            session.query(Map).filter(Map.short_name.ilike(map_short_name)).first()
        )

        rotation_map: RotationMap | None = (
            session.query(RotationMap)
            .filter(RotationMap.map_id == map.id)
            .filter(RotationMap.rotation_id == rotation.id)
            .first()
        )

        session.add(MapVote(message.channel.id, message.author.id, rotation_map.id))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()

        rotation_map_votes: list[MapVote] = (
            session.query(MapVote)
            .filter(MapVote.rotation_map_id == rotation_map.id)
            .all()
        )
        if len(rotation_map_votes) >= config.MAP_VOTE_THRESHOLD:
            session.query(RotationMap).filter(
                RotationMap.rotation_id == rotation.id
            ).filter(RotationMap.is_next == True).update({"is_next": False})
            rotation_map.is_next = True

            map_votes = (
                session.query(MapVote)
                .join(RotationMap, RotationMap.id == MapVote.rotation_map_id)
                .filter(RotationMap.rotation_id == rotation.id)
                .all()
            )
            for map_vote in map_votes:
                session.delete(map_vote)
            session.query(SkipMapVote).filter(
                SkipMapVote.rotation_id == rotation.id
            ).delete()

            if message.guild:
                # TODO: Check if another vote already exists
                session.add(
                    VotePassedWaitlist(
                        channel_id=message.channel.id,
                        guild_id=message.guild.id,
                        end_waitlist_at=datetime.now(timezone.utc)
                        + timedelta(seconds=config.RE_ADD_DELAY),
                    )
                )
            session.commit()

            await self.send_success_message(
                f"Vote for **{map.full_name} ({map.short_name})** passed!\nMap rotated, all votes removed"
            )
        else:
            map_votes = (
                session.query(MapVote)
                .filter(MapVote.rotation_map_id == rotation_map.id)
                .count()
            )

            await self.send_success_message(
                f"Added map vote for **{map.short_name}** in **{queue.name}**.\n`!unvotemap` to remove your vote.\nMap vote status: [{map_votes}/{config.MAP_VOTE_THRESHOLD}]"
            )

            # old logic for showing current vote status
            # map_votes: list[MapVote] = session.query(MapVote).all()
            # voted_map_ids: list[str] = [map_vote.map_id for map_vote in map_votes]
            # voted_maps: list[Map] = (
            #     session.query(Map).filter(Map.id.in_(voted_map_ids)).all()  # type: ignore
            # )
            # voted_maps_str = ", ".join(
            #     [
            #         f"{voted_map.short_name} [{voted_map_ids.count(voted_map.id)}/{config.MAP_VOTE_THRESHOLD}]"
            #         for voted_map in voted_maps
            #     ]
            # )

    # @command()
    async def voteskip(self, ctx: Context, queue_name: str):
        """
        Vote to skip a map in a queue
        """
        session = ctx.session
        message = ctx.message

        queue: Queue | None = (
            session.query(Queue).filter(Queue.name.ilike(queue_name)).first()
        )
        if not queue:
            await self.send_error_message(f"Could not find queue **{queue_name}**")
            return

        rotation: Rotation | None = (
            session.query(Rotation)
            .join(Queue, Queue.rotation_id == Rotation.id)
            .filter(Queue.id == queue.id)
            .first()
        )

        session.add(SkipMapVote(message.channel.id, message.author.id, rotation.id))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()

        skip_map_votes_count = (
            session.query(SkipMapVote)
            .filter(SkipMapVote.rotation_id == rotation.id)
            .count()
        )
        if skip_map_votes_count >= config.MAP_VOTE_THRESHOLD:
            await self.send_success_message(
                f"Vote to skip the current map passed!  All votes removed."
            )
            await update_next_map_to_map_after_next(rotation.id, True)

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
                            + timedelta(seconds=config.RE_ADD_DELAY),
                        )
                    )

            session.commit()
        else:
            await self.send_success_message(
                f"Added vote to skip the current map.\n!unvoteskip to remove vote.\nVotes to skip: [{skip_map_votes_count}/{config.MAP_VOTE_THRESHOLD}]"
            )
