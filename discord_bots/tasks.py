# The discord bot doesn't like to execute things off the main thread. Instead we
# use queues to be able to execute discord actions from child threads.
# https://stackoverflow.com/a/67996748

from datetime import datetime, timedelta, timezone
from random import shuffle

from discord.channel import TextChannel
from discord.colour import Colour
from discord.ext import tasks
from discord.member import Member

from .bot import bot
from .commands import AFK_TIME_MINUTES, add_player_to_queue, is_in_game, send_message
from .models import (
    Player,
    QueuePlayer,
    QueueWaitlist,
    QueueWaitlistPlayer,
    Session,
)



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
        pass
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

            channel = bot.get_channel(queue_waitlist.channel_id)
            guild = bot.get_guild(queue_waitlist.guild_id)
            if channel and guild and isinstance(channel, TextChannel):
                await add_player_to_queue(
                    queue_waitlist.queue_id,
                    queue_waitlist_player.player_id,
                    channel,
                    guild,
                )

        session.query(QueueWaitlistPlayer).filter(
            QueueWaitlistPlayer.queue_waitlist_id == queue_waitlist.id
        ).delete()
        session.delete(queue_waitlist)
        session.commit()
