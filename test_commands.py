from time import sleep

from commands import (
    ADMINS,
    Author,
    BANNED_PLAYERS,
    GAMES,
    Message,
    RE_ADD_DELAY,
    on_message,
)
from models import Queue, QueuePlayer, Session


opsayo = Author("opsayo")
stork = Author("stork")
izza = Author("izza")
lyon = Author("lyon")

ADMINS.add(opsayo)
session = Session()

on_message(Message(opsayo, "!commands"))


def test_create_queue_with_odd_size_then_does_not_create_queue():
    session.query(Queue).delete()
    session.commit()

    on_message(Message(opsayo, "!createqueue LTgold 5"))

    queues = [q for q in session.query(Queue)]
    assert len(queues) == 0


def test_create_queue_should_create_queue():
    session.query(Queue).delete()
    session.commit()

    on_message(Message(opsayo, "!createqueue LTpug 10"))

    queues = [q for q in session.query(Queue)]
    assert len(queues) == 1


def test_remove_queue_should_remove_queue():
    session.query(Queue).delete()
    session.commit()

    on_message(Message(opsayo, "!createqueue LTpug 10"))
    on_message(Message(opsayo, "!removequeue LTpug"))

    queues = [q for q in session.query(Queue)]
    assert len(queues) == 0


def test_remove_queue_with_nonexistent_queue_should_not_throw_exception():
    session.query(Queue).delete()
    session.commit()

    on_message(Message(opsayo, "!removequeue LTpug"))

    assert True


def test_add_should_add_player_to_all_queues():
    session.query(Queue).delete()
    session.query(QueuePlayer).delete()
    session.commit()
    on_message(Message(opsayo, "!createqueue LTpug 10"))
    on_message(Message(opsayo, "!createqueue LTunrated 10"))

    on_message(Message(opsayo, "!add"))

    for queue_name in ("LTpug", "LTunrated"):
        queue = [q for q in session.query(Queue).filter(Queue.name == queue_name)][0]
        queue_players = [
            qp
            for qp in session.query(QueuePlayer).filter(
                QueuePlayer.player_id == opsayo.id, QueuePlayer.queue_id == queue.id
            )
        ]
        assert len(queue_players) == 1


def test_add_with_multiple_calls_should_not_throw_exception():
    session.query(Queue).delete()
    session.query(QueuePlayer).delete()
    session.commit()
    on_message(Message(opsayo, "!createqueue LTpug 10"))

    on_message(Message(opsayo, "!add"))
    on_message(Message(opsayo, "!add"))

    assert True


def test_add_with_queue_named_should_add_player_to_named_queue():
    session.query(Queue).delete()
    session.query(QueuePlayer).delete()
    session.commit()
    on_message(Message(opsayo, "!createqueue LTpug 10"))

    on_message(Message(opsayo, "!add LTpug"))

    lt_pug_queue = [q for q in session.query(Queue).filter(Queue.name == "LTpug")][0]
    lt_pug_queue_players = [
        qp
        for qp in session.query(QueuePlayer).filter(
            QueuePlayer.player_id == opsayo.id, QueuePlayer.queue_id == lt_pug_queue.id
        )
    ]
    assert len(lt_pug_queue_players) == 1


def test_add_with_queue_named_should_not_add_player_to_unnamed_queue():
    session.query(Queue).delete()
    session.query(QueuePlayer).delete()
    session.commit()
    on_message(Message(opsayo, "!createqueue LTpug 10"))
    on_message(Message(opsayo, "!createqueue LTunrated 10"))

    on_message(Message(opsayo, "!add LTpug"))

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
    assert len(lt_unrated_queue_players) == 0


def test_del_should_remove_player_from_all_queues():
    session.query(Queue).delete()
    session.query(QueuePlayer).delete()
    session.commit()
    on_message(Message(opsayo, "!createqueue LTpug 10"))
    on_message(Message(opsayo, "!createqueue LTunrated 10"))
    on_message(Message(opsayo, "!add"))

    on_message(Message(opsayo, "!del"))

    for queue_name in ("LTpug", "LTunrated"):
        queue = [q for q in session.query(Queue).filter(Queue.name == queue_name)][0]
        queue_players = [
            qp
            for qp in session.query(QueuePlayer).filter(
                QueuePlayer.player_id == opsayo.id, QueuePlayer.queue_id == queue.id
            )
        ]
        assert len(queue_players) == 0


def test_del_with_queue_named_should_del_player_from_named_queue():
    session.query(Queue).delete()
    session.query(QueuePlayer).delete()
    session.commit()
    on_message(Message(opsayo, "!createqueue LTpug 10"))
    on_message(Message(opsayo, "!add LTpug"))

    on_message(Message(opsayo, "!del LTpug"))

    lt_pug_queue = [q for q in session.query(Queue).filter(Queue.name == "LTpug")][0]
    lt_pug_queue_players = [
        qp
        for qp in session.query(QueuePlayer).filter(
            QueuePlayer.player_id == opsayo.id, QueuePlayer.queue_id == lt_pug_queue.id
        )
    ]
    assert len(lt_pug_queue_players) == 0


def test_add_with_queue_named_should_not_add_player_to_unnamed_queue():
    session.query(Queue).delete()
    session.query(QueuePlayer).delete()
    session.commit()
    on_message(Message(opsayo, "!createqueue LTpug 10"))
    on_message(Message(opsayo, "!createqueue LTunrated 10"))
    on_message(Message(opsayo, "!add"))

    on_message(Message(opsayo, "!del LTpug"))

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


# on_message(Message(opsayo, "!del"))
# on_message(Message(opsayo, "!listqueues"))
# for queue in QUEUES.values():
#     assert len(queue.players) == 0

# on_message(Message(opsayo, "!add LTgold"))
# on_message(Message(opsayo, "!add LTpug"))
# assert opsayo in QUEUES["LTpug"].players
# on_message(Message(opsayo, "!listqueues"))

# on_message(Message(opsayo, "!del LTgold"))
# on_message(Message(opsayo, "!del LTpug"))
# assert opsayo not in QUEUES["LTpug"].players
# on_message(Message(opsayo, "!del LTpug"))
# on_message(Message(opsayo, "!listqueues"))

# on_message(Message(opsayo, "!createqueue LTpug 2"))
# on_message(Message(opsayo, "!add LTpug"))
# on_message(Message(stork, "!add LTpug"))
# assert len(GAMES["LTpug"]) == 1
# on_message(Message(lyon, "!add LTpug"))
# on_message(Message(stork, "!add LTpug"))
# assert len(GAMES["LTpug"]) == 1

# on_message(Message(stork, "!listqueues"))
# on_message(Message(stork, "!status"))

# on_message(Message(lyon, "!finishgame"))
# on_message(Message(lyon, "!finishgame loss"))
# on_message(Message(izza, "!add LTpug"))
# assert len(GAMES["LTpug"]) == 2
# on_message(Message(stork, "!finishgame"))
# on_message(Message(stork, "!finishgame loss"))
# assert len(GAMES["LTpug"]) == 1

# on_message(Message(stork, "!status"))
# on_message(Message(stork, "!sub"))
# on_message(Message(izza, "!sub lyon"))
# on_message(Message(stork, "!sub opsayo"))
# on_message(Message(stork, "!sub izza"))
# on_message(Message(stork, "!status"))

# on_message(Message(opsayo, "!cancelgame"))
# on_message(Message(opsayo, "!status"))
# assert len(GAMES["LTpug"]) == 1
# on_message(Message(lyon, "!cancelgame"))
# on_message(Message(opsayo, "!status"))
# assert len(GAMES["LTpug"]) == 0
# on_message(Message(lyon, "!add LTpug"))

# # Re-add timer triggers here
# on_message(Message(stork, "!add LTpug"))
# assert len(GAMES["LTpug"]) == 0
# sleep(RE_ADD_DELAY)
# on_message(Message(stork, "!add LTpug"))
# assert len(GAMES["LTpug"]) == 1

# on_message(Message(stork, "!cancelgame " + GAMES["LTpug"][0].id))
# assert len(GAMES["LTpug"]) == 1
# on_message(Message(opsayo, "!cancelgame " + GAMES["LTpug"][0].id))
# assert len(GAMES["LTpug"]) == 0
# on_message(Message(opsayo, "!status"))

# on_message(Message(opsayo, "!addadmin lyon"))
# on_message(Message(opsayo, "!removeadmin lyon"))
# on_message(Message(opsayo, "!removeadmin opsayo"))
# on_message(Message(izza, "!addadmin stork"))

# on_message(Message(opsayo, "!ban lyon"))
# assert len(BANNED_PLAYERS) == 1
# on_message(Message(opsayo, "!ban lyon"))
# assert len(BANNED_PLAYERS) == 1
# on_message(Message(izza, "!ban opsayo"))
# assert len(BANNED_PLAYERS) == 1
# on_message(Message(opsayo, "!listbans"))

# on_message(Message(opsayo, "!unban lyon"))
# assert len(BANNED_PLAYERS) == 0
# on_message(Message(opsayo, "!unban lyon"))
# on_message(Message(izza, "!unban opsayo"))
# on_message(Message(opsayo, "!listbans"))

# on_message(Message(opsayo, "!coinflip"))
# on_message(Message(opsayo, "!setcommandprefix"))
# on_message(Message(opsayo, "!setcommandprefix #"))
# on_message(Message(opsayo, "#coinflip"))
# on_message(Message(opsayo, "#setcommandprefix !"))
# on_message(Message(opsayo, "!coinflip"))

# on_message(Message(opsayo, "!setadddelay"))
# on_message(Message(opsayo, "!setadddelay 1"))
# sleep(1)

# on_message(Message(opsayo, "!add LTpug"))
# on_message(Message(stork, "!add LTpug"))
# on_message(Message(opsayo, "!finishgame win"))
# on_message(Message(opsayo, "!add LTpug"))
# assert len(QUEUES["LTpug"].players) == 0
