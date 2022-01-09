# The discord bot doesn't like to execute things off the main thread. Instead we
# use queues to be able to execute discord actions from child threads.
# https://stackoverflow.com/a/67996748

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from random import shuffle
from re import I

from discord.channel import TextChannel
from discord.colour import Colour
from discord.ext import tasks
from discord.guild import Guild
from discord.member import Member

from .bot import bot
from .commands import (
    AFK_TIME_MINUTES,
    MAP_ROTATION_MINUTES,
    add_player_to_queue,
    is_in_game,
    send_message,
)
from .models import (
    CurrentMap,
    InProgressGame,
    InProgressGameChannel,
    MapVote,
    Player,
    Queue,
    QueuePlayer,
    QueueWaitlist,
    QueueWaitlistPlayer,
    Session,
    SkipMapVote,
)
from .queues import AddPlayerQueueMessage, add_player_queue
from .utils import update_current_map_to_next_map_in_rotation


@tasks.loop(minutes=1)
async def afk_timer_task():
    session = Session()
    timeout: datetime = datetime.now(timezone.utc) - timedelta(minutes=AFK_TIME_MINUTES)

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
                        embed_description=f"{player.name} was removed from all queues for being inactive for {AFK_TIME_MINUTES} minutes",
                        colour=Colour.red(),
                    )
            session.query(QueuePlayer).filter(
                QueuePlayer.player_id == player.id
            ).delete()
            session.commit()

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
                        embed_description=f"{player.name}'s votes removed for being inactive for {AFK_TIME_MINUTES} minutes",
                        colour=Colour.red(),
                    )
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
            channel = bot.get_channel(skip_map_votes[0].channel_id)
            if channel and isinstance(channel, TextChannel):
                member: Member | None = channel.guild.get_member(player.id)
                if member:
                    await send_message(
                        channel,
                        content=member.mention,
                        embed_content=False,
                        embed_description=f"{player.name}'s votes removed for being inactive for {AFK_TIME_MINUTES} minutes",
                        colour=Colour.red(),
                    )
            session.query(SkipMapVote).filter(
                SkipMapVote.player_id == player.id
            ).delete()
            session.commit()


@tasks.loop(seconds=1)
async def queue_waitlist_task():
    """
    Move players in the waitlist into the queues. Pop queues if needed.

    This exists as a task so that it happens on the main thread. Sqlite doesn't
    like to do writes on a second thread.

    TODO: Tests for this method
    """
    session = Session()
    queues: list[Queue] = session.query(Queue).order_by(Queue.created_at.asc())  # type: ignore
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

                # if channel and guild and isinstance(channel, TextChannel):
                #     # Bugfix - TODO: Add tests
                #     if queue_waitlist_player.queue_id:
                #         await add_player_to_queue(
                #             queue_waitlist_player.queue_id,
                #             queue_waitlist_player.player_id,
                #             channel,
                #             guild,
                #         )

        for igp_channel in session.query(InProgressGameChannel).filter(
            InProgressGameChannel.in_progress_game_id
            == queue_waitlist.in_progress_game_id
        ):
            if guild:
                guild_channel = guild.get_channel(igp_channel.channel_id)
                if guild_channel:
                    await guild_channel.delete()
            session.delete(igp_channel)

        session.query(QueueWaitlistPlayer).filter(
            QueueWaitlistPlayer.queue_waitlist_id == queue_waitlist.id
        ).delete()
        session.delete(queue_waitlist)
    session.commit()


@tasks.loop(minutes=1)
async def map_rotation_task():
    """Rotate the map automatically, stopping on the 0th map
    TODO: tests
    """
    session = Session()
    current_map: CurrentMap | None = session.query(CurrentMap).first()
    if not current_map:
        return

    if current_map.map_rotation_index == 0:
        # Stop at the first map
        return

    time_since_update: timedelta = datetime.now(
        timezone.utc
    ) - current_map.updated_at.replace(tzinfo=timezone.utc)
    if (time_since_update.seconds // 60) > MAP_ROTATION_MINUTES:
        # TODO: Need to announce to the server, get a handle to a channel /
        # guild
        update_current_map_to_next_map_in_rotation()


@tasks.loop(seconds=1)
async def add_player_task():
    """
    Handle adding players in a task that pulls messages off of a queue.

    This helps with concurrency issues since players can be added from multiple
    sources (waitlist vs normal add command)
    """
    queues: list[Queue] = Session().query(Queue).all()
    queue_by_id: dict[str, Queue] = {queue.id: queue for queue in queues}
    while not add_player_queue.empty():
        queues_added_to: list[str] = []
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
                print("queue popped", message)
                break
            if added_to_queue:
                queues_added_to.append(queue.name)

        if not queue_popped and message.should_print_status:
            queue_statuses = []
            queue: Queue
            session = Session()
            for queue in queues:
                queue_players = (
                    Session()
                    .query(QueuePlayer)
                    .filter(QueuePlayer.queue_id == queue.id)
                    .all()
                )

                in_progress_games: list[InProgressGame] = (
                    session.query(InProgressGame)
                    .filter(InProgressGame.queue_id == queue.id)
                    .all()
                )

                if len(in_progress_games) > 0:
                    queue_statuses.append(
                        f"{queue.name} [{len(queue_players)}/{queue.size}] *(In game)*"
                    )
                else:
                    queue_statuses.append(
                        f"{queue.name} [{len(queue_players)}/{queue.size}]"
                    )

            await send_message(
                message.channel,
                content=f"{message.player_name} added to: {', '.join(queues_added_to)}",
                embed_description=" ".join(queue_statuses),
                colour=Colour.green(),
            )
