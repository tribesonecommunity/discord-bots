from discord.ext.commands import Bot, Context, check, command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound

from discord_bots.checks import is_admin
from discord_bots.cogs.base import BaseCog
from discord_bots.models import Map, Rotation, RotationMap