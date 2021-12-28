from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4
import sys

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
)

# pylance issue with sqlalchemy:
# https://github.com/microsoft/pylance-release/issues/845
from sqlalchemy.orm import registry, sessionmaker  # type: ignore
from sqlalchemy.sql.schema import ForeignKey

"""
engine = create_engine("sqlite:///tribes.db", echo=True)
"""
# TODO: Use a test db if in test environment
# TODO: Use locking - sqlite will have corruptions if not same thread

db_url = (
    "sqlite:///tribes.test.db?check_same_thread=false"
    if "pytest" in sys.modules
    else "sqlite:///tribes.db?check_same_thread=false"
)
engine = create_engine(db_url, echo=False)
mapper_registry = registry()
Base = mapper_registry.generate_base()


"""
Sorry if this is complex.  This lets us mix Python dataclasses with SQLAlchemy
using the method here:
https://docs.sqlalchemy.org/en/14/orm/mapping_styles.html#example-two-dataclasses-with-declarative-table
"""


@mapper_registry.mapped
@dataclass
class Game:
    """
    A game either in progress or already completed

    winning_team: null means in progress, -1 means in draw, 0 means team 0, 1
    means team 1. this is so that it's easy to find the other team with (team +
    1) % 2
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "game"

    queue_id: str = field(
        metadata={
            "sa": Column(String, ForeignKey("queue.id"), nullable=False, index=True)
        },
    )
    winning_team: int | None = field(
        init=False,
        metadata={"sa": Column(Integer, index=True)},
    )
    finished_at: datetime | None = field(
        init=False,
        metadata={"sa": Column(DateTime, index=True)},
    )
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
        init=False,
        metadata={"sa": Column(DateTime, index=True)},
    )
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


@mapper_registry.mapped
@dataclass
class GamePlayer:
    """
    A participant in a game
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "game_player"

    game_id: str = field(
        metadata={
            "sa": Column(String, ForeignKey("game.id"), nullable=False, index=True)
        },
    )
    player_id: int = field(
        metadata={
            "sa": Column(Integer, ForeignKey("player.id"), nullable=False, index=True)
        },
    )
    team: int = field(metadata={"sa": Column(Integer, nullable=False, index=True)})
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


@mapper_registry.mapped
@dataclass
class GameChannel:
    """
    A channel created for a game, intended for temporary voice channels
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "game_channel"

    game_id: str = field(
        metadata={
            "sa": Column(String, ForeignKey("game.id"), nullable=False, index=True)
        },
    )
    channel_id: int = field(
        metadata={"sa": Column(Integer, nullable=False)},
    )
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


@mapper_registry.mapped
@dataclass
class Player:
    """
    id: We use the user id from discord
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "player"

    id: int = field(metadata={"sa": Column(Integer, primary_key=True)})
    name: str = field(metadata={"sa": Column(String, nullable=False)})
    is_admin: bool = field(
        default=False, metadata={"sa": Column(Boolean, nullable=False)}
    )
    is_banned: bool = field(
        default=False, metadata={"sa": Column(Boolean, nullable=False)}
    )
    last_activity_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
        metadata={"sa": Column(DateTime)},
    )

    # TODO:
    # trueskill_rating: float = Column(Float, nullable=False, default=0.0)


@mapper_registry.mapped
@dataclass
class Queue:
    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "queue"

    name: str = field(
        metadata={"sa": Column(String, unique=True, nullable=False, index=True)}
    )
    size: int = field(metadata={"sa": Column(Integer, nullable=False)})
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


@mapper_registry.mapped
@dataclass
class QueuePlayer:
    """
    Players currently waiting in a queue

    :channel_id: The channel that the user sent the message to join the queue
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "queue_player"
    __table_args__ = (UniqueConstraint("queue_id", "player_id"),)

    queue_id: str = field(
        metadata={
            "sa": Column(String, ForeignKey("queue.id"), nullable=False, index=True)
        },
    )
    player_id: int = field(
        metadata={
            "sa": Column(Integer, ForeignKey("player.id"), nullable=False, index=True)
        },
    )
    channel_id: int = field(metadata={"sa": Column(Integer, nullable=False)})
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


@mapper_registry.mapped
@dataclass
class QueueWaitlistPlayer:
    """
    Player in a waitlist to be automatically added to a queue.

    Used when players just finished a game to randomly add them back to the
    queue
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "queue_waitlist_player"
    __table_args__ = (UniqueConstraint("game_id", "queue_id", "player_id"),)

    game_id: str = field(
        metadata={
            "sa": Column(String, ForeignKey("game.id"), nullable=False, index=True)
        },
    )
    queue_id: str = field(
        metadata={"sa": Column(String, ForeignKey("queue.id"), nullable=False)},
    )
    player_id: int = field(
        metadata={"sa": Column(Integer, ForeignKey("player.id"), nullable=False)},
    )
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
