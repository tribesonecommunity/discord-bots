from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4
from dotenv import load_dotenv

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
    text,
)

from sqlalchemy.ext.hybrid import hybrid_property

# pylance issue with sqlalchemy:
# https://github.com/microsoft/pylance-release/issues/845
from sqlalchemy.orm import registry, sessionmaker  # type: ignore
from sqlalchemy.sql import expression, func
from sqlalchemy.sql.schema import ForeignKey, MetaData

load_dotenv()
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
    is_random: bool = field(
        default=False,
        metadata={
            "sa": Column(Boolean, nullable=False, server_default=expression.false())
        },
    )
    random_probability: float = field(
        default=0,
        metadata={"sa": Column(Integer, nullable=False, server_default=text("0"))},
    )
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
    queue_region_name: str = field(
        default=None,
        metadata={"sa": Column(String, index=True, nullable=True)},
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
        metadata={"sa": Column(String, nullable=False, index=True)},
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

    def __lt__(self, other: FinishedGamePlayer):
        return self.player_id < other.player_id


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


default_rating = trueskill.Rating()
DEFAULT_TRUESKILL_MU = float(os.getenv("DEFAULT_TRUESKILL_MU") or default_rating.mu)
DEFAULT_TRUESKILL_SIGMA = float(
    os.getenv("DEFAULT_TRUESKILL_MU") or default_rating.sigma
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
    :raffle_tickets: The number of raffle tickets a player has
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
        default=DEFAULT_TRUESKILL_MU, metadata={"sa": Column(Float, nullable=False)}
    )
    rated_trueskill_sigma: float = field(
        default=DEFAULT_TRUESKILL_SIGMA, metadata={"sa": Column(Float, nullable=False)}
    )
    unrated_trueskill_mu: float = field(
        default=DEFAULT_TRUESKILL_MU, metadata={"sa": Column(Float, nullable=False)}
    )
    unrated_trueskill_sigma: float = field(
        default=DEFAULT_TRUESKILL_SIGMA, metadata={"sa": Column(Float, nullable=False)}
    )
    raffle_tickets: int = field(
        default=0,
        metadata={
            "sa": Column(Integer, index=True, nullable=False, server_default=text("0"))
        },
    )
    leaderboard_enabled: bool = field(
        default=True,
        metadata={
            "sa": Column(Boolean, nullable=False, server_default=expression.true())
        },
    )

    @hybrid_property
    def leaderboard_trueskill(self):
        return self.rated_trueskill_mu - 3 * self.rated_trueskill_sigma

    def __lt__(self, other: Player):
        return self.id < other.id


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
class PlayerRegionTrueskill:
    """
    Separate a player's trueskill by region
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "player_region_trueskill"

    player_id: int = field(
        metadata={
            "sa": Column(Integer, ForeignKey("player.id"), nullable=False, index=True)
        },
    )
    queue_region_id: str = field(
        metadata={
            "sa": Column(
                String,
                ForeignKey("queue_region.id"),
                nullable=False,
                index=True,
            )
        },
    )
    rated_trueskill_mu: float = field(metadata={"sa": Column(Float, nullable=False)})
    rated_trueskill_sigma: float = field(metadata={"sa": Column(Float, nullable=False)})
    unrated_trueskill_mu: float = field(metadata={"sa": Column(Float, nullable=False)})
    unrated_trueskill_sigma: float = field(
        metadata={"sa": Column(Float, nullable=False)}
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

    @hybrid_property
    def leaderboard_trueskill(self):
        return self.rated_trueskill_mu - 3 * self.rated_trueskill_sigma


@mapper_registry.mapped
@dataclass
class Queue:
    """
    :is_isolated: A queue that doesn't interact with the other queues. No
    auto-adds, no waitlists, doesn't affect map rotation, and doesn't affect
    trueskill. Useful for things like 1v1s, duels, etc.
    :is_sweaty: A sweaty queue picks the top 10 trueskill ranked players in the
    queue.  For example if 9 players are waiting to play and another game
    finishes and 10 players add to a sweaty queue, the top 10 players in
    trueskill will get into the next game regardless of who was waiting longest.
    """

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
    is_isolated: bool = field(
        default=False,
        metadata={
            "sa": Column(
                Boolean, index=True, nullable=False, server_default=expression.false()
            )
        },
    )
    is_sweaty: bool = field(
        default=False,
        metadata={
            "sa": Column(Boolean, nullable=False, server_default=expression.false())
        },
    )
    queue_region_id: str = field(
        default=None,
        metadata={
            "sa": Column(
                String,
                ForeignKey("queue_region.id"),
                nullable=True,
                index=True,
            )
        },
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
class QueueNotification:
    """
    Notify a player via DM when a queue first reaches a certain size
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "queue_notification"

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
    size: int = field(metadata={"sa": Column(Integer, nullable=False, index=True)})
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
class QueueRegion:
    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "queue_region"

    name: str = field(metadata={"sa": Column(String, nullable=False)})
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

    :in_progress_game_id: Needed to close channels after processing waitlist
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
class Raffle:
    """
    An instance of a raffle

    :code: Auto-generated code to run / reset the raffle. Used to prevent accidentally running it or resetting it
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "raffle"

    code: str = field(default=None, metadata={"sa": Column(String)})
    winning_player_id: int = field(
        default=None,
        metadata={"sa": Column(Integer, ForeignKey("player.id"), index=True)},
    )
    total_tickets: int = field(
        default=0, metadata={"sa": Column(Integer, index=True, nullable=False)}
    )
    winning_player_total_tickets: int = field(
        default=0, metadata={"sa": Column(Integer, index=True, nullable=False)}
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
class RotationMap:
    """
    A map that's part of the fixed rotation

    :raffle_ticket_reward: The number of raffle tickets this map rewards for playing it
    :default_full_name: For random maps, the default when random map is not rolled
    :default_short_name: For random maps, the default when random map is not rolled
    :rolled_full_name: The random map rolled during rotation
    :rolled_short_name: The random map rolled during rotation
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "rotation_map"

    full_name: str = field(metadata={"sa": Column(String, unique=True, index=True)})
    short_name: str = field(metadata={"sa": Column(String, unique=True, index=True)})
    raffle_ticket_reward: int = field(
        default=0,
        metadata={
            "sa": Column(Integer, index=True, nullable=False, server_default=text("0"))
        },
    )
    is_random: bool = field(
        default=False,
        metadata={
            "sa": Column(Boolean, nullable=False, server_default=expression.false())
        },
    )
    random_probability: float = field(
        default=0,
        metadata={"sa": Column(Integer, nullable=False, server_default=text("0"))},
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


@mapper_registry.mapped
@dataclass
class VotePassedWaitlist:
    """
    Queue players from adding after a vote passes. This is to avoid the race
    that might happen after a vote passes, and also allow players to delete if
    they don't want to play the map that just passed.
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "vote_passed_waitlist"

    channel_id: int = field(metadata={"sa": Column(Integer, nullable=False)})
    guild_id: int = field(metadata={"sa": Column(Integer, nullable=False)})
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
class VotePassedWaitlistPlayer:
    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "vote_passed_waitlist_player"
    __table_args__ = (UniqueConstraint("player_id", "queue_id"),)

    vote_passed_waitlist_id: str = field(
        metadata={
            "sa": Column(
                String,
                ForeignKey("vote_passed_waitlist.id"),
                nullable=False,
                index=True,
            )
        }
    )
    player_id: int = field(
        metadata={"sa": Column(Integer, ForeignKey("player.id"), nullable=False)},
    )
    queue_id: str = field(metadata={"sa": Column(String, ForeignKey("queue.id"))})
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


Session: sessionmaker = sessionmaker(bind=engine)
