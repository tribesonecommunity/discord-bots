# this file contains fixtures for tests

# this import allows classes to reference each other in fields
from __future__ import annotations

from dataclasses import dataclass, field
from math import floor
from random import random
from uuid import uuid4

from discord_bots.commands import TRIBES_VOICE_CATEGORY_CHANNEL_ID
from discord_bots.models import (
    AdminRole,
    Base,
    CurrentMap,
    FinishedGame,
    FinishedGamePlayer,
    InProgressGame,
    InProgressGamePlayer,
    MapVote,
    Player,
    PlayerDecay,
    Queue,
    QueuePlayer,
    QueueRole,
    QueueWaitlistPlayer,
    RotationMap,
    Session,
    SkipMapVote,
    VoteableMap,
    engine,
)


# Mock discord models so we can invoke tests
@dataclass
class Category:
    id: int


TRIBES_VOICE_CATEGORY = Category(TRIBES_VOICE_CATEGORY_CHANNEL_ID)


@dataclass
class Role:
    name: str
    id: str = field(default_factory=lambda: str(uuid4()))


ROLE_ADMIN = Role("Admin")
ROLE_LT_PUG = Role("LTpug")
ROLE_LT_GOLD = Role("LTgold")


@dataclass
class Channel:
    id: int = field(default_factory=lambda: floor(random() * 2 ** 32))

    async def send(self, content, embed):
        if embed:
            print(f"[Channel.send] content={content}, embed={embed.description}")
        else:
            print(f"[Channel.send] content={content}")

    async def delete(self):
        pass


TEST_CHANNEL = Channel()


@dataclass
class Guild:
    """
    Fake utility class for
    https://discordpy.readthedocs.io/en/stable/api.html#guild
    """

    _members: list[Member] = field(default_factory=list)
    id: int = field(default_factory=lambda: floor(random() * 2 ** 32))
    categories: list[Category] = field(default_factory=lambda: [TRIBES_VOICE_CATEGORY])
    channels: dict[int, Channel] = field(default_factory=dict)
    roles: list[Role] = field(
        default_factory=lambda: [ROLE_ADMIN, ROLE_LT_GOLD, ROLE_LT_PUG]
    )

    # TODO: Use VoiceChannel instead of Channel
    async def create_voice_channel(self, name, category: Category = None) -> Channel:
        channel = Channel()
        self.channels[channel.id] = channel
        return channel

    def get_channel(self, channel_id: int) -> Channel:
        return self.channels[channel_id]

    def get_member(self, member_id: int) -> Member | None:
        for member in self._members:
            if member.id == member_id:
                return member

        return None

    def get_member_named(self, member_name: str) -> Member:
        for member in self._members:
            if member.name == member_name:
                return member
        self._members.append(Member(name=member_name))

        return self._members[-1]


TEST_GUILD = Guild()


@dataclass
class Member:
    name: str
    id: int = field(default_factory=lambda: floor(random() * 2 ** 32))
    roles: list[Role] = field(default_factory=list)

    @property
    def guild(self) -> Guild:
        """
        Defined as a property rather than attribute to avoid circular reference
        """
        return TEST_GUILD


opsayo = Member("opsayo")
stork = Member("stork")
izza = Member("izza")
lyon = Member("lyon")


def setup_tests():
    Base.metadata.create_all(engine)
    session = Session()
    # TODO: Is there a single command to just do this
    session.query(SkipMapVote).delete()
    session.query(MapVote).delete()
    session.query(RotationMap).delete()
    session.query(VoteableMap).delete()
    session.query(CurrentMap).delete()
    session.query(AdminRole).delete()
    session.query(QueueWaitlistPlayer).delete()
    session.query(QueuePlayer).delete()
    session.query(InProgressGamePlayer).delete()
    session.query(InProgressGame).delete()
    session.query(FinishedGamePlayer).delete()
    session.query(FinishedGame).delete()
    session.query(QueueRole).delete()
    session.query(Queue).delete()
    session.query(PlayerDecay).delete()
    session.query(Player).delete()
    session.add(Player(id=opsayo.id, name="opsayo", is_admin=True))
    session.add(Player(id=stork.id, name="stork"))
    TEST_GUILD.channels = {}
    TEST_GUILD._members = [opsayo, stork, izza, lyon]

    session.commit()
