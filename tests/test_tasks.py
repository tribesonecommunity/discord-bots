from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from pytest import fixture
import pytest

from discord_bots.models import (
    InProgressGame,
    InProgressGamePlayer,
    Player,
    Queue,
    QueuePlayer,
    QueueWaitlistPlayer,
    Session,
)
from discord_bots.tasks import afk_timer_task

from .fixtures import TEST_CHANNEL, TEST_GUILD, izza, lyon, opsayo, stork


def Bot():
    """Return a mock discord client"""
    return Mock(get_channel=lambda x: TEST_CHANNEL)


session = Session()

# Runs around each test
@fixture(autouse=True)
def run_around_tests():
    session.query(QueueWaitlistPlayer).delete()
    session.query(QueuePlayer).delete()
    session.query(InProgressGamePlayer).delete()
    session.query(Queue).delete()
    session.query(InProgressGame).delete()
    session.query(Player).delete()
    session.add(Player(id=opsayo.id, name="opsayo", is_admin=True))
    TEST_GUILD.channels = {}
    TEST_GUILD._members = [opsayo, stork, izza, lyon]

    session.commit()


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
