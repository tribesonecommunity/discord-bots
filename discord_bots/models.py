from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4
import sys

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
)
import trueskill

# pylance issue with sqlalchemy:
# https://github.com/microsoft/pylance-release/issues/845
from sqlalchemy.orm import registry, sessionmaker  # type: ignore
from sqlalchemy.sql.schema import ForeignKey

# TODO: Create db backups on start or periodically
# It may be tempting, but do not set check_same_thread=False here. Sqlite
# doesn't handle concurrency well and writing to the db on different threads
# could cause file corruption. Use a queue / tasks to ensure that writes happen
# on the main thread.
db_url = (
    "sqlite:///tribes.test.db" if "pytest" in sys.modules else "sqlite:///tribes.db"
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
class AdminRole:
    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "admin_role"

    role_id: int = field(
        metadata={"sa": Column(Integer, nullable=False)},
    )
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


@mapper_registry.mapped
@dataclass
class FinishedGame:

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "finished_game"

    game_id: str = field(metadata={"sa": Column(String, index=True, nullable=False)})
    finished_at: datetime = field(
        metadata={"sa": Column(DateTime, index=True, nullable=False)},
    )
    queue_name: str = field(
        metadata={"sa": Column(String, index=True, nullable=False)},
    )
    started_at: datetime = field(
        metadata={"sa": Column(DateTime, index=True, nullable=False)},
    )
    win_probability: float = field(metadata={"sa": Column(Float, nullable=False)})
    winning_team: int = field(
        metadata={"sa": Column(Integer, index=True, nullable=False)},
    )
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


@mapper_registry.mapped
@dataclass
class FinishedGamePlayer:
    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "finished_game_player"

    finished_game_id: str = field(
        metadata={
            "sa": Column(
                String, ForeignKey("finished_game.id"), nullable=False, index=True
            )
        },
    )
    player_id: int = field(
        metadata={"sa": Column(Integer, ForeignKey("player.id"), index=True)},
    )
    player_name: str = field(
        metadata={
            "sa": Column(String, ForeignKey("player.id"), nullable=False, index=True)
        },
    )
    team: int = field(metadata={"sa": Column(Integer, nullable=False, index=True)})
    trueskill_mu_after: float = field(metadata={"sa": Column(Float, nullable=False)})
    trueskill_mu_before: float = field(metadata={"sa": Column(Float, nullable=False)})
    trueskill_sigma_after: float = field(metadata={"sa": Column(Float, nullable=False)})
    trueskill_sigma_before: float = field(
        metadata={"sa": Column(Float, nullable=False)}
    )
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


@mapper_registry.mapped
@dataclass
class InProgressGame:
    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "in_progress_game"

    queue_id: str | None = field(
        metadata={"sa": Column(String, ForeignKey("queue.id"), index=True)},
    )
    win_probability: float = field(metadata={"sa": Column(Float, nullable=False)})
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
class InProgressGamePlayer:
    """
    A participant in a game
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "in_progress_game_player"

    in_progress_game_id: str = field(
        metadata={
            "sa": Column(
                String, ForeignKey("in_progress_game.id"), nullable=False, index=True
            )
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
class InProgressGameChannel:
    """
    A channel created for a game, intended for temporary voice channels
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "in_progress_game_channel"

    in_progress_game_id: str = field(
        metadata={
            "sa": Column(
                String, ForeignKey("in_progress_game.id"), nullable=False, index=True
            )
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
    :id: We use the user id from discord
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
    trueskill_mu: float = field(
        default=trueskill.Rating().mu, metadata={"sa": Column(Float, nullable=False)}
    )
    trueskill_sigma: float = field(
        default=trueskill.Rating().sigma, metadata={"sa": Column(Float, nullable=False)}
    )


@mapper_registry.mapped
@dataclass
class Queue:
    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "queue"

    name: str = field(
        metadata={"sa": Column(String, unique=True, nullable=False, index=True)}
    )
    size: int = field(metadata={"sa": Column(Integer, nullable=False)})
    is_locked: bool = field(
        default=False, metadata={"sa": Column(Boolean, nullable=False)}
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
class QueueRole:
    """
    :role_id: Discord id, used like guild.get_role(role_id)
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "queue_role"

    queue_id: str = field(
        metadata={
            "sa": Column(String, ForeignKey("queue.id"), index=True, nullable=False)
        },
    )
    role_id: int = field(
        metadata={"sa": Column(Integer, nullable=False)},
    )
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
    __table_args__ = (UniqueConstraint("finished_game_id", "queue_id", "player_id"),)

    finished_game_id: str = field(
        metadata={
            "sa": Column(
                String, ForeignKey("finished_game.id"), nullable=False, index=True
            )
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


Session: sessionmaker = sessionmaker(bind=engine)
