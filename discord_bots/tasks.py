# The discord bot doesn't like to execute things off the main thread. Instead we
# use queues to be able to execute discord actions from child threads.
# https://stackoverflow.com/a/67996748

from datetime import datetime, timedelta, timezone
from random import shuffle

from discord.channel import TextChannel
from discord.colour import Colour
from discord.ext import tasks
from discord.member import Member

from discord_bots.utils import update_current_map_to_next_map_in_rotation

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
    InProgressGameChannel,
    MapVote,
    Player,
    QueuePlayer,
    QueueWaitlist,
    QueueWaitlistPlayer,
    Session,
    SkipMapVote,
)


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
    queue_waitlist: QueueWaitlist
    for queue_waitlist in session.query(QueueWaitlist).filter(
        QueueWaitlist.end_waitlist_at < datetime.now(timezone.utc)
    ):
        channel = bot.get_channel(queue_waitlist.channel_id)
        guild = bot.get_guild(queue_waitlist.guild_id)

        queue_waitlist_players: list[QueueWaitlistPlayer]
        queue_waitlist_players = (
            session.query(QueueWaitlistPlayer)
            .filter(QueueWaitlistPlayer.queue_waitlist_id == queue_waitlist.id)
            .all()
        )
        shuffle(queue_waitlist_players)
        for queue_waitlist_player in queue_waitlist_players:
            if is_in_game(queue_waitlist_player.player_id):
                session.delete(queue_waitlist_player)
                continue

            if channel and guild and isinstance(channel, TextChannel):
                # Bugfix - TODO: Add tests
                if queue_waitlist_player.queue_id:
                    await add_player_to_queue(
                        queue_waitlist_player.queue_id,
                        queue_waitlist_player.player_id,
                        channel,
                        guild,
                    )
                else:
                    # Legacy behavior
                    await add_player_to_queue(
                        queue_waitlist.queue_id,
                        queue_waitlist_player.player_id,
                        channel,
                        guild,
                    )

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
