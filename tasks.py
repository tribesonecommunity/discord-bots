# The discord bot doesn't like to execute things off the main thread. Instead we
# use queues to be able to execute discord actions from child threads.
# https://stackoverflow.com/a/67996748

from queue import SimpleQueue
from time import sleep

from discord.ext import tasks

from commands import send_message
from models import GameChannel, Session
from queues import (
    CREATE_VOICE_CHANNEL_QUEUE,
    SEND_MESSAGE_QUEUE,
    MessageQueueMessage,
    VoiceChannelQueueMessage,
)


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
