# The discord bot doesn't like to execute things off the main thread. Instead we
# use queues to be able to execute discord actions from child threads.
# https://stackoverflow.com/a/67996748

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from random import shuffle
from re import I

import discord
import sqlalchemy
from discord.channel import TextChannel
from discord.colour import Colour
from discord.ext import tasks
from discord.guild import Guild
from discord.member import Member
from discord.utils import escape_markdown

import discord_bots.config as config
from discord_bots.utils import (
    code_block,
    print_leaderboard,
    send_message,
    short_uuid,
    update_next_map_to_map_after_next,
)

from .bot import bot
from .cogs.economy import EconomyCommands
from .commands import add_player_to_queue, create_game, is_in_game
from .models import (
    InProgressGame,
    InProgressGameChannel,
    InProgressGamePlayer,
    MapVote,
    Player,
    PlayerCategoryTrueskill,
    Queue,
    QueuePlayer,
    QueueWaitlist,
    QueueWaitlistPlayer,
    Rotation,
    RotationMap,
    Session,
    SkipMapVote,
    VotePassedWaitlist,
    VotePassedWaitlistPlayer,
)
from .queues import AddPlayerQueueMessage, add_player_queue

_log = logging.getLogger(__name__)


@tasks.loop(minutes=1)
async def afk_timer_task():
    session = Session()
    timeout: datetime = datetime.now(timezone.utc) - timedelta(
        minutes=config.AFK_TIME_MINUTES
    )

    player: Player
    for player in (
        session.query(Player)
        .join(QueuePlayer)
        .filter(Player.last_activity_at < timeout, QueuePlayer.player_id == Player.id)
    ):
        queue_player = (
            session.query(QueuePlayer)
            .filter(QueuePlayer.player_id == player.id)
            .first()
        )
        if queue_player:
            channel = bot.get_channel(queue_player.channel_id)
            if channel and isinstance(channel, TextChannel):
                member: Member | None = channel.guild.get_member(player.id)
                if member:
                    await send_message(
                        channel,
                        content=member.mention,
                        embed_content=False,
                        embed_description=f"{escape_markdown(player.name)} was removed from all queues for being inactive for {config.AFK_TIME_MINUTES} minutes",
                        colour=Colour.red(),
                    )
            session.query(QueuePlayer).filter(
                QueuePlayer.player_id == player.id
            ).delete()
            session.commit()

    votes_removed_sent = False
    for player in (
        session.query(Player)
        .join(MapVote)
        .filter(Player.last_activity_at < timeout, MapVote.player_id == Player.id)
    ):
        map_votes: list[MapVote] = (
            session.query(MapVote).filter(MapVote.player_id == player.id).all()
        )
        if len(map_votes) > 0:
            channel = bot.get_channel(map_votes[0].channel_id)
            if channel and isinstance(channel, TextChannel):
                member: Member | None = channel.guild.get_member(player.id)
                if member:
                    await send_message(
                        channel,
                        content=member.mention,
                        embed_content=False,
                        embed_description=f"{escape_markdown(player.name)}'s votes removed for being inactive for {config.AFK_TIME_MINUTES} minutes",
                        colour=Colour.red(),
                    )
                    votes_removed_sent = True
            session.query(MapVote).filter(MapVote.player_id == player.id).delete()
            session.commit()

    for player in (
        session.query(Player)
        .join(SkipMapVote)
        .filter(Player.last_activity_at < timeout, SkipMapVote.player_id == Player.id)
    ):
        skip_map_votes: list[SkipMapVote] = (
            session.query(SkipMapVote).filter(SkipMapVote.player_id == player.id).all()
        )
        if len(skip_map_votes) > 0:
            # So we don't send this message twice
            if not votes_removed_sent:
                channel = bot.get_channel(skip_map_votes[0].channel_id)
                if channel and isinstance(channel, TextChannel):
                    member: Member | None = channel.guild.get_member(player.id)
                    if member:
                        await send_message(
                            channel,
                            content=member.mention,
                            embed_content=False,
                            embed_description=f"{escape_markdown(player.name)}'s votes removed for being inactive for {config.AFK_TIME_MINUTES} minutes",
                            colour=Colour.red(),
                        )
            session.query(SkipMapVote).filter(
                SkipMapVote.player_id == player.id
            ).delete()
            session.commit()
    session.close()


@tasks.loop(seconds=1)
async def queue_waitlist_task():
    """
    Move players in the waitlist into the queues. Pop queues if needed.

    This exists as a task so that it happens on the main thread. Sqlite doesn't
    like to do writes on a second thread.

    TODO: Tests for this method
    """
    session: sqlalchemy.orm.Session
    with Session() as session:
        queues: list[Queue] = session.query(Queue).order_by(Queue.ordinal.asc())  # type: ignore
        queue_waitlist: QueueWaitlist
        channel = None
        guild: Guild | None = None
        for queue_waitlist in session.query(QueueWaitlist).filter(
            QueueWaitlist.end_waitlist_at < datetime.now(timezone.utc)
        ):
            if not channel:
                channel = bot.get_channel(queue_waitlist.channel_id)
            if not guild:
                guild = bot.get_guild(queue_waitlist.guild_id)

            queue_waitlist_players: list[QueueWaitlistPlayer]
            queue_waitlist_players = (
                session.query(QueueWaitlistPlayer)
                .filter(QueueWaitlistPlayer.queue_waitlist_id == queue_waitlist.id)
                .all()
            )
            qwp_by_queue_id: dict[str, list[QueueWaitlistPlayer]] = defaultdict(list)
            for qwp in queue_waitlist_players:
                if qwp.queue_id:
                    qwp_by_queue_id[qwp.queue_id].append(qwp)

            # Ensure that we process the queues in the order the queues were
            # created. TODO: Make the last queue that popped the lowest priority
            for queue in queues:
                qwps_for_queue = qwp_by_queue_id[queue.id]
                shuffle(qwps_for_queue)
                for queue_waitlist_player in qwps_for_queue:
                    if is_in_game(queue_waitlist_player.player_id):
                        session.delete(queue_waitlist_player)
                        continue

                    if isinstance(channel, TextChannel) and guild:
                        player = (
                            session.query(Player)
                            .filter(Player.id == queue_waitlist_player.player_id)
                            .first()
                        )

                        add_player_queue.put(
                            AddPlayerQueueMessage(
                                queue_waitlist_player.player_id,
                                player.name,
                                # TODO: This is sucky to do it one at a time
                                [queue.id],
                                False,
                                channel,
                                guild,
                            )
                        )
            # cleanup any InProgressGameChannels that are hanging around
            for igp_channel in session.query(InProgressGameChannel).filter(
                InProgressGameChannel.in_progress_game_id
                == queue_waitlist.in_progress_game_id
            ):
                if guild:
                    guild_channel = guild.get_channel(igp_channel.channel_id)
                    if guild_channel:
                        await guild_channel.delete()
                session.delete(igp_channel)
            session.query(InProgressGame).filter(
                InProgressGame.id == queue_waitlist.in_progress_game_id
            ).delete()

            session.query(QueueWaitlistPlayer).filter(
                QueueWaitlistPlayer.queue_waitlist_id == queue_waitlist.id
            ).delete()
            session.delete(queue_waitlist)
        session.commit()


@tasks.loop(seconds=1)
async def vote_passed_waitlist_task():
    """
    Move players in the waitlist into the queues. Pop queues if needed.

    This exists as a task so that it happens on the main thread. Sqlite doesn't
    like to do writes on a second thread.

    TODO: Tests for this method
    """
    session = Session()
    vpw: VotePassedWaitlist | None = (
        session.query(VotePassedWaitlist)
        .filter(VotePassedWaitlist.end_waitlist_at < datetime.now(timezone.utc))
        .first()
    )
    if not vpw:
        return

    channel = bot.get_channel(vpw.channel_id)
    guild: Guild | None = bot.get_guild(vpw.guild_id)
    queues: list[Queue] = session.query(Queue).order_by(Queue.created_at.asc())  # type: ignore

    # TODO: Do we actually need to filter by id?
    vote_passed_waitlist_players: list[VotePassedWaitlistPlayer] = (
        session.query(VotePassedWaitlistPlayer)
        .filter(VotePassedWaitlistPlayer.vote_passed_waitlist_id == vpw.id)
        .all()
    )
    vpwp_by_queue_id: dict[str, list[VotePassedWaitlistPlayer]] = defaultdict(list)
    for vote_passed_waitlist_player in vote_passed_waitlist_players:
        vpwp_by_queue_id[vote_passed_waitlist_player.queue_id].append(
            vote_passed_waitlist_player
        )

    # Ensure that we process the queues in the order the queues were created
    for queue in queues:
        vpwps_for_queue = vpwp_by_queue_id[queue.id]
        shuffle(vpwps_for_queue)
        for vote_passed_waitlist_player in vpwps_for_queue:
            if is_in_game(vote_passed_waitlist_player.player_id):
                session.delete(vote_passed_waitlist_player)
                continue

            if isinstance(channel, TextChannel) and guild:
                player = (
                    session.query(Player)
                    .filter(Player.id == vote_passed_waitlist_player.player_id)
                    .first()
                )

                add_player_queue.put(
                    AddPlayerQueueMessage(
                        vote_passed_waitlist_player.player_id,
                        player.name,
                        # TODO: This is sucky to do it one at a time
                        [queue.id],
                        False,
                        channel,
                        guild,
                    )
                )

    session.query(VotePassedWaitlistPlayer).filter(
        VotePassedWaitlistPlayer.vote_passed_waitlist_id == vpw.id
    ).delete()
    session.delete(vpw)
    session.commit()
    session.close()


@tasks.loop(minutes=1)
async def map_rotation_task():
    """Rotate the map automatically, stopping on the 1st map
    TODO: tests
    """
    if config.DISABLE_MAP_ROTATION:
        return

    session = Session()

    rotations: list[Rotation] | None = session.query(Rotation).all()
    if not rotations:
        session.close()
        return

    for rotation in rotations:
        next_rotation_map: RotationMap | None = (
            session.query(RotationMap)
            .filter(RotationMap.rotation_id == rotation.id)
            .filter(RotationMap.is_next == True)
            .first()
        )
        if next_rotation_map and next_rotation_map.ordinal != 1:
            time_since_update: timedelta = datetime.now(
                timezone.utc
            ) - next_rotation_map.updated_at.replace(tzinfo=timezone.utc)
            if (time_since_update.seconds // 60) > config.MAP_ROTATION_MINUTES:
                await update_next_map_to_map_after_next(rotation.id, True)
    session.close()


async def add_players(session: sqlalchemy.orm.Session):
    """
    Handle adding players in a task that pulls messages off of a queue.

    This helps with concurrency issues since players can be added from multiple
    sources (waitlist vs normal add command)
    """
    if add_player_queue.empty():
        # check if the queue is empty up front to avoid emitting any SQL
        return
    queues: list[Queue] = session.query(Queue).order_by(Queue.ordinal.asc()).all()
    queue_by_id: dict[str, Queue] = {queue.id: queue for queue in queues}
    message: AddPlayerQueueMessage | None = None
    while not add_player_queue.empty():
        queues_added_to: list[Queue] = []
        message: AddPlayerQueueMessage = add_player_queue.get()
        queue_popped = False
        for queue_id in message.queue_ids:
            queue: Queue = queue_by_id[queue_id]
            if queue.is_locked:
                continue

            added_to_queue, queue_popped = await add_player_to_queue(
                queue.id, message.player_id, message.channel, message.guild
            )
            if queue_popped:
                break
            if added_to_queue:
                queues_added_to.append(queue)

        if not queue_popped and message.should_print_status:
            queue: Queue
            embed = discord.Embed()
            for queue in queues_added_to:
                if queue.is_locked:
                    continue
                queue_players = (
                    Session()
                    .query(QueuePlayer)
                    .filter(QueuePlayer.queue_id == queue.id)
                    .all()
                )
                queue_title_str = f"(**{queue.ordinal}**) {queue.name} [{len(queue_players)}/{queue.size}]"
                player_display_names: list[str] = []
                for qp in queue_players:
                    user: discord.User | None = bot.get_user(qp.player_id)
                    if user:
                        player_display_names.append(user.display_name)
                    else:
                        player: Player | None = (
                            session.query(Player).join(QueuePlayer).first()
                        )
                        if player:
                            player_display_names.append(player.name)
                        else:
                            player_display_names.append(f"<@{qp.player_id}>")
                # player_mentions = ", ".join(
                # [f"<@{qp.player_id}>" for qp in queue_players]
                # )
                embed.add_field(
                    name=queue_title_str,
                    # value="" if not player_mentions else f"> {player_mentions}",
                    value=(
                        ""
                        if not player_display_names
                        else f"> {', '.join(player_display_names)}"
                    ),
                    inline=False,
                )

            if queues_added_to:
                queue_names = [queue.name for queue in queues_added_to]
                embed.description = (
                    f"<@{message.player_id}> added to **{', '.join(queue_names)}**"
                )
                embed.color = discord.Color.green()
            else:
                embed.description = f"<@{message.player_id}> no valid queues specified"
                embed.color = discord.Color.red()
            await message.channel.send(
                embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )

    # No messages processed, so no way that sweaty queues popped
    if not message:
        return

    # Handle sweaty queues
    for queue in queues:
        if not queue.is_sweaty:
            continue
        queue_players: list[QueuePlayer] = (
            session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue.id).all()
        )
        if len(queue_players) >= queue.size:
            player_ids: list[int] = list(map(lambda x: x.player_id, queue_players))
            if queue.category_id:
                pcts = session.query(PlayerCategoryTrueskill).filter(
                    PlayerCategoryTrueskill.player_id.in_(player_ids),
                    PlayerCategoryTrueskill.category_id == queue.category_id,
                )
                top_player_ids = [
                    prt.player_id
                    for prt in sorted(
                        pcts,
                        key=lambda prt: prt.rated_trueskill_mu,
                        reverse=True,
                    )[: queue.size]
                ]
            else:
                players: list[Player] = (
                    session.query(Player).filter(Player.id.in_(player_ids)).all()
                )
                top_player_ids = [
                    player.id
                    for player in sorted(
                        players,
                        key=lambda player: player.rated_trueskill_mu,
                        reverse=True,
                    )[: queue.size]
                ]
            await create_game(
                queue_id=queue.id,
                player_ids=top_player_ids,
                channel=message.channel,
                guild=message.guild,
            )


@tasks.loop(seconds=1)
async def add_player_task():
    session: sqlalchemy.orm.Session
    with Session() as session:
        await add_players(session)

@tasks.loop(seconds=1800)
async def leaderboard_task():
    """
    Periodically print the leaderboard
    """
    await print_leaderboard()


@tasks.loop(seconds=5)
async def prediction_task():
    """
    Updates prediction embeds.
    Closes prediction after submission period
    """
    session = Session()
    in_progress_games: list[InProgressGame] | None = (
            session.query(InProgressGame)
            .filter(InProgressGame.prediction_open == True)
            .all()
        )
    try:
        await EconomyCommands.update_embeds(None, in_progress_games)
    except Exception as e:
        # Task fails if attemping to update an embed that hasn't been posted yet
        # Occurs during game channel creation depending on when task runs.
        # Cleanly cancel & restart task to resolve
        _log.warning(e)
        _log.info("prediction_task restarting...")
        session.close()
        prediction_task.cancel()
        prediction_task.restart()

    await EconomyCommands.close_predictions(None, in_progress_games=in_progress_games)
    session.close()
