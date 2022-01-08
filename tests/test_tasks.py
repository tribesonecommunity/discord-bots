from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
from discord.channel import TextChannel
from pytest import fixture

from discord_bots.commands import handle_message
from discord_bots.models import (
    FinishedGame,
    InProgressGame,
    MapVote,
    Player,
    Queue,
    QueuePlayer,
    QueueWaitlist,
    QueueWaitlistPlayer,
    Session,
    SkipMapVote,
    VoteableMap,
)
from discord_bots.tasks import afk_timer_task, queue_waitlist_task

from .fixtures import TEST_CHANNEL, TEST_GUILD, Channel, Guild, opsayo, setup_tests
from .test_commands import Message


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


# Test doesn't work yet, says database is locked?
@pytest.mark.asyncio
@pytest.mark.xfail
@patch("discord_bots.tasks.bot")
async def test_queue_waitlist_task_with_multiple_queues_should_add_to_first_queue_by_index(
    bot,
):
    bot.get_channel.return_value = Mock(spec=TextChannel, id=0)
    bot.get_guild.return_value = Mock(spec=Guild)
    session = Session()
    first_queue = Queue("first", 1)
    second_queue = Queue("second", 1)
    session.add(first_queue)
    session.add(second_queue)
    fg = FinishedGame(0, "", datetime.now(), True, "", "", "", datetime.now(), 0, 0)
    igp = InProgressGame(0, "", "", first_queue.id, 0)
    session.add(fg)
    session.add(igp)
    qw = QueueWaitlist(
        channel_id=0,
        finished_game_id=fg.id,
        guild_id=0,
        in_progress_game_id=igp.id,
        queue_id=first_queue.id,
        end_waitlist_at=datetime.now(),
    )
    session.add(qw)
    session.add(QueueWaitlistPlayer(second_queue.id, qw.id, opsayo.id))
    session.add(QueueWaitlistPlayer(first_queue.id, qw.id, opsayo.id))
    session.commit()

    await queue_waitlist_task()

    igp: InProgressGame = Session().query(InProgressGame).first()
    assert igp.queue_id == first_queue.id


@pytest.mark.asyncio
async def test_map_rotation_task_should_rotate_map():
    pass


@pytest.mark.asyncio
async def test_map_rotation_task_should_stop_on_first_map():
    pass
