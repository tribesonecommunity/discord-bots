from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from glob import glob
from shutil import copyfile
from sqlalchemy.orm.session import Session as SQLAlchemySession
from typing import List, Literal, Optional, TYPE_CHECKING

from discord import (
    app_commands,
    Colour,
    Embed,
    Guild,
    HTTPException,
    Interaction,
    Member,
    Object,
    Role,
)
from discord.ext import commands
from discord.ext.commands import Bot, Cog, Context, Greedy
from discord.ui.item import Item
from discord.utils import escape_markdown

import discord_bots.config as config
from discord_bots.bot import bot
from discord_bots.checks import is_admin_app_command
from discord_bots.cogs.base import BaseCog
from discord_bots.models import (
    AdminRole,
    CustomCommand,
    DiscordGuild,
    Player,
    Session,
)
from discord_bots.utils import send_message


if TYPE_CHECKING:
    from typing import Any, Type


_log = logging.getLogger(__name__)


class AdminCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)
        self.bot: Bot = bot

    group = app_commands.Group(name="admin", description="Admin related commands;")

    @group.command(name="addadmin", description="Add an admin")
    @app_commands.check(is_admin_app_command)
    async def addadmin(self, interaction: Interaction, member: Member):
        session: SQLAlchemySession
        with Session() as session:
            player: Player | None = (
                session.query(Player).filter(Player.id == member.id).first()
            )
            if not player:
                session.add(
                    Player(
                        id=member.id,
                        name=member.name,
                        is_admin=True,
                    )
                )
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"{escape_markdown(member.name)} added to admins",
                        colour=Colour.green(),
                    )
                )
                session.commit()
            else:
                if player.is_admin:
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f"{escape_markdown(player.name)} is already an admin",
                            colour=Colour.red(),
                        )
                    )
                else:
                    player.is_admin = True
                    session.commit()
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f"{escape_markdown(player.name)} added to admins",
                            colour=Colour.red(),
                        )
                    )

    @group.command(name="addrole", description="Add an admin role")
    @app_commands.check(is_admin_app_command)
    async def addadminrole(self, interaction: Interaction, role: Role):
        if interaction.guild:
            if not role in interaction.guild.roles:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find role: {role.name}",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

            session: SQLAlchemySession
            with Session() as session:
                admin_role: AdminRole | None = (
                    session.query(AdminRole)
                    .filter(AdminRole.role_id == role.id)
                    .first()
                )
                if admin_role:
                    await interaction.response.send_message(
                        embed=Embed(
                            description="Role is already an admin", colour=Colour.blue()
                        ),
                        ephemeral=True,
                    )
                else:
                    session.add(AdminRole(role.id))
                    session.commit()
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f"Added admin role: {role.name}",
                            colour=Colour.green(),
                        )
                    )

    @group.command(name="ban", description="Bans player from queues")
    @app_commands.check(is_admin_app_command)
    async def ban(self, interaction: Interaction, member: Member):
        """TODO: remove player from queues"""
        session: SQLAlchemySession
        with Session() as session:
            players = session.query(Player).filter(Player.id == member.id).all()
            if len(players) == 0:
                session.add(
                    Player(
                        id=member.id,
                        name=member.name,
                        is_banned=True,
                    )
                )
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"{escape_markdown(member.name)} banned",
                        colour=Colour.green(),
                    )
                )
                session.commit()
            else:
                player = players[0]
                if player.is_banned:
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f"{escape_markdown(player.name)} is already banned",
                            colour=Colour.red(),
                        )
                    )
                else:
                    player.is_banned = True
                    session.commit()
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f"{escape_markdown(player.name)} banned",
                            colour=Colour.green(),
                        )
                    )

    @group.command(
        name="configure", description="Initially configure the bot for this server"
    )
    @app_commands.check(is_admin_app_command)
    async def configure(self, interaction: Interaction):
        if interaction.guild:
            session: SQLAlchemySession
            with Session() as session:
                guild = (
                    session.query(DiscordGuild)
                    .filter(DiscordGuild.discord_id == interaction.guild.id)
                    .first()
                )
                if guild:
                    await interaction.response.send_message(
                        embed=Embed(
                            description="Server already configured",
                            colour=Colour.red(),
                        ),
                        ephemeral=True,
                    )
                else:
                    guild = DiscordGuild(interaction.guild.id, interaction.guild.name)
                    session.add(guild)
                    session.commit()
                    await interaction.response.send_message(
                        embed=Embed(
                            description="Server configured successfully!",
                            colour=Colour.green(),
                        ),
                        ephemeral=True,
                    )
        else:
            await interaction.response.send_message(
                embed=Embed(
                    description="Command must be run from within a guild",
                    colour=Colour.blue(),
                ),
                ephemeral=True,
            )

    @group.command(name="createcommand", description="Create a custom command")
    @app_commands.check(is_admin_app_command)
    async def createcommand(self, interaction: Interaction, name: str, *, output: str):
        session: SQLAlchemySession
        with Session() as session:
            exists = (
                session.query(CustomCommand).filter(CustomCommand.name == name).first()
            )
            if exists is not None:
                await interaction.response.send_message(
                    embed=Embed(
                        description="A command with that name already exists",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            session.add(CustomCommand(name, output))
            session.commit()

        await interaction.response.send_message(
            embed=Embed(description=f"Command `{name}` added", colour=Colour.green())
        )

    @group.command(
        name="createdbbackup", description="Creates a backup of the database"
    )
    @app_commands.check(is_admin_app_command)
    async def createdbbackup(self, interaction: Interaction):
        date_string = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        copyfile(f"{config.DB_NAME}.db", f"{config.DB_NAME}_{date_string}.db")
        await interaction.response.send_message(
            embed=Embed(
                description=f"Backup made to {config.DB_NAME}_{date_string}.db",
                colour=Colour.green(),
            ),
            ephemeral=True,
        )

    @group.command(name="editcommand", description="Edit a custom command")
    @app_commands.check(is_admin_app_command)
    async def editcommand(self, interaction: Interaction, name: str, *, output: str):
        session: SQLAlchemySession
        with Session() as session:
            exists = (
                session.query(CustomCommand).filter(CustomCommand.name == name).first()
            )
            if exists is None:
                await interaction.response.send_message(
                    embed=Embed(
                        description="Could not find a command with that name",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            exists.output = output
            session.commit()

        await interaction.response.send_message(
            embed=Embed(
                description=f"Command `{name}` updated",
                colour=Colour.green(),
            )
        )

    @group.command(name="listadmins", description="List admin users")
    async def listadmins(self, interaction: Interaction):
        output = "Admins:"
        session: SQLAlchemySession
        with Session() as session:
            player: Player
            for player in session.query(Player).filter(Player.is_admin == True).all():
                output += f"\n- {escape_markdown(player.name)}"

        await interaction.response.send_message(
            embed=Embed(description=output, colour=Colour.blue())
        )

    @group.command(name="listbans", description="List banned players")
    async def listbans(self, interaction: Interaction):
        output = "Bans:"
        session: SQLAlchemySession
        with Session() as session:
            for player in session.query(Player).filter(Player.is_banned == True):
                output += f"\n- {escape_markdown(player.name)}"
        await interaction.response.send_message(
            embed=Embed(
                description=output,
                colour=Colour.blue(),
            )
        )
        await send_message(
            message.channel, embed_description=output, colour=Colour.blue()
        )

    @group.command(name="listchannels", description="List bot channels")
    @app_commands.check(is_admin_app_command)
    async def listchannels(self, interaction: Interaction):
        for channel in bot.get_all_channels():
            _log.info(channel.id, channel)  # DEBUG, TRACE?

        await interaction.response.send_message(
            embed=Embed(
                description="Check the logs",
                colour=Colour.blue(),
            ),
            ephemeral=True,
        )

    @group.command(name="listdbbackups", description="List database backups")
    @app_commands.check(is_admin_app_command)
    async def listdbbackups(self, interaction: Interaction):
        output = "Backups:"
        for filename in glob(f"{config.DB_NAME}_*.db"):
            output += f"\n- {filename}"

        await interaction.response.send_message(
            embed=Embed(
                description=output,
                colour=Colour.blue(),
            )
        )

    @group.command(name="listroles", description="List admin roles")
    async def listadminroles(self, interaction: Interaction):
        output = "Admin roles:"
        session: SQLAlchemySession
        if not interaction.guild:
            return

        with Session() as session:
            admin_role_ids = list(
                map(lambda x: x.role_id, session.query(AdminRole).all())
            )
        admin_role_names: list[str] = []

        role_id_to_role_name: dict[int, str] = {
            role.id: role.name for role in interaction.guild.roles
        }

        for admin_role_id in admin_role_ids:
            if admin_role_id in role_id_to_role_name:
                admin_role_names.append(role_id_to_role_name[admin_role_id])
        output += f"\n{', '.join(admin_role_names)}"

        await interaction.response.send_message(
            embed=Embed(description=output, colour=Colour.blue())
        )

    @group.command(name="removeadmin", description="Remove an admin")
    @app_commands.check(is_admin_app_command)
    async def removeadmin(self, interaction: Interaction, member: Member):
        session: SQLAlchemySession
        with Session() as session:
            players = session.query(Player).filter(Player.id == member.id).all()
            if len(players) == 0 or not players[0].is_admin:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"{escape_markdown(member.name)} is not an admin",
                        colour=Colour.red(),
                    )
                )
                return

            players[0].is_admin = False
            session.commit()
        await interaction.response.send_message(
            embed=Embed(
                description=f"{escape_markdown(member.name)} removed from admins",
                colour=Colour.green(),
            )
        )

    @group.command(name="removerole", description="Remove an admin role")
    @app_commands.check(is_admin_app_command)
    async def removeadminrole(self, interaction: Interaction, role: Role):
        if interaction.guild:
            if not role in interaction.guild.roles:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find role: {role.name}",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

            session: SQLAlchemySession
            with Session() as session:
                admin_role = (
                    session.query(AdminRole)
                    .filter(AdminRole.role_id == role.id)
                    .first()
                )
                if admin_role:
                    session.delete(admin_role)
                    session.commit()
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f"Removed admin role: {role.name}",
                            colour=Colour.green(),
                        )
                    )
                else:
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f"Could not find admin role: {role.name}",
                            colour=Colour.blue(),
                        ),
                        ephemeral=True,
                    )

    @group.command(name="removecommand", description="Remove a custom command")
    @app_commands.check(is_admin_app_command)
    async def removecommand(self, interaction: Interaction, name: str):
        session: SQLAlchemySession
        with Session() as session:
            exists = (
                session.query(CustomCommand).filter(CustomCommand.name == name).first()
            )
            if not exists:
                await interaction.response.send_message(
                    embed=Embed(
                        description="Could not find command with that name",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            session.delete(exists)
            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=f"Command `{name}` removed",
                    colour=Colour.green(),
                )
            )

    @group.command(name="removedbbackup", description="Remove a database backup")
    @app_commands.check(is_admin_app_command)
    async def removedbbackup(self, interaction: Interaction, db_filename: str):
        if not db_filename.startswith(config.DB_NAME) or not db_filename.endswith(
            ".db"
        ):
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Filename must be of the format {config.DB_NAME}_{{date}}.db",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        try:
            os.remove(db_filename)
        except Exception as e:
            _log.exception(f"Caught Exception in removedbbackup: {e}")
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Failed to remove DB Backup {db_filename}",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=Embed(
                    description=f"DB backup {db_filename} removed",
                    colour=Colour.green(),
                )
            )

    @group.command(name="restart", description="Restart the bot")
    @app_commands.check(is_admin_app_command)
    async def restart(self, interaction: Interaction):
        await interaction.response.send_message(
            embed=Embed(
                description="Restarting bot...",
                colour=Colour.blue(),
            )
        )
        os.execv(sys.executable, ["python", "-m", "discord_bots.main"])

    @group.command(name="unban", description="Unban player")
    @app_commands.check(is_admin_app_command)
    async def unban(self, interaction: Interaction, member: Member):
        session: SQLAlchemySession
        with Session() as session:
            players = session.query(Player).filter(Player.id == member.id).all()
            if len(players) == 0 or not players[0].is_banned:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"{escape_markdown(member.name)} is not banned",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            players[0].is_banned = False
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"{escape_markdown(member.name)} unbanned",
                    colour=Colour.green(),
                )
            )
