from typing import Protocol

import sqlalchemy
from discord import Colour, Embed, Interaction, Member, Message
from discord.ext.commands.context import Context

from discord_bots.utils import send_message

from .config import CHANNEL_ID, ECONOMY_ENABLED
from .models import AdminRole, Player, Session


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
    session: sqlalchemy.orm.Session
    with Session() as session:
        message: Message = ctx.message
        caller = (
            session.query(Player)
            .filter(Player.id == message.author.id, Player.is_admin == True)
            .first()
        )
        if caller:
            return True

        if not message.guild:
            return False

        member = message.guild.get_member(message.author.id)
        if not member:
            return False

        admin_roles = session.query(AdminRole).all()
        admin_role_ids = map(lambda x: x.role_id, admin_roles)
        member_role_ids = map(lambda x: x.id, member.roles)
        is_admin: bool = len(set(admin_role_ids).intersection(set(member_role_ids))) > 0
        if is_admin:
            return True
        else:
            await send_message(
                message.channel,
                embed_description="You must be an admin to use that command",
                colour=Colour.red(),
            )
            return False


async def is_admin_app_command(interaction: Interaction) -> bool:
    session: sqlalchemy.orm.Session
    with Session() as session:
        caller = (
            session.query(Player)
            .filter(Player.id == interaction.user.id, Player.is_admin == True)
            .first()
        )
        if caller:
            return True

        member: Member = interaction.user
        if not member:
            return False

        admin_roles = session.query(AdminRole).all()
        admin_role_ids = map(lambda x: x.role_id, admin_roles)
        member_role_ids = map(lambda x: x.id, member.roles)
        is_admin: bool = len(set(admin_role_ids).intersection(set(member_role_ids))) > 0
        if is_admin:
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


class HasName(Protocol):
    name: str
