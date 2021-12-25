from __future__ import annotations

from dataclasses import dataclass, field
from math import floor
from random import random
from time import sleep
from typing import Dict, List
from unittest.mock import Mock
from uuid import uuid4

from discord import Message
from pytest import fixture
import pytest

from commands import (
    COMMAND_PREFIX,
    OPSAYO_MEMBER_ID,
    TRIBES_VOICE_CATEGORY_CHANNEL_ID,
    is_in_game,
    handle_message,
)
from models import Game, GamePlayer, Player, Queue, QueuePlayer, Session


# Mock discord models so we can invoke tests
@dataclass
class Category:
    id: str = field(default_factory=lambda: str(uuid4()))


TRIBES_VOICE_CATEGORY = Category(TRIBES_VOICE_CATEGORY_CHANNEL_ID)


@dataclass
class Role:
    name: str
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass
class Guild:
    """
    Fake utility class for
    https://discordpy.readthedocs.io/en/stable/api.html#guild
    """

    _members: List[Member] = field(default_factory=list)
    categories: List[Category] = field(default_factory=lambda: [TRIBES_VOICE_CATEGORY])
    channels: Dict[str, Channel] = field(default_factory=dict)
    roles: List[Role] = field(default_factory=lambda: [Role("LTpug")])

    async def create_voice_channel(self, name, category: Category = None) -> Channel:
        channel = Channel()
        self.channels[channel.id] = channel
        return channel

    def get_channel(self, channel_id: str) -> Channel:
        return self.channels[channel_id]

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

    @property
    def guild(self) -> Guild:
        """
        Defined as a property rather than attribute to avoid circular reference
        """
        return TEST_GUILD


@dataclass
class Channel:
    id: str = field(default_factory=lambda: str(uuid4()))

    async def send(self, content, embed):
        if embed:
            print(f"[Channel.send] content={content}, embed={embed.description}")
        else:
            print(f"[Channel.send] content={content}")

    async def delete(self):
        pass


session = Session()
opsayo = Member("opsayo", id=OPSAYO_MEMBER_ID)
stork = Member("stork")
izza = Member("izza")
lyon = Member("lyon")


# Runs around each test
@fixture(autouse=True)
def run_around_tests():
    session.query(QueuePlayer).delete()
    session.query(GamePlayer).delete()
    session.query(Queue).delete()
    session.query(Game).delete()
    session.query(Player).delete()
    session.add(Player(id=OPSAYO_MEMBER_ID, name="opsayo", is_admin=True))
    TEST_GUILD.channels = {}
    TEST_GUILD._members = [opsayo, stork, izza, lyon]

    session.commit()


def mentions(content: str) -> List[Member]:
    """
    """
    mentions = []
    for chunk in content.split(" "):
        if not chunk.startswith("@"):
            continue
        mentions.append(TEST_GUILD.get_member_named(chunk[1:]))
    return mentions


def Message(author: Member, content: str):
    """
    Return a mock object that makes Pylance happy
    """
    # https://discordpy.readthedocs.io/en/stable/api.html#discord.Message.mentions
    # When a real message has a mention, the content string looks like:
    # '!command <@!370328859440054286>'. For simplicity, we assume it looks like
    # '!command @username'
    mentions = []
    for chunk in content.split(" "):
        if not chunk.startswith("@"):
            continue
        mentions.append(TEST_GUILD.get_member_named(chunk[1:]))

    return Mock(
        author=author,
        content=content,
        channel=Channel(),
        guild=TEST_GUILD,
        mentions=mentions,
    )


@pytest.mark.asyncio
async def test_is_in_game_with_player_in_game_should_return_true():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 2"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))
    await handle_message(Message(stork, f"{COMMAND_PREFIX}add"))

    assert is_in_game(opsayo.id)


@pytest.mark.asyncio
def test_is_in_game_with_player_not_in_game_should_return_false():
    assert not is_in_game(opsayo.id)


@pytest.mark.asyncio
async def test_is_in_game_with_player_in_finished_game_should_return_false():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 2"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))
    await handle_message(Message(stork, f"{COMMAND_PREFIX}add"))
    await handle_message(Message(stork, f"{COMMAND_PREFIX}finishgame loss"))

    assert not is_in_game(opsayo.id)


@pytest.mark.asyncio
@pytest.mark.skip
async def test_create_queue_with_odd_size_then_does_not_create_queue():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTgold 5"))

    queues = list(session.query(Queue))
    assert len(queues) == 0


@pytest.mark.asyncio
async def test_create_queue_should_create_queue():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 10"))

    queues = list(session.query(Queue))
    assert len(queues) == 1


@pytest.mark.asyncio
async def test_remove_queue_should_remove_queue():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 10"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}removequeue LTpug"))

    queues = list(session.query(Queue))
    assert len(queues) == 0


@pytest.mark.asyncio
async def test_remove_queue_with_nonexistent_queue_should_not_throw_exception():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}removequeue LTpug"))

    assert True


@pytest.mark.asyncio
async def test_add_should_add_player_to_all_queues():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 10"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTunrated 10"))

    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))

    for queue_name in ("LTpug", "LTunrated"):
        queue = list(session.query(Queue).filter(Queue.name == queue_name))[0]
        queue_players = [
            qp
            for qp in session.query(QueuePlayer).filter(
                QueuePlayer.player_id == opsayo.id, QueuePlayer.queue_id == queue.id
            )
        ]
        assert len(queue_players) == 1


@pytest.mark.asyncio
async def test_add_with_multiple_calls_should_not_throw_exception():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 10"))

    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))

    assert True


@pytest.mark.asyncio
async def test_add_with_queue_named_should_add_player_to_named_queue():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 10"))

    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add LTpug"))

    lt_pug_queue = list(session.query(Queue).filter(Queue.name == "LTpug"))[0]
    lt_pug_queue_players = [
        qp
        for qp in session.query(QueuePlayer).filter(
            QueuePlayer.player_id == opsayo.id, QueuePlayer.queue_id == lt_pug_queue.id
        )
    ]
    assert len(lt_pug_queue_players) == 1


@pytest.mark.asyncio
async def test_add_with_queue_named_should_not_add_player_to_unnamed_queue():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 10"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTunrated 10"))

    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add LTpug"))

    lt_unrated_queue = list(session.query(Queue).filter(Queue.name == "LTunrated"))[0]
    lt_unrated_queue_players = list(
        session.query(QueuePlayer).filter(
            QueuePlayer.player_id == opsayo.id,
            QueuePlayer.queue_id == lt_unrated_queue.id,
        )
    )
    assert len(lt_unrated_queue_players) == 0


@pytest.mark.asyncio
async def test_del_should_remove_player_from_all_queues():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 10"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTunrated 10"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))

    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}del"))

    for queue_name in ("LTpug", "LTunrated"):
        queue = [q for q in session.query(Queue).filter(Queue.name == queue_name)][0]
        queue_players = [
            qp
            for qp in session.query(QueuePlayer).filter(
                QueuePlayer.player_id == opsayo.id, QueuePlayer.queue_id == queue.id
            )
        ]
        assert len(queue_players) == 0


@pytest.mark.asyncio
async def test_del_with_queue_named_should_del_player_from_named_queue():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 10"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add LTpug"))

    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}del LTpug"))

    lt_pug_queue = [q for q in session.query(Queue).filter(Queue.name == "LTpug")][0]
    lt_pug_queue_players = [
        qp
        for qp in session.query(QueuePlayer).filter(
            QueuePlayer.player_id == opsayo.id, QueuePlayer.queue_id == lt_pug_queue.id
        )
    ]
    assert len(lt_pug_queue_players) == 0


@pytest.mark.asyncio
async def test_del_with_queue_named_should_not_del_add_player_from_unnamed_queue():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 4"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTunrated 10"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}del LTpug"))

    lt_unrated_queue = [
        q for q in session.query(Queue).filter(Queue.name == "LTunrated")
    ][0]
    lt_unrated_queue_players = [
        qp
        for qp in session.query(QueuePlayer).filter(
            QueuePlayer.player_id == opsayo.id,
            QueuePlayer.queue_id == lt_unrated_queue.id,
        )
    ]
    assert len(lt_unrated_queue_players) == 1


@pytest.mark.asyncio
async def test_add_with_queue_at_size_should_create_game_and_clear_queue():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 4"))

    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))
    await handle_message(Message(lyon, f"{COMMAND_PREFIX}add"))
    await handle_message(Message(stork, f"{COMMAND_PREFIX}add"))
    await handle_message(Message(izza, f"{COMMAND_PREFIX}add"))

    queue = [q for q in session.query(Queue).filter(Queue.name == "LTpug")][0]
    queue_players = [
        qp
        for qp in session.query(QueuePlayer).filter(
            QueuePlayer.queue_id == queue.id,
        )
    ]
    assert len(queue_players) == 0

    games = [g for g in session.query(Game).filter(Game.queue_id == queue.id)]
    assert len(games) == 1

    game_players = [
        gp for gp in session.query(GamePlayer).filter(GamePlayer.game_id == games[0].id)
    ]
    assert len(game_players) == 4


@pytest.mark.asyncio
async def test_add_with_player_in_game_should_not_add_to_queue():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 2"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))
    await handle_message(Message(lyon, f"{COMMAND_PREFIX}add"))

    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))

    queue = [q for q in session.query(Queue).filter(Queue.name == "LTpug")][0]
    queue_players = [
        qp
        for qp in session.query(QueuePlayer).filter(
            QueuePlayer.queue_id == queue.id,
        )
    ]
    assert len(queue_players) == 0


@pytest.mark.asyncio
async def test_status():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}status"))


@pytest.mark.asyncio
async def test_finish_game_with_win_should_record_win_for_reporting_team():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 2"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))
    await handle_message(Message(lyon, f"{COMMAND_PREFIX}add"))

    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}finishgame win"))

    game_player = [
        gp for gp in session.query(GamePlayer).filter(GamePlayer.player_id == opsayo.id)
    ][0]
    game = [g for g in session.query(Game)][0]
    assert game.winning_team == game_player.team


@pytest.mark.asyncio
async def test_finish_game_with_loss_should_record_win_for_other_team():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 2"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))
    await handle_message(Message(lyon, f"{COMMAND_PREFIX}add"))

    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}finishgame loss"))

    game_player = [
        gp for gp in session.query(GamePlayer).filter(GamePlayer.player_id == opsayo.id)
    ][0]
    game = [g for g in session.query(Game)][0]
    assert game.winning_team == (game_player.team + 1) % 2


@pytest.mark.asyncio
async def test_finish_game_with_tie_should_record_tie():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 2"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))
    await handle_message(Message(lyon, f"{COMMAND_PREFIX}add"))

    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}finishgame tie"))

    game = list(session.query(Game))[0]
    assert game.winning_team == -1


@pytest.mark.asyncio
async def test_finish_game_with_player_not_in_game_should_not_finish_game():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 2"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))
    await handle_message(Message(lyon, f"{COMMAND_PREFIX}add"))

    await handle_message(Message(stork, f"{COMMAND_PREFIX}finishgame loss"))

    game = [g for g in session.query(Game)][0]
    assert game.winning_team is None


@pytest.mark.asyncio
async def test_add_with_player_after_finish_game_should_be_added_to_queue():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}createqueue LTpug 2"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))
    await handle_message(Message(lyon, f"{COMMAND_PREFIX}add"))
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}finishgame win"))

    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add"))

    queue_players = [qp for qp in session.query(QueuePlayer)]
    assert len(queue_players) == 1


@pytest.mark.asyncio
async def test_add_admin_should_add_player_to_admins():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}addadmin @lyon"))

    admins = list(session.query(Player).filter(Player.is_admin == True))
    assert len(admins) == 2


@pytest.mark.asyncio
async def test_add_admin_with_non_admin_should_not_add_player_to_admins():
    await handle_message(Message(izza, f"{COMMAND_PREFIX}addadmin @lyon"))

    admins = list(session.query(Player).filter(Player.is_admin == True))
    assert len(admins) == 1


@pytest.mark.asyncio
async def test_remove_admin_should_remove_player_from_admins():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}addadmin @lyon"))

    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}removeadmin @lyon"))

    admins = list(session.query(Player).filter(Player.is_admin == True))
    assert len(admins) == 1


@pytest.mark.asyncio
async def test_remove_admin_with_self_should_not_remove_player_from_admins():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}removeadmin @opsayo"))

    admins = list(session.query(Player).filter(Player.is_admin == True))
    assert len(admins) == 1


@pytest.mark.asyncio
async def test_remove_admin_with_non_admin_should_not_remove_player_from_admins():
    await handle_message(Message(opsayo, f"{COMMAND_PREFIX}addadmin @lyon"))

    await handle_message(Message(izza, f"{COMMAND_PREFIX}removeadmin @lyon"))

    admins = list(session.query(Player).filter(Player.is_admin == True))
    assert len(admins) == 2


# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}ban lyon"))
# assert len(BANNED_PLAYERS) == 1
# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}ban lyon"))
# assert len(BANNED_PLAYERS) == 1
# await handle_message(Message(izza, f"{COMMAND_PREFIX}ban opsayo"))
# assert len(BANNED_PLAYERS) == 1
# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}listbans"))

# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}unban lyon"))
# assert len(BANNED_PLAYERS) == 0
# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}unban lyon"))
# await handle_message(Message(izza, f"{COMMAND_PREFIX}unban opsayo"))
# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}listbans"))


# await handle_message(Message(stork, f"{COMMAND_PREFIX}status"))
# await handle_message(Message(stork, f"{COMMAND_PREFIX}sub"))
# await handle_message(Message(izza, f"{COMMAND_PREFIX}sub lyon"))
# await handle_message(Message(stork, f"{COMMAND_PREFIX}sub opsayo"))
# await handle_message(Message(stork, f"{COMMAND_PREFIX}sub izza"))
# await handle_message(Message(stork, f"{COMMAND_PREFIX}status"))

# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}cancelgame"))
# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}status"))
# assert len(GAMES["LTpug"]) == 1
# await handle_message(Message(lyon, f"{COMMAND_PREFIX}cancelgame"))
# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}status"))
# assert len(GAMES["LTpug"]) == 0
# await handle_message(Message(lyon, f"{COMMAND_PREFIX}add LTpug"))

# # Re-add timer triggers here
# await handle_message(Message(stork, f"{COMMAND_PREFIX}add LTpug"))
# assert len(GAMES["LTpug"]) == 0
# sleep(RE_ADD_DELAY)
# await handle_message(Message(stork, f"{COMMAND_PREFIX}add LTpug"))
# assert len(GAMES["LTpug"]) == 1

# await handle_message(Message(stork, f"{COMMAND_PREFIX}cancelgame " + GAMES["LTpug"][0].id))
# assert len(GAMES["LTpug"]) == 1
# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}cancelgame " + GAMES["LTpug"][0].id))
# assert len(GAMES["LTpug"]) == 0
# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}status"))

# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}setadddelay"))
# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}setadddelay 1"))
# sleep(1)

# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add LTpug"))
# await handle_message(Message(stork, f"{COMMAND_PREFIX}add LTpug"))
# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}finishgame win"))
# await handle_message(Message(opsayo, f"{COMMAND_PREFIX}add LTpug"))
# assert len(QUEUES["LTpug"].players) == 0
