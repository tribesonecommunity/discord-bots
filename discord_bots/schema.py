# Define GraphQL schema
from graphene import relay
from graphene_sqlalchemy import SQLAlchemyObjectType

from .models import FinishedGame
from .models import Player as PlayerModel


class Player(SQLAlchemyObjectType):
    class Meta:
        model = PlayerModel
        interfaces = (relay.Node,)


class Game(SQLAlchemyObjectType):
    class Meta:
        model = FinishedGame
        interfaces = (relay.Node,)
