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
class Game:
    """
    An instance of a game played
    """

    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "game"

    queue_id: str = field(
        metadata={"sa": Column(String, ForeignKey("queue.id"), nullable=False)},
    )
    id: str = field(
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
        metadata={"sa": Column(String, ForeignKey("game.id"), nullable=False)},
    )
    player_id: int = field(
        metadata={"sa": Column(String, ForeignKey("player.id"), nullable=False)},
    )
    team: int = field(metadata={"sa": Column(Integer, nullable=False)})
    id: str = field(
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
    # trueskill_rating: float = Column(Float, nullable=False, default=0.0)


@mapper_registry.mapped
@dataclass
class Queue:
    __sa_dataclass_metadata_key__ = "sa"
    __tablename__ = "queue"

    name: str = field(metadata={"sa": Column(String, unique=True, nullable=False)})
    size: int = field(metadata={"sa": Column(Integer, nullable=False)})
    id: str = field(
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
        metadata={"sa": Column(String, ForeignKey("queue.id"), nullable=False)},
    )
    player_id: int = field(
        metadata={"sa": Column(String, ForeignKey("player.id"), nullable=False)},
    )
    id: str = field(
        default_factory=lambda: str(uuid4()),
        metadata={"sa": Column(String, primary_key=True)},
    )


Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
