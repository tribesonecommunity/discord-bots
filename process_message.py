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
from models import Queue, Session


if __name__ == "__main__":
    opsayo = Author("opsayo")
    stork = Author("stork")
    izza = Author("izza")
    lyon = Author("lyon")

    ADMINS.add(opsayo)
    session = Session()

    on_message(Message(opsayo, "!commands"))

    # on_message(Message(opsayo, "createqueue"))
    on_message(Message(opsayo, "!createqueue"))

    on_message(Message(opsayo, "!createqueue LTgold 5"))
    on_message(Message(opsayo, "!createqueue LTgold 10"))
    on_message(Message(opsayo, "!createqueue LTgold 10"))
    on_message(Message(opsayo, "!createqueue LTpug 10"))
    on_message(Message(opsayo, "!createqueue LTunrated 10"))
    queues = [q for q in session.query(Queue)]
    assert len(queues) == 3

    on_message(Message(opsayo, "!removequeue"))
    on_message(Message(opsayo, "!removequeue LTgold"))
    on_message(Message(opsayo, "!removequeue LTfun"))
    queues = [q for q in session.query(Queue)]
    assert len(queues) == 2

    on_message(Message(opsayo, "!add"))
    for queue in QUEUES.values():
        assert len(queue.players) == 1

    on_message(Message(opsayo, "!del"))
    for queue in QUEUES.values():
        assert len(queue.players) == 0

    on_message(Message(opsayo, "!add LTgold"))
    on_message(Message(opsayo, "!add LTpug"))
    assert opsayo in QUEUES["LTpug"].players

    on_message(Message(opsayo, "!del LTgold"))
    on_message(Message(opsayo, "!del LTpug"))
    assert opsayo not in QUEUES["LTpug"].players
    on_message(Message(opsayo, "!del LTpug"))

    on_message(Message(opsayo, "!createqueue LTpug 2"))
    on_message(Message(opsayo, "!add LTpug"))
    on_message(Message(stork, "!add LTpug"))
    assert len(GAMES["LTpug"]) == 1
    on_message(Message(lyon, "!add LTpug"))
    on_message(Message(stork, "!add LTpug"))
    assert len(GAMES["LTpug"]) == 1

    on_message(Message(stork, "!status"))

    on_message(Message(lyon, "!finishgame"))
    on_message(Message(lyon, "!finishgame loss"))
    on_message(Message(izza, "!add LTpug"))
    assert len(GAMES["LTpug"]) == 2
    on_message(Message(stork, "!finishgame"))
    on_message(Message(stork, "!finishgame loss"))
    assert len(GAMES["LTpug"]) == 1

    on_message(Message(stork, "!status"))
    on_message(Message(stork, "!sub"))
    on_message(Message(izza, "!sub lyon"))
    on_message(Message(stork, "!sub opsayo"))
    on_message(Message(stork, "!sub izza"))
    on_message(Message(stork, "!status"))

    on_message(Message(opsayo, "!cancelgame"))
    on_message(Message(opsayo, "!status"))
    assert len(GAMES["LTpug"]) == 1
    on_message(Message(lyon, "!cancelgame"))
    on_message(Message(opsayo, "!status"))
    assert len(GAMES["LTpug"]) == 0
    on_message(Message(lyon, "!add LTpug"))

    # Re-add timer triggers here
    on_message(Message(stork, "!add LTpug"))
    assert len(GAMES["LTpug"]) == 0
    sleep(RE_ADD_DELAY)
    on_message(Message(stork, "!add LTpug"))
    assert len(GAMES["LTpug"]) == 1

    on_message(Message(stork, "!cancelgame " + GAMES["LTpug"][0].id))
    assert len(GAMES["LTpug"]) == 1
    on_message(Message(opsayo, "!cancelgame " + GAMES["LTpug"][0].id))
    assert len(GAMES["LTpug"]) == 0
    on_message(Message(opsayo, "!status"))

    on_message(Message(opsayo, "!addadmin lyon"))
    on_message(Message(opsayo, "!removeadmin lyon"))
    on_message(Message(opsayo, "!removeadmin opsayo"))
    on_message(Message(izza, "!addadmin stork"))

    on_message(Message(opsayo, "!ban lyon"))
    assert len(BANNED_PLAYERS) == 1
    on_message(Message(opsayo, "!ban lyon"))
    assert len(BANNED_PLAYERS) == 1
    on_message(Message(izza, "!ban opsayo"))
    assert len(BANNED_PLAYERS) == 1
    on_message(Message(opsayo, "!listbans"))

    on_message(Message(opsayo, "!unban lyon"))
    assert len(BANNED_PLAYERS) == 0
    on_message(Message(opsayo, "!unban lyon"))
    on_message(Message(izza, "!unban opsayo"))
    on_message(Message(opsayo, "!listbans"))

    on_message(Message(opsayo, "!coinflip"))
    on_message(Message(opsayo, "!setcommandprefix"))
    on_message(Message(opsayo, "!setcommandprefix #"))
    on_message(Message(opsayo, "#coinflip"))
    on_message(Message(opsayo, "#setcommandprefix !"))
    on_message(Message(opsayo, "!coinflip"))

    on_message(Message(opsayo, "!setadddelay"))
    on_message(Message(opsayo, "!setadddelay 1"))
    sleep(1)

    on_message(Message(opsayo, "!add LTpug"))
    on_message(Message(stork, "!add LTpug"))
    on_message(Message(opsayo, "!finishgame win"))
    on_message(Message(opsayo, "!add LTpug"))
    assert len(QUEUES["LTpug"].players) == 0