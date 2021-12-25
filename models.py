from dataclasses import dataclass, field
from uuid import uuid4

from sqlalchemy import Column, Integer, String, UniqueConstraint, create_engine
from sqlalchemy.orm import registry, sessionmaker
from sqlalchemy.sql.schema import ForeignKey

"""
engine = create_engine("sqlite:///tribes.db", echo=True)
"""
# TODO: Use a test db if in test environment
engine = create_engine("sqlite:///tribes.db", echo=False)
mapper_registry = registry()
Base = mapper_registry.generate_base()


"""
Sorry if this looks confusing. Models are declared using the method here: https://docs.sqlalchemy.org/en/14/orm/mapping_styles.html#example-two-dataclasses-with-declarative-table

This lets us mix Python dataclasses with SQLAlchemy. We get the conveniences of dataclasses
without needing to declare the table schema twice. It does add some boilerplate.
"""


@mapper_registry.mapped
@dataclass
class Admin:
    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "admin"

    player_id: int = field(
        metadata={
            "sa": Column(String, ForeignKey("player.id"), nullable=False, index=True)
        },
    )
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


@mapper_registry.mapped
@dataclass
class BannedPlayer:
    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "banned_player"

    player_id: int = field(
        metadata={
            "sa": Column(String, ForeignKey("player.id"), nullable=False, index=True)
        },
    )
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


@mapper_registry.mapped
@dataclass
class Game:
    """
    A game either in progress or already completed

    winning_team: null means in progress, -1 means in draw, 0 means team 0, 1
    means team 1. this is so that it's easy to find the other team with (team + 1) % 2
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "game"

    queue_id: str = field(
        metadata={
            "sa": Column(String, ForeignKey("queue.id"), nullable=False, index=True)
        },
    )
    winning_team: int = field(
        init=False,
        metadata={"sa": Column(Integer, index=True)},
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
            "sa": Column(String, ForeignKey("player.id"), nullable=False, index=True)
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
            "sa": Column(String, ForeignKey("player.id"), nullable=False, index=True)
        },
    )
    id: str = field(
        init=False,
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
