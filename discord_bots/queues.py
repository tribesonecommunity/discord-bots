from dataclasses import dataclass
from queue import SimpleQueue
from discord.channel import DMChannel, GroupChannel, TextChannel

from discord.guild import Guild


QUEUE_WAITLIST = SimpleQueue()


@dataclass
class QueueWaitlistQueueMessage:
    channel: (DMChannel | GroupChannel | TextChannel)
    guild: Guild
    finished_game_id: str
