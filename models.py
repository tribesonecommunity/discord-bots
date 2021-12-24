from dataclasses import dataclass, field
from uuid import uuid4

from sqlalchemy import Column, Integer, String, UniqueConstraint, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql.expression import null
from sqlalchemy.sql.schema import ForeignKey

"""
engine = create_engine("sqlite:///tribes.db", echo=True)
"""
# TODO: Use a test db if in test environment
engine = create_engine("sqlite:///tribes.db", echo=False)
Base = declarative_base()


@dataclass
class Game(Base):
    """
    An instance of a game played
    """

    __tablename__ = "game"

    id: str = Column(String, primary_key=True, default=lambda: str(uuid4()))
    queue_id: str = Column(String, ForeignKey("queue.id"), nullable=False)


@dataclass
class GamePlayer(Base):
    """
    A participant in a game
    """

    __tablename__ = "game_player"

    id: str = Column(String, primary_key=True, default=lambda: str(uuid4()))
    game_id: str = Column(String, ForeignKey("game.id"), nullable=False)
    player_id: int = Column(String, ForeignKey("player.id"), nullable=False)
    # team: int = Column(Integer, nullable=False)


@dataclass
class Player(Base):
    """
    id: We use the user id from discord
    """

    __tablename__ = "player"

    id: int = Column(Integer, primary_key=True)
    name: str = Column(String, nullable=False)
    # trueskill_rating: float = Column(Float, nullable=False, default=0.0)


@dataclass
class Queue(Base):
    __tablename__ = "queue"

    id: str = Column(String, primary_key=True, default=lambda: str(uuid4()))
    name: str = Column(String, unique=True, nullable=False)
    size: int = Column(Integer, nullable=False)


@dataclass
class QueuePlayer(Base):
    """
    Players currently waiting in a queue
    """

    __tablename__ = "queue_player"
    __table_args__ = (UniqueConstraint("queue_id", "player_id"),)

    id: str = Column(String, primary_key=True, default=lambda: str(uuid4()))
    queue_id: str = Column(String, ForeignKey("queue.id"), nullable=False)
    player_id: int = Column(String, ForeignKey("player.id"), nullable=False)


Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
