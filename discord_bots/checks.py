from typing import Protocol

import sqlalchemy
from discord import Colour, Embed, Interaction, Member, Message
from discord.ext.commands.context import Context

from discord_bots.utils import send_message

from . import config
from .config import CHANNEL_ID, ECONOMY_ENABLED
from .models import AdminRole, Config, Player, Session

_captain_channel_id_cache: int | None = None
_captain_channel_id_cache_loaded: bool = False
_ladder_channel_id_cache: int | None = None
_ladder_channel_id_cache_loaded: bool = False


def _load_captain_channel_id_cache() -> None:
    global _captain_channel_id_cache, _captain_channel_id_cache_loaded
    with Session() as session:
        config_row = session.query(Config).first()
        _captain_channel_id_cache = (
            config_row.captain_channel_id if config_row else None
        )
    _captain_channel_id_cache_loaded = True


def get_cached_captain_channel_id() -> int | None:
    if not _captain_channel_id_cache_loaded:
        _load_captain_channel_id_cache()
    return _captain_channel_id_cache


def update_captain_channel_id_cache(value: int | None) -> None:
    global _captain_channel_id_cache, _captain_channel_id_cache_loaded
    _captain_channel_id_cache = value
    _captain_channel_id_cache_loaded = True


def _load_ladder_channel_id_cache() -> None:
    global _ladder_channel_id_cache, _ladder_channel_id_cache_loaded
    with Session() as session:
        config_row = session.query(Config).first()
        _ladder_channel_id_cache = config_row.ladder_channel_id if config_row else None
    _ladder_channel_id_cache_loaded = True


def get_cached_ladder_channel_id() -> int | None:
    if not _ladder_channel_id_cache_loaded:
        _load_ladder_channel_id_cache()
    return _ladder_channel_id_cache


def update_ladder_channel_id_cache(value: int | None) -> None:
    global _ladder_channel_id_cache, _ladder_channel_id_cache_loaded
    _ladder_channel_id_cache = value
    _ladder_channel_id_cache_loaded = True


def queue_is_captain_pick_for_channel(channel_id: int) -> bool:
    """
    Whether queues visible from `channel_id` should be captain-pick queues.
    True if `channel_id` is the registered captain channel; False otherwise.
    Use this to derive a Queue.is_captain_pick filter from the invocation
    channel.
    """
    captain_channel_id = get_cached_captain_channel_id()
    return captain_channel_id is not None and channel_id == captain_channel_id


def __has_admin_role(user_id: int, member: Member) -> bool:
    session: sqlalchemy.orm.Session
    with Session() as session:
        has_admin_priviledge = (
            session.query(Player)
            .filter(Player.id == user_id, Player.is_admin == True)
            .first()
        )
        if has_admin_priviledge:
            return True

        if not member:
            return False

        admin_roles = session.query(AdminRole).all()
        admin_role_ids = map(lambda x: x.role_id, admin_roles)
        member_role_ids = map(lambda x: x.id, member.roles)
        has_admin_role: bool = (
            len(set(admin_role_ids).intersection(set(member_role_ids))) > 0
        )
        return has_admin_role


async def economy_enabled(interaction: Interaction) -> bool:
    """
    Check to wrap functions that require player economy to be enabled
    """

    if not interaction:
        return False

    if not ECONOMY_ENABLED:
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=Embed(
                    description="Player economy is disabled", colour=Colour.red()
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=Embed(
                    description="Player economy is disabled", colour=Colour.red()
                ),
                ephemeral=True,
            )
        return False
    else:
        return True


async def is_admin(ctx: Context):
    """
    Check to wrap functions that require admin

    https://discordpy.readthedocs.io/en/stable/ext/commands/commands.html#global-checks
    """
    member = (
        None
        if not ctx.message.guild
        else ctx.message.guild.get_member(ctx.message.author.id)
    )
    if __has_admin_role(ctx.message.author.id, member):
        return True
    else:
        await send_message(
            ctx.message.channel,
            embed_description="You must be an admin to use that command",
            colour=Colour.red(),
        )
        return False


async def is_admin_app_command(interaction: Interaction) -> bool:
    if __has_admin_role(interaction.user.id, interaction.user):
        return True
    else:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=Embed(
                    description="You must be an admin to use that command",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=Embed(
                    description="You must be an admin to use that command",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        return False


async def is_mock_user_app_command(interaction: Interaction) -> bool:
    if interaction.user.id in config.MOCK_COMMAND_USERS:
        return True
    else:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=Embed(
                    description="You must be a mock command user to use that command",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=Embed(
                    description="You must be a mock command user to use that command",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        return False


async def is_command_channel(interaction: Interaction) -> bool:
    """
    Check that interactions are performed from the command channel
    """
    if not interaction.channel or interaction.channel.id != CHANNEL_ID:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=Embed(
                    description="Interactions must be performed from the command channel",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=Embed(
                    description="Interactions must be performed from the command channel",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        return False
    else:
        return True


async def is_command_or_captain_channel(interaction: Interaction) -> bool:
    """
    Check that interactions are performed from the command channel or the
    registered captain pick channel (Config.captain_channel_id).
    """
    if interaction.channel:
        captain_channel_id = get_cached_captain_channel_id()
        if interaction.channel.id == CHANNEL_ID or (
            captain_channel_id is not None
            and interaction.channel.id == captain_channel_id
        ):
            return True

    description = "Interactions must be performed from the command channel"
    if get_cached_captain_channel_id() is not None:
        description += " or the captain channel"
    embed = Embed(description=description, colour=Colour.red())
    if not interaction.response.is_done():
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.followup.send(embed=embed, ephemeral=True)
    return False


async def is_ladder_channel(interaction: Interaction) -> bool:
    """
    Check that interactions are performed from the registered ladder channel
    (Config.ladder_channel_id). Returns False with a friendly error if no
    ladder channel is configured.
    """
    ladder_channel_id = get_cached_ladder_channel_id()
    if (
        ladder_channel_id is not None
        and interaction.channel
        and interaction.channel.id == ladder_channel_id
    ):
        return True

    if ladder_channel_id is None:
        description = (
            "No ladder channel is configured. An admin must set one with "
            "`/config setladderchannel`."
        )
    else:
        description = "Ladder commands must be used in the ladder channel."
    embed = Embed(description=description, colour=Colour.red())
    if not interaction.response.is_done():
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.followup.send(embed=embed, ephemeral=True)
    return False


class HasName(Protocol):
    name: str
