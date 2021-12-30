from typing import List
from unittest.mock import Mock

from discord import Message
from pytest import approx, fixture
from trueskill import Rating
import pytest

from commands import (
    COMMAND_PREFIX,
    OPSAYO_MEMBER_ID,
    is_in_game,
    handle_message,
)
from fixtures import TEST_GUILD, Channel, Member, izza, lyon, opsayo, stork
from models import (
    FinishedGame,
    FinishedGamePlayer,
    InProgressGame,
    InProgressGamePlayer,
    Player,
    Queue,
    QueuePlayer,
    QueueWaitlistPlayer,
    Session,
)


session = Session()


# Runs around each test
@fixture(autouse=True)
def run_around_tests():
    session.query(QueueWaitlistPlayer).delete()
    session.query(QueuePlayer).delete()
    session.query(InProgressGamePlayer).delete()
    session.query(InProgressGame).delete()
    session.query(FinishedGamePlayer).delete()
    session.query(FinishedGame).delete()
    session.query(Queue).delete()
    session.query(Player).delete()
    session.add(Player(id=OPSAYO_MEMBER_ID, name="opsayo", is_admin=True))
    TEST_GUILD.channels = {}
    TEST_GUILD._members = [opsayo, stork, izza, lyon]

    session.commit()


def mentions(content: str) -> List[Member]:
    """ """
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
        content=f"{COMMAND_PREFIX}{content}",
        channel=Channel(),
        guild=TEST_GUILD,
        mentions=mentions,
    )


@pytest.mark.asyncio
async def test_is_in_game_with_player_in_game_should_return_true():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(stork, "add"))

    assert is_in_game(opsayo.id)


@pytest.mark.asyncio
def test_is_in_game_with_player_not_in_game_should_return_false():
    assert not is_in_game(opsayo.id)


@pytest.mark.asyncio
async def test_is_in_game_with_player_in_finished_game_should_return_false():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(stork, "add"))
    await handle_message(Message(stork, "finishgame loss"))

    assert not is_in_game(opsayo.id)


@pytest.mark.asyncio
@pytest.mark.skip
async def test_create_queue_with_odd_size_then_does_not_create_queue():
    await handle_message(Message(opsayo, "createqueue LTgold 5"))

    queues = session.query(Queue).all()
    assert len(queues) == 0


@pytest.mark.asyncio
async def test_create_queue_should_create_queue():
    await handle_message(Message(opsayo, "createqueue LTpug 10"))

    queues = session.query(Queue).all()
    assert len(queues) == 1


@pytest.mark.asyncio
async def test_remove_queue_should_remove_queue():
    await handle_message(Message(opsayo, "createqueue LTpug 10"))
    await handle_message(Message(opsayo, "removequeue LTpug"))

    queues = session.query(Queue).all()
    assert len(queues) == 0


@pytest.mark.asyncio
async def test_remove_queue_with_a_game_should_not_remove_queue():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(lyon, "add"))
    await handle_message(Message(opsayo, "add"))

    await handle_message(Message(opsayo, "removequeue LTpug"))

    queues = session.query(Queue).all()
    assert len(queues) == 1


@pytest.mark.asyncio
async def test_remove_queue_with_nonexistent_queue_should_not_throw_exception():
    await handle_message(Message(opsayo, "removequeue LTpug"))

    assert True


@pytest.mark.asyncio
async def test_add_should_add_player_to_all_queues():
    await handle_message(Message(opsayo, "createqueue LTpug 10"))
    await handle_message(Message(opsayo, "createqueue LTunrated 10"))

    await handle_message(Message(opsayo, "add"))

    for queue_name in ("LTpug", "LTunrated"):
        queue = session.query(Queue).filter(Queue.name == queue_name).first()
        queue_players = (
            session.query(QueuePlayer)
            .filter(
                QueuePlayer.player_id == opsayo.id, QueuePlayer.queue_id == queue.id
            )
            .all()
        )
        assert len(queue_players) == 1


@pytest.mark.asyncio
async def test_add_with_multiple_calls_should_not_throw_exception():
    await handle_message(Message(opsayo, "createqueue LTpug 10"))

    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(opsayo, "add"))

    assert True


@pytest.mark.asyncio
async def test_add_with_queue_named_should_add_player_to_named_queue():
    await handle_message(Message(opsayo, "createqueue LTpug 10"))

    await handle_message(Message(opsayo, "add LTpug"))

    lt_pug_queue = session.query(Queue).filter(Queue.name == "LTpug").first()
    lt_pug_queue_players = (
        session.query(QueuePlayer)
        .filter(
            QueuePlayer.player_id == opsayo.id, QueuePlayer.queue_id == lt_pug_queue.id
        )
        .all()
    )
    assert len(lt_pug_queue_players) == 1


@pytest.mark.asyncio
async def test_add_with_queue_named_should_not_add_player_to_unnamed_queue():
    await handle_message(Message(opsayo, "createqueue LTpug 10"))
    await handle_message(Message(opsayo, "createqueue LTunrated 10"))

    await handle_message(Message(opsayo, "add LTpug"))

    lt_unrated_queue = session.query(Queue).filter(Queue.name == "LTunrated").first()
    lt_unrated_queue_players = (
        session.query(QueuePlayer)
        .filter(
            QueuePlayer.player_id == opsayo.id,
            QueuePlayer.queue_id == lt_unrated_queue.id,
        )
        .all()
    )
    assert len(lt_unrated_queue_players) == 0


@pytest.mark.asyncio
async def test_del_should_remove_player_from_all_queues():
    await handle_message(Message(opsayo, "createqueue LTpug 10"))
    await handle_message(Message(opsayo, "createqueue LTunrated 10"))
    await handle_message(Message(opsayo, "add"))

    await handle_message(Message(opsayo, "del"))

    for queue_name in ("LTpug", "LTunrated"):
        queue = session.query(Queue).filter(Queue.name == queue_name).first()
        queue_players = (
            session.query(QueuePlayer)
            .filter(
                QueuePlayer.player_id == opsayo.id, QueuePlayer.queue_id == queue.id
            )
            .all()
        )
        assert len(queue_players) == 0


@pytest.mark.asyncio
async def test_del_with_queue_named_should_del_player_from_named_queue():
    await handle_message(Message(opsayo, "createqueue LTpug 10"))
    await handle_message(Message(opsayo, "add LTpug"))

    await handle_message(Message(opsayo, "del LTpug"))

    lt_pug_queue = session.query(Queue).filter(Queue.name == "LTpug").first()
    lt_pug_queue_players = (
        session.query(QueuePlayer)
        .filter(
            QueuePlayer.player_id == opsayo.id, QueuePlayer.queue_id == lt_pug_queue.id
        )
        .all()
    )
    assert len(lt_pug_queue_players) == 0


@pytest.mark.asyncio
async def test_del_with_queue_named_should_not_del_add_player_from_unnamed_queue():
    await handle_message(Message(opsayo, "createqueue LTpug 4"))
    await handle_message(Message(opsayo, "createqueue LTunrated 10"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(opsayo, "del LTpug"))

    lt_unrated_queue = session.query(Queue).filter(Queue.name == "LTunrated").first()
    lt_unrated_queue_players = (
        session.query(QueuePlayer)
        .filter(
            QueuePlayer.player_id == opsayo.id,
            QueuePlayer.queue_id == lt_unrated_queue.id,
        )
        .all()
    )
    assert len(lt_unrated_queue_players) == 1


@pytest.mark.asyncio
async def test_add_with_queue_at_size_should_create_game_and_clear_queue():
    await handle_message(Message(opsayo, "createqueue LTpug 4"))

    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))
    await handle_message(Message(stork, "add"))
    await handle_message(Message(izza, "add"))

    queue = session.query(Queue).filter(Queue.name == "LTpug").first()
    queue_players = (
        session.query(QueuePlayer)
        .filter(
            QueuePlayer.queue_id == queue.id,
        )
        .all()
    )
    assert len(queue_players) == 0

    game = (
        session.query(InProgressGame)
        .filter(InProgressGame.queue_id == queue.id)
        .first()
    )
    assert game is not None

    game_players = (
        session.query(InProgressGamePlayer)
        .filter(InProgressGamePlayer.in_progress_game_id == game.id)
        .all()
    )
    assert len(game_players) == 4


@pytest.mark.asyncio
async def test_add_with_player_in_game_should_not_add_to_queue():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(opsayo, "add"))

    queue = session.query(Queue).filter(Queue.name == "LTpug").first()
    queue_players = (
        session.query(QueuePlayer)
        .filter(
            QueuePlayer.queue_id == queue.id,
        )
        .all()
    )
    assert len(queue_players) == 0


@pytest.mark.asyncio
async def test_status():
    await handle_message(Message(opsayo, "status"))


@pytest.mark.asyncio
async def test_finish_game_should_record_finish_at_timestamp():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(opsayo, "finishgame win"))

    assert len(session.query(InProgressGame).all()) == 0
    assert len(session.query(FinishedGame).all()) == 1


@pytest.mark.asyncio
async def test_finish_game_with_win_should_record_win_for_reporting_team():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(opsayo, "finishgame win"))

    finished_game = session.query(FinishedGame).first()
    finished_game_player = (
        session.query(FinishedGamePlayer)
        .filter(FinishedGamePlayer.player_id == opsayo.id)
        .first()
    )
    assert finished_game.winning_team == finished_game_player.team


@pytest.mark.asyncio
async def test_finish_game_with_loss_should_record_win_for_other_team():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(opsayo, "finishgame loss"))

    finished_game = session.query(FinishedGame).first()
    finished_game_player = (
        session.query(FinishedGamePlayer)
        .filter(FinishedGamePlayer.player_id == opsayo.id)
        .first()
    )
    assert finished_game.winning_team == (finished_game_player.team + 1) % 2


@pytest.mark.asyncio
async def test_finish_game_with_tie_should_record_tie():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(opsayo, "finishgame tie"))

    finished_game = session.query(FinishedGame).first()
    finished_game_player = (
        session.query(FinishedGamePlayer)
        .filter(FinishedGamePlayer.player_id == opsayo.id)
        .first()
    )
    assert finished_game.winning_team == -1


@pytest.mark.asyncio
async def test_finish_game_with_win_should_increase_trueskill_for_reporting_team():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(opsayo, "finishgame win"))

    assert (
        session.query(Player).filter(Player.id == opsayo.id).first().trueskill_mu
        > Rating().mu
    )
    assert (
        session.query(Player).filter(Player.id == lyon.id).first().trueskill_mu
        < Rating().mu
    )


@pytest.mark.asyncio
async def test_finish_game_with_loss_should_increase_trueskill_for_other_team():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(opsayo, "finishgame loss"))

    assert (
        session.query(Player).filter(Player.id == opsayo.id).first().trueskill_mu
        < Rating().mu
    )
    assert (
        session.query(Player).filter(Player.id == lyon.id).first().trueskill_mu
        > Rating().mu
    )


@pytest.mark.asyncio
async def test_finish_game_with_tie_and_equal_trueskill_should_not_modify_trueskill():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(opsayo, "finishgame tie"))

    assert session.query(Player).filter(
        Player.id == opsayo.id
    ).first().trueskill_mu == approx(Rating().mu)
    assert session.query(Player).filter(
        Player.id == lyon.id
    ).first().trueskill_mu == approx(Rating().mu)


@pytest.mark.asyncio
async def test_finish_game_with_player_not_in_game_should_not_finish_game():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(stork, "finishgame loss"))

    assert session.query(InProgressGame).first() is not None
    assert session.query(FinishedGame).first() is None


@pytest.mark.asyncio
async def test_add_with_player_right_after_finish_game_should_not_be_added_to_queue():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))
    await handle_message(Message(opsayo, "finishgame win"))

    await handle_message(Message(opsayo, "add"))

    assert len(session.query(QueuePlayer).all()) == 0


@pytest.mark.asyncio
async def test_add_admin_should_add_player_to_admins():
    await handle_message(Message(opsayo, "addadmin @lyon"))

    admins = session.query(Player).filter(Player.is_admin == True).all()
    assert len(admins) == 2


@pytest.mark.asyncio
@pytest.mark.skip
async def test_add_admin_with_non_admin_should_not_add_player_to_admins():
    await handle_message(Message(izza, "addadmin @lyon"))

    admins = session.query(Player).filter(Player.is_admin == True).all()
    assert len(admins) == 1


@pytest.mark.asyncio
async def test_remove_admin_should_remove_player_from_admins():
    await handle_message(Message(opsayo, "addadmin @lyon"))

    await handle_message(Message(opsayo, "removeadmin @lyon"))

    admins = session.query(Player).filter(Player.is_admin == True).all()
    assert len(admins) == 1


@pytest.mark.asyncio
@pytest.mark.skip
async def test_remove_admin_with_self_should_not_remove_player_from_admins():
    await handle_message(Message(opsayo, "removeadmin @opsayo"))

    admins = session.query(Player).filter(Player.is_admin == True).all()
    assert len(admins) == 1


@pytest.mark.asyncio
async def test_remove_admin_with_non_admin_should_not_remove_player_from_admins():
    await handle_message(Message(opsayo, "addadmin @lyon"))

    await handle_message(Message(izza, "removeadmin @lyon"))

    admins = session.query(Player).filter(Player.is_admin == True).all()
    assert len(admins) == 2


@pytest.mark.asyncio
async def test_add_with_player_just_finished_should_not_add_to_queue():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))
    await handle_message(Message(opsayo, "finishgame win"))

    await handle_message(Message(opsayo, "add"))

    queue = session.query(Queue).filter(Queue.name == "LTpug").first()
    queue_players = (
        session.query(QueuePlayer)
        .filter(QueuePlayer.player_id == opsayo.id, QueuePlayer.queue_id == queue.id)
        .all()
    )
    assert len(queue_players) == 0


@pytest.mark.asyncio
async def test_add_with_player_just_finished_should_add_to_queue_waitlist():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))
    await handle_message(Message(opsayo, "finishgame win"))

    await handle_message(Message(opsayo, "add"))

    queue_waitlist = session.query(QueueWaitlistPlayer).all()
    assert len(queue_waitlist) == 1


@pytest.mark.asyncio
async def test_ban_should_add_player_to_bans():
    await handle_message(Message(opsayo, "ban @lyon"))

    banned_players = session.query(Player).filter(Player.is_banned == True).all()
    assert len(banned_players) == 1


@pytest.mark.asyncio
async def test_unban_should_remove_player_from_bans():
    await handle_message(Message(opsayo, "ban @lyon"))

    await handle_message(Message(opsayo, "unban @lyon"))

    banned_players = session.query(Player).filter(Player.is_banned == True).all()
    assert len(banned_players) == 0


@pytest.mark.asyncio
async def test_sub_with_subber_in_game_and_subbee_not_in_game_should_substitute_players():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(opsayo, "sub @stork"))

    game_player_ids = set([gp.player_id for gp in session.query(InProgressGamePlayer)])
    assert stork.id in game_player_ids
    assert opsayo.id not in game_player_ids


@pytest.mark.asyncio
async def test_sub_with_subber_not_in_game_and_subbee_in_game_should_substitute_players():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(stork, "sub @opsayo"))

    game_player_ids = set([gp.player_id for gp in session.query(InProgressGamePlayer)])
    assert stork.id in game_player_ids
    assert opsayo.id not in game_player_ids


@pytest.mark.asyncio
async def test_sub_with_subber_in_game_and_subbee_in_game_should_not_substitute_players():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(opsayo, "sub @lyon"))

    game_player_ids = set([gp.player_id for gp in session.query(InProgressGamePlayer)])
    assert lyon.id in game_player_ids
    assert opsayo.id in game_player_ids


@pytest.mark.asyncio
async def test_sub_with_subber_not_in_game_and_subbee_not_in_game_should_substitute_players():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(izza, "sub @stork"))

    game_player_ids = set([gp.player_id for gp in session.query(InProgressGamePlayer)])
    assert stork.id not in game_player_ids
    assert izza.id not in game_player_ids
