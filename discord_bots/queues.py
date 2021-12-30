from dataclasses import dataclass
from datetime import datetime
from queue import SimpleQueue
from discord.channel import CategoryChannel, DMChannel, GroupChannel, TextChannel

from discord.colour import Colour
from discord.guild import Guild
from sqlalchemy.sql.sqltypes import DateTime


CREATE_VOICE_CHANNEL = SimpleQueue()
QUEUE_WAITLIST = SimpleQueue()
SEND_MESSAGE = SimpleQueue()


@dataclass
class SendMessageQueueMessage:
    channel: (DMChannel | GroupChannel | TextChannel)
    content: str | None = None
    embed_description: str | None = None
    colour: Colour | None = None


@dataclass
class CreateVoiceChannelQueueMessage:
    guild: Guild
    name: str
    in_progress_game_id: str
    category: CategoryChannel


@dataclass
class QueueWaitlistQueueMessage:
    channel: (DMChannel | GroupChannel | TextChannel)
    guild: Guild
    finished_game_id: str
