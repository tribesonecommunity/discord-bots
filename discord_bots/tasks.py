# The discord bot doesn't like to execute things off the main thread. Instead we
# use queues to be able to execute discord actions from child threads.
# https://stackoverflow.com/a/67996748

from datetime import datetime, timedelta, timezone
from random import shuffle
from typing import List

from discord.channel import TextChannel
from discord.colour import Colour
from discord.ext import tasks
from discord.member import Member

from .bot import bot
from .commands import AFK_TIME_MINUTES, add_player_to_queue, is_in_game, send_message
from .models import (
    Player,
    QueuePlayer,
    QueueWaitlistPlayer,
    Session,
)
from .queues import (
    QUEUE_WAITLIST,
    QueueWaitlistQueueMessage,
)


@tasks.loop(seconds=60)
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
    """
    session = Session()
    while not QUEUE_WAITLIST.empty():
        message: QueueWaitlistQueueMessage = QUEUE_WAITLIST.get()

        queue_waitlist_players: List[QueueWaitlistPlayer]
        queue_waitlist_players = (
            session.query(QueueWaitlistPlayer)
            .filter(QueueWaitlistPlayer.finished_game_id == message.finished_game_id)
            .all()
        )
        shuffle(queue_waitlist_players)

        for queue_waitlist_player in queue_waitlist_players:
            if is_in_game(queue_waitlist_player.player_id):
                session.delete(queue_waitlist_player)
                continue

            await add_player_to_queue(
                queue_waitlist_player.queue_id,
                queue_waitlist_player.player_id,
                message.channel,
                message.guild,
            )

        session.query(QueueWaitlistPlayer).filter(
            QueueWaitlistPlayer.finished_game_id == message.finished_game_id
        ).delete()
        session.commit()
