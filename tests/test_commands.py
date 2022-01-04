from unittest.mock import Mock

import pytest
from discord import Message
from pytest import approx, fixture
from trueskill import Rating

from discord_bots.commands import COMMAND_PREFIX, handle_message, is_in_game
from discord_bots.models import (
    AdminRole,
    CustomCommand,
    FinishedGame,
    FinishedGamePlayer,
    InProgressGame,
    InProgressGamePlayer,
    Player,
    PlayerDecay,
    Queue,
    QueuePlayer,
    QueueRole,
    QueueWaitlistPlayer,
    Session,
)
from discord_bots.utils import short_uuid

from .fixtures import (
    ROLE_LT_GOLD,
    TEST_GUILD,
    Channel,
    Member,
    izza,
    lyon,
    opsayo,
    setup_tests,
    stork,
)

session = Session()


# Runs around each test
@fixture(autouse=True)
def run_around_tests():
    setup_tests()


def mentions(content: str) -> list[Member]:
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
async def test_set_queue_unrated_should_make_queue_unrated():
    await handle_message(Message(opsayo, "createqueue LTpug 10"))

    await handle_message(Message(opsayo, "setqueueunrated LTpug"))

    queue: Queue = session.query(Queue).first()
    assert queue.is_rated == False


@pytest.mark.asyncio
async def test_set_queue_rated_should_make_queue_rated():
    await handle_message(Message(opsayo, "createqueue LTpug 10"))
    await handle_message(Message(opsayo, "setqueueunrated LTpug"))

    await handle_message(Message(opsayo, "setqueuerated LTpug"))

    queue: Queue = session.query(Queue).first()
    assert queue.is_rated == True


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
async def test_add_with_queue_index_should_add_player_to_queue():
    await handle_message(Message(opsayo, "createqueue LTpug 10"))

    await handle_message(Message(opsayo, "add 1"))

    queue = session.query(Queue).filter(Queue.name == "LTpug").first()
    queue_players = (
        session.query(QueuePlayer)
        .filter(QueuePlayer.player_id == opsayo.id, QueuePlayer.queue_id == queue.id)
        .all()
    )
    assert len(queue_players) == 1


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
async def test_del_with_queue_index_should_remove_player_to_queue():
    await handle_message(Message(opsayo, "createqueue LTpug 10"))
    await handle_message(Message(opsayo, "add 1"))

    await handle_message(Message(opsayo, "del 1"))

    queue = session.query(Queue).filter(Queue.name == "LTpug").first()
    queue_players = (
        session.query(QueuePlayer)
        .filter(QueuePlayer.player_id == opsayo.id, QueuePlayer.queue_id == queue.id)
        .all()
    )
    assert len(queue_players) == 0


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

    winner: Player = session.query(Player).filter(Player.id == opsayo.id).first()
    loser: Player = session.query(Player).filter(Player.id == lyon.id).first()
    assert winner.rated_trueskill_mu > Rating().mu
    assert winner.unrated_trueskill_mu > Rating().mu
    assert loser.rated_trueskill_mu < Rating().mu
    assert loser.unrated_trueskill_mu < Rating().mu

    fgp: FinishedGamePlayer
    for fgp in session.query(FinishedGamePlayer).all():
        assert fgp.rated_trueskill_mu_before != approx(fgp.rated_trueskill_mu_after)
        assert fgp.unrated_trueskill_mu_before != approx(fgp.unrated_trueskill_mu_after)


@pytest.mark.asyncio
async def test_finish_game_with_loss_should_increase_trueskill_for_other_team():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(opsayo, "finishgame loss"))

    loser: Player = session.query(Player).filter(Player.id == opsayo.id).first()
    winner: Player = session.query(Player).filter(Player.id == lyon.id).first()
    assert loser.rated_trueskill_mu < Rating().mu
    assert loser.unrated_trueskill_mu < Rating().mu
    assert winner.rated_trueskill_mu > Rating().mu
    assert winner.unrated_trueskill_mu > Rating().mu

    fgp: FinishedGamePlayer
    for fgp in session.query(FinishedGamePlayer).all():
        assert fgp.rated_trueskill_mu_before != approx(fgp.rated_trueskill_mu_after)
        assert fgp.unrated_trueskill_mu_before != approx(fgp.unrated_trueskill_mu_after)


@pytest.mark.asyncio
async def test_finish_game_with_tie_and_equal_trueskill_should_not_modify_trueskill():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(opsayo, "finishgame tie"))

    first_player: Player = session.query(Player).filter(Player.id == opsayo.id).first()
    second_player: Player = session.query(Player).filter(Player.id == lyon.id).first()

    assert first_player.rated_trueskill_mu == approx(Rating().mu)
    assert first_player.unrated_trueskill_mu == approx(Rating().mu)
    assert second_player.rated_trueskill_mu == approx(Rating().mu)
    assert second_player.unrated_trueskill_mu == approx(Rating().mu)

    fgp: FinishedGamePlayer
    for fgp in session.query(FinishedGamePlayer).all():
        assert fgp.rated_trueskill_mu_before == approx(fgp.rated_trueskill_mu_after)
        assert fgp.unrated_trueskill_mu_before == approx(fgp.unrated_trueskill_mu_after)


@pytest.mark.asyncio
async def test_finish_game_with_unrated_queue_win_should_only_affect_unrated_trueskill():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "setqueueunrated LTpug"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(opsayo, "finishgame win"))

    winner: Player = session.query(Player).filter(Player.id == opsayo.id).first()
    loser: Player = session.query(Player).filter(Player.id == lyon.id).first()
    assert winner.rated_trueskill_mu == approx(Rating().mu)
    assert winner.unrated_trueskill_mu > Rating().mu
    assert loser.rated_trueskill_mu == approx(Rating().mu)
    assert loser.unrated_trueskill_mu < Rating().mu

    fgp: FinishedGamePlayer
    for fgp in session.query(FinishedGamePlayer).all():
        assert fgp.rated_trueskill_mu_before == approx(fgp.rated_trueskill_mu_after)
        assert fgp.unrated_trueskill_mu_before != approx(fgp.unrated_trueskill_mu_after)


@pytest.mark.asyncio
async def test_finish_game_with_unrated_queue_loss_should_only_affect_unrated_trueskill():
    await handle_message(Message(opsayo, "createqueue LTpug 2"))
    await handle_message(Message(opsayo, "setqueueunrated LTpug"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))

    await handle_message(Message(opsayo, "finishgame loss"))

    loser: Player = session.query(Player).filter(Player.id == opsayo.id).first()
    winner: Player = session.query(Player).filter(Player.id == lyon.id).first()
    assert loser.rated_trueskill_mu == approx(Rating().mu)
    assert loser.unrated_trueskill_mu < Rating().mu
    assert winner.rated_trueskill_mu == approx(Rating().mu)
    assert winner.unrated_trueskill_mu > Rating().mu

    fgp: FinishedGamePlayer
    for fgp in session.query(FinishedGamePlayer).all():
        assert fgp.rated_trueskill_mu_before == fgp.rated_trueskill_mu_after
        assert fgp.unrated_trueskill_mu_before != fgp.unrated_trueskill_mu_after


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


@pytest.mark.asyncio
async def test_add_queue_role_should_add_queue_role():
    await handle_message(Message(opsayo, "createqueue LTgold 2"))

    await handle_message(Message(opsayo, "addqueuerole LTgold LTgold"))

    queue_roles = Session().query(QueueRole).all()
    assert len(queue_roles) == 1


@pytest.mark.asyncio
async def test_remove_queue_role_should_remove_queue_role():
    await handle_message(Message(opsayo, "createqueue LTgold 2"))
    await handle_message(Message(opsayo, "addqueuerole LTgold LTgold"))

    await handle_message(Message(opsayo, "removequeuerole LTgold LTgold"))

    queue_roles = Session().query(QueueRole).all()
    assert len(queue_roles) == 0


@pytest.mark.asyncio
async def test_add_to_queue_with_queue_with_no_queue_roles_should_add_to_queue():
    await handle_message(Message(opsayo, "createqueue LTunrated 2"))

    await handle_message(Message(opsayo, "add"))

    queue_players = Session().query(QueuePlayer).all()
    assert len(queue_players) == 1


@pytest.mark.asyncio
async def test_add_to_queue_with_player_with_queue_role_should_add_to_queue():
    await handle_message(Message(opsayo, "createqueue LTgold 2"))
    await handle_message(Message(opsayo, "addqueuerole LTgold LTgold"))
    opsayo.roles.append(ROLE_LT_GOLD)

    await handle_message(Message(opsayo, "add"))

    queue_players = Session().query(QueuePlayer).all()
    assert len(queue_players) == 1


@pytest.mark.asyncio
async def test_add_to_queue_with_player_without_queue_role_should_not_add_to_queue():
    await handle_message(Message(opsayo, "createqueue LTgold 2"))
    await handle_message(Message(opsayo, "addqueuerole LTgold LTgold"))
    opsayo.roles = []

    await handle_message(Message(opsayo, "add"))

    queue_players = Session().query(QueuePlayer).all()
    assert len(queue_players) == 0


@pytest.mark.asyncio
async def test_add_admin_role_should_add_admin_role():
    await handle_message(Message(opsayo, "addadminrole admin"))

    admin_roles = Session().query(AdminRole).all()
    assert len(admin_roles) == 1


@pytest.mark.asyncio
async def test_remove_admin_role_should_remove_admin_role():
    await handle_message(Message(opsayo, "addadminrole admin"))

    await handle_message(Message(opsayo, "removeadminrole admin"))

    admin_roles = Session().query(AdminRole).all()
    assert len(admin_roles) == 0


@pytest.mark.asyncio
async def test_add_with_locked_queue_should_not_add_to_queue():
    await handle_message(Message(opsayo, "createqueue ltpug 10"))
    await handle_message(Message(opsayo, "lockqueue ltpug"))

    await handle_message(Message(opsayo, "add"))

    queue_players = Session().query(QueuePlayer).all()
    assert len(queue_players) == 0


@pytest.mark.asyncio
async def test_unlock_queue_should_allow_add_to_queue():
    await handle_message(Message(opsayo, "createqueue ltpug 10"))
    await handle_message(Message(opsayo, "lockqueue ltpug"))

    await handle_message(Message(opsayo, "unlockqueue ltpug"))
    await handle_message(Message(opsayo, "add"))

    queue_players = Session().query(QueuePlayer).all()
    assert len(queue_players) == 1


@pytest.mark.asyncio
async def test_edit_game_winner_should_change_winning_team():
    await handle_message(Message(opsayo, "createqueue ltpug 2"))
    await handle_message(Message(opsayo, "add"))
    await handle_message(Message(lyon, "add"))
    await handle_message(Message(opsayo, "finishgame win"))

    finished_game = Session().query(FinishedGame).first()
    short_game_id = short_uuid(finished_game.game_id)
    await handle_message(Message(opsayo, f"editgamewinner {short_game_id} tie"))

    finished_game = Session().query(FinishedGame).first()
    assert finished_game.winning_team == -1


@pytest.mark.asyncio
async def test_create_command_should_create_command():
    await handle_message(Message(opsayo, "createcommand hello world"))

    command = Session().query(CustomCommand).first()
    assert command is not None


@pytest.mark.asyncio
async def test_remove_command_should_remove_command():
    await handle_message(Message(opsayo, "removecommand hello"))

    command = Session().query(CustomCommand).first()
    assert command is None


@pytest.mark.asyncio
async def test_decay_player_should_decay_player():
    session = Session()
    stork_player: Player = session.query(Player).filter(Player.id == stork.id).first()
    session.commit()

    await handle_message(Message(opsayo, "decayplayer @stork 10%"))

    session = Session()
    stork_player: Player = session.query(Player).filter(Player.id == stork.id).first()
    print(stork_player)
    assert stork_player.rated_trueskill_mu == Rating().mu * 0.90
    assert stork_player.unrated_trueskill_mu == Rating().mu * 0.90
    player_decays = session.query(PlayerDecay).all()
    assert len(player_decays) == 1
