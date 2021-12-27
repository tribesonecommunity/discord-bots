from dataclasses import dataclass
from queue import SimpleQueue
from discord.channel import CategoryChannel, DMChannel, GroupChannel, TextChannel

from discord.colour import Colour
from discord.guild import Guild


SEND_MESSAGE_QUEUE = SimpleQueue()
CREATE_VOICE_CHANNEL_QUEUE = SimpleQueue()


@dataclass
class MessageQueueMessage:
    channel: (DMChannel | GroupChannel | TextChannel)
    content: str | None = None
    embed_description: str | None = None
    colour: Colour | None = None


@dataclass
class VoiceChannelQueueMessage:
    guild: Guild
    name: str
    game_id: str
    category: CategoryChannel
