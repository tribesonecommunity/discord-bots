from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
from pytest import fixture

from discord_bots.commands import vote_map
from discord_bots.models import (
    MapVote,
    Player,
    Queue,
    QueuePlayer,
    Session,
    SkipMapVote,
    VoteableMap,
)
from discord_bots.tasks import afk_timer_task

from .fixtures import TEST_CHANNEL, opsayo, setup_tests


def Bot():
    """Return a mock discord client"""
    return Mock(get_channel=lambda x: TEST_CHANNEL)


session = Session()

# Runs around each test
@fixture(autouse=True)
def run_around_tests():
    setup_tests()


@pytest.mark.asyncio
@patch("discord_bots.tasks.bot")
async def test_afk_timer_with_inactive_player_should_delete_player_from_queue(bot):
    player: Player = session.query(Player).filter(Player.id == opsayo.id).first()
    player.last_activity_at = datetime.now(timezone.utc) - timedelta(hours=1)
    queue = Queue("ltpug", 10)
    queue_player = QueuePlayer(queue.id, player.id, TEST_CHANNEL.id)
    session.add(queue)
    session.add(queue_player)
    session.commit()

    await afk_timer_task()

    queue_players = session.query(QueuePlayer).all()
    assert len(queue_players) == 0


@pytest.mark.asyncio
async def test_afk_timer_with_active_player_should_not_delete_player_from_queue():
    player: Player = session.query(Player).filter(Player.id == opsayo.id).first()
    player.last_activity_at = datetime.now(timezone.utc)
    queue = Queue("ltpug", 10)
    queue_player = QueuePlayer(queue.id, player.id, 0)
    session.add(queue)
    session.add(queue_player)
    session.commit()

    await afk_timer_task()

    queue_players = session.query(QueuePlayer).all()
    assert len(queue_players) == 1


@pytest.mark.asyncio
@patch("discord_bots.tasks.bot")
async def test_afk_timer_with_inactive_player_should_delete_player_votes(bot):
    session = Session()
    player: Player = session.query(Player).filter(Player.id == opsayo.id).first()
    player.last_activity_at = datetime.now(timezone.utc) - timedelta(hours=1)
    voteable_map = VoteableMap("dangerouscrossing", "dx")
    session.add(VoteableMap("dangerouscrossing", "dx"))
    session.add(MapVote(0, player.id, voteable_map.id))
    session.add(SkipMapVote(0, player.id))
    session.commit()

    await afk_timer_task()

    session = Session()
    assert len(session.query(MapVote).all()) == 0
    assert len(session.query(SkipMapVote).all()) == 0
