# Module for Python queues used to handle concurrency - not to be confused with
# the game queues
from dataclasses import dataclass
from queue import SimpleQueue

from discord.channel import TextChannel
from discord.guild import Guild
from discord.message import Message

add_player_queue: SimpleQueue = SimpleQueue()
waitlist_messages: list[Message] = (
    []
)  # short-term solution to bulk delete queue_waitlist messages


@dataclass
class AddPlayerQueueMessage:
    """
    :should_print_status: Controls whether to print the status after adding
    players to queue. We want to print when someone manually adds, but when
    someone is buffered into it (via waitlist)
    """

    player_id: int
    player_name: str
    queue_ids: list[str]
    should_print_status: bool
    channel: TextChannel
    guild: Guild
