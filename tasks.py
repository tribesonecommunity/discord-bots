# The discord bot doesn't like to execute things off the main thread. Instead we
# use queues to be able to execute discord actions from child threads.
# https://stackoverflow.com/a/67996748

from datetime import datetime, timedelta, timezone
from discord.channel import TextChannel
from discord.colour import Colour

from discord.ext import tasks

from bot import bot
from commands import AFK_TIME_MINUTES, send_message
from models import GameChannel, Player, Queue, QueuePlayer, Session
from queues import (
    CREATE_VOICE_CHANNEL_QUEUE,
    SEND_MESSAGE_QUEUE,
    MessageQueueMessage,
    VoiceChannelQueueMessage,
)


@tasks.loop(seconds=5)
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
                await send_message(
                    channel,
                    embed_description=f"{player.name} was removed from all queues for being inactive for {AFK_TIME_MINUTES} minutes",
                    colour=Colour.red(),
                )
            session.query(QueuePlayer).filter(
                QueuePlayer.player_id == player.id
            ).delete()
            session.commit()


@tasks.loop(seconds=0.5)
async def send_message_task():
    while not SEND_MESSAGE_QUEUE.empty():
        message: MessageQueueMessage = SEND_MESSAGE_QUEUE.get()
        await send_message(
            message.channel, message.content, message.embed_description, message.colour
        )


@tasks.loop(seconds=0.5)
async def create_voice_channel_task():
    while not CREATE_VOICE_CHANNEL_QUEUE.empty():
        message: VoiceChannelQueueMessage = CREATE_VOICE_CHANNEL_QUEUE.get()
        channel = await message.guild.create_voice_channel(
            message.name,
            category=message.category,
        )
        session = Session()
        session.add(GameChannel(game_id=message.game_id, channel_id=channel.id))
        session.commit()
