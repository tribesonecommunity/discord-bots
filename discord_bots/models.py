import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

import trueskill
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

# pylance issue with sqlalchemy:
# https://github.com/microsoft/pylance-release/issues/845
from sqlalchemy.orm import registry, sessionmaker  # type: ignore
from sqlalchemy.sql import func
from sqlalchemy.sql.schema import ForeignKey, MetaData

DB_NAME = "tribes"

# It may be tempting, but do not set check_same_thread=False here. Sqlite
# doesn't handle concurrency well and writing to the db on different threads
# could cause file corruption. Use tasks to ensure that writes happen on the main thread.
db_url = (
    f"sqlite:///{DB_NAME}.test.db"
    if "pytest" in sys.modules
    else f"sqlite:///{DB_NAME}.db"
)
engine = create_engine(db_url, echo=False)
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
mapper_registry = registry(metadata=MetaData(naming_convention=naming_convention))
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
class CurrentMap:
    """
    The current map up to play - not necessarily a rotation map. The rotation
    index is stored so we can find the next map.

    This table is intended to store one and only one row.
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "current_map"

    map_rotation_index: int = field(metadata={"sa": Column(Integer)})
    full_name: str = field(metadata={"sa": Column(String)})
    short_name: str = field(metadata={"sa": Column(String)})
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
        init=False,
        metadata={"sa": Column(DateTime, nullable=False, server_default=func.now())},
    )
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
        init=False,
        metadata={"sa": Column(DateTime, nullable=False)},
    )
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


@mapper_registry.mapped
@dataclass
class CustomCommand:
    """
    A way for users to add custom text commands to the bot
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "custom_command"

    name: str = field(
        metadata={"sa": Column(String, index=True, nullable=False, unique=True)},
    )
    output: str = field(
        metadata={"sa": Column(String, nullable=False)},
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

    average_trueskill: float = field(metadata={"sa": Column(Float, nullable=False)})
    game_id: str = field(metadata={"sa": Column(String, index=True, nullable=False)})
    finished_at: datetime = field(
        metadata={"sa": Column(DateTime, index=True, nullable=False)},
    )
    is_rated: bool = field(metadata={"sa": Column(Boolean, nullable=False)})
    map_full_name: str = field(metadata={"sa": Column(String, server_default="")})
    map_short_name: str = field(
        metadata={"sa": Column(String, index=True, server_default="")}
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
    team0_name: str = field(
        default="Blood Eagle",
        metadata={"sa": Column(String, nullable=False, server_default="Blood Eagle")},
    )
    team1_name: str = field(
        default="Diamond Sword",
        metadata={"sa": Column(String, nullable=False, server_default="Diamond Sword")},
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
    rated_trueskill_mu_after: float = field(
        metadata={"sa": Column(Float, nullable=False)}
    )
    rated_trueskill_mu_before: float = field(
        metadata={"sa": Column(Float, nullable=False)}
    )
    rated_trueskill_sigma_after: float = field(
        metadata={"sa": Column(Float, nullable=False)}
    )
    rated_trueskill_sigma_before: float = field(
        metadata={"sa": Column(Float, nullable=False)}
    )
    unrated_trueskill_mu_after: float = field(
        metadata={"sa": Column(Float, nullable=False)}
    )
    unrated_trueskill_mu_before: float = field(
        metadata={"sa": Column(Float, nullable=False)}
    )
    unrated_trueskill_sigma_after: float = field(
        metadata={"sa": Column(Float, nullable=False)}
    )
    unrated_trueskill_sigma_before: float = field(
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

    average_trueskill: float = field(metadata={"sa": Column(Float, nullable=True)})
    map_full_name: str = field(metadata={"sa": Column(String, server_default="")})
    map_short_name: str = field(
        metadata={"sa": Column(String, index=True, server_default="")}
    )
    queue_id: str | None = field(
        metadata={"sa": Column(String, ForeignKey("queue.id"), index=True)},
    )
    win_probability: float = field(metadata={"sa": Column(Float, nullable=False)})
    team0_name: str = field(
        default="Blood Eagle",
        metadata={"sa": Column(String, nullable=False, server_default="Blood Eagle")},
    )
    team1_name: str = field(
        default="Diamond Sword",
        metadata={"sa": Column(String, nullable=False, server_default="Diamond Sword")},
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
class MapVote:
    """
    A player's vote to replace the current map
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "map_vote"
    __table_args__ = (UniqueConstraint("player_id", "voteable_map_id"),)

    channel_id: int = field(metadata={"sa": Column(Integer, nullable=False)})
    player_id: int = field(
        metadata={
            "sa": Column(Integer, ForeignKey("player.id"), nullable=False, index=True)
        }
    )
    voteable_map_id: str = field(
        metadata={
            "sa": Column(
                String, ForeignKey("voteable_map.id"), nullable=False, index=True
            )
        },
    )
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


@mapper_registry.mapped
@dataclass
class SkipMapVote:
    """
    A player's vote to skip to the next map in the rotation
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "skip_map_vote"

    channel_id: int = field(metadata={"sa": Column(Integer, nullable=False)})
    player_id: int = field(
        metadata={
            "sa": Column(
                Integer,
                ForeignKey("player.id"),
                nullable=False,
                unique=True,
                index=True,
            )
        },
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
    :rated_trueskill_mu: A player's trueskill rating accounting for only rated
    games.
    :unrated_trueskill_mu: A player's trueskill rating account for rated and
    unrated games.
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
    rated_trueskill_mu: float = field(
        default=trueskill.Rating().mu, metadata={"sa": Column(Float, nullable=False)}
    )
    rated_trueskill_sigma: float = field(
        default=trueskill.Rating().sigma, metadata={"sa": Column(Float, nullable=False)}
    )
    unrated_trueskill_mu: float = field(
        default=trueskill.Rating().mu, metadata={"sa": Column(Float, nullable=False)}
    )
    unrated_trueskill_sigma: float = field(
        default=trueskill.Rating().sigma, metadata={"sa": Column(Float, nullable=False)}
    )


@mapper_registry.mapped
@dataclass
class PlayerDecay:
    """
    A manual instance of decaying a player's trueskill
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "player_decay"

    player_id: int = field(
        metadata={
            "sa": Column(Integer, ForeignKey("player.id"), nullable=False, index=True)
        },
    )
    decay_percentage: float = field(metadata={"sa": Column(Float, nullable=False)})
    rated_trueskill_mu_before: float = field(
        metadata={"sa": Column(Float, nullable=False)}
    )
    rated_trueskill_mu_after: float = field(
        metadata={"sa": Column(Float, nullable=False)}
    )
    unrated_trueskill_mu_before: float = field(
        metadata={"sa": Column(Float, nullable=False)}
    )
    unrated_trueskill_mu_after: float = field(
        metadata={"sa": Column(Float, nullable=False)}
    )
    decayed_at: datetime = field(
        init=False,
        default_factory=lambda: datetime.now(timezone.utc),
        metadata={"sa": Column(DateTime)},
    )
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
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
    is_rated: bool = field(
        default=True, metadata={"sa": Column(Boolean, nullable=False)}
    )
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
class QueueWaitlist:
    """
    A waitlist to buffer players after they finish game. Players are randomly
    added from this waitlist into queues.
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "queue_waitlist"

    channel_id: int = field(metadata={"sa": Column(Integer, nullable=False)})
    finished_game_id: str = field(
        metadata={
            "sa": Column(
                String, ForeignKey("finished_game.id"), nullable=False, unique=True
            )
        },
    )
    guild_id: int = field(metadata={"sa": Column(Integer, nullable=False)})
    in_progress_game_id: str = field(
        metadata={
            "sa": Column(
                String, ForeignKey("in_progress_game.id"), nullable=False, unique=True
            )
        },
    )
    queue_id: str = field(
        metadata={"sa": Column(String, ForeignKey("queue.id"), nullable=False)},
    )
    end_waitlist_at: datetime = field(
        metadata={"sa": Column(DateTime, index=True, nullable=False)},
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
    __table_args__ = (UniqueConstraint("queue_id", "queue_waitlist_id", "player_id"),)

    queue_id: str | None = field(
        metadata={"sa": Column(String, ForeignKey("queue.id"), nullable=True)}
    )
    queue_waitlist_id: str = field(
        metadata={
            "sa": Column(
                String, ForeignKey("queue_waitlist.id"), nullable=False, index=True
            )
        },
    )
    player_id: int = field(
        metadata={"sa": Column(Integer, ForeignKey("player.id"), nullable=False)},
    )
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


@mapper_registry.mapped
@dataclass
class RotationMap:
    """
    A map that's part of the fixed rotation
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "rotation_map"

    full_name: str = field(metadata={"sa": Column(String, unique=True, index=True)})
    short_name: str = field(metadata={"sa": Column(String, unique=True, index=True)})
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
class VoteableMap:
    """
    A map that can be voted in to replace the current map in rotation
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "voteable_map"

    full_name: str = field(metadata={"sa": Column(String, unique=True, index=True)})
    short_name: str = field(metadata={"sa": Column(String, unique=True, index=True)})
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


Session: sessionmaker = sessionmaker(bind=engine)
