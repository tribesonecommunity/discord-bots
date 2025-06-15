import logging
import os
import sys
from datetime import datetime, timezone
from shutil import copyfile
from typing import Literal

import discord
from discord import Colour, Embed, Interaction, Member, Role, TextChannel, app_commands
from discord.ext.commands import Bot
from discord.utils import escape_markdown
from sqlalchemy.orm.session import Session as SQLAlchemySession

import discord_bots.config as config
from discord_bots.bot import bot
from discord_bots.checks import is_admin_app_command, is_command_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.cogs.in_progress_game import InProgressGameCommands
from discord_bots.models import (
    AdminRole,
    CustomCommand,
    DiscordGuild,
    FinishedGame,
    FinishedGamePlayer,
    InProgressGame,
    Map,
    MapVote,
    Player,
    Queue,
    QueuePlayer,
    QueueWaitlistPlayer,
    Rotation,
    RotationMap,
    Session,
    SkipMapVote,
    VotePassedWaitlistPlayer,
)
from discord_bots.utils import (
    add_empty_field,
    command_autocomplete,
    del_player_from_queues_and_waitlists,
    finished_game_str,
    in_progress_game_autocomplete,
    map_short_name_autocomplete,
    print_leaderboard,
    update_next_map,
    queue_autocomplete,
)

_log = logging.getLogger(__name__)


class AdminCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(name="admin", description="Admin commands")

    @group.command(name="add", description="Add an admin")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(member="Player to be made admin")
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
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f"{escape_markdown(player.name)} added to admins",
                            colour=Colour.green(),
                        )
                    )
                    session.commit()

    @group.command(name="addrole", description="Add an admin role")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(role="Role to be made admin")
    async def addadminrole(self, interaction: Interaction, role: Role):
        if interaction.guild:
            if role not in interaction.guild.roles:
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
                            description=f"**{role.name}** is already an admin", colour=Colour.yellow()
                        ),
                        ephemeral=True,
                    )
                else:
                    session.add(AdminRole(role.id))
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f"Added admin role: {role.name}",
                            colour=Colour.green(),
                        )
                    )
                    session.commit()

    @group.command(name="ban", description="Bans player from queues")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(member="Player to be banned")
    async def ban(self, interaction: Interaction, member: Member):
        session: SQLAlchemySession
        with Session() as session:
            player: Player | None = session.query(Player).filter(Player.id == member.id).first()
            if not player:
                session.add(
                    Player(
                        id=member.id,
                        name=member.name,
                        is_banned=True,
                    )
                )
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"{escape_markdown(member.name)} banned",
                        colour=Colour.green(),
                    )
                )
            else:
                if player.is_banned:
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f"{escape_markdown(player.name)} is already banned",
                            colour=Colour.red(),
                        )
                    )
                else:
                    player.is_banned = True
                    session.query(MapVote).filter(MapVote.player_id == player.id).delete()
                    session.query(SkipMapVote).filter(SkipMapVote.player_id == player.id).delete()
                    session.query(VotePassedWaitlistPlayer).filter(
                        VotePassedWaitlistPlayer.player_id == player.id).delete()
                    session.query(QueueWaitlistPlayer).filter(QueueWaitlistPlayer.player_id == player.id).delete()
                    session.query(QueuePlayer).filter(QueuePlayer.player_id == player.id).delete()
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
    @app_commands.check(is_command_channel)
    @app_commands.guild_only()
    async def configure(self, interaction: Interaction):
        assert interaction.guild

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
                await interaction.response.send_message(
                    embed=Embed(
                        description="Server configured successfully!",
                        colour=Colour.green(),
                    ),
                    ephemeral=True,
                )
                session.commit()

    @group.command(description="Create or Edit a custom command")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(name="New or Existing command name")
    @app_commands.autocomplete(name=command_autocomplete)
    async def customcommand(self, interaction: Interaction, name: str):
        session: SQLAlchemySession
        with Session() as session:
            custom_command_modal = CustomCommandModal(name, session)
            await interaction.response.send_modal(custom_command_modal)
            await custom_command_modal.wait()

    @group.command(
        name="createdbbackup", description="Creates a backup of the database"
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    async def createdbbackup(self, interaction: Interaction):
        # Only functions for SQLite
        # TODO: Covert to work for Postgres

        # Check for Postgres URI
        if config.DATABASE_URI:
            await interaction.response.send_message(
                embed=Embed(
                    description="This command does not support Postgres databases",
                    colour=Colour.yellow(),
                ),
                ephemeral=True
            )
            return

        date_string = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        copyfile(f"{config.DB_NAME}.db", f"{config.DB_NAME}_{date_string}.db")
        await interaction.response.send_message(
            embed=Embed(
                description=f"Backup made to {config.DB_NAME}_{date_string}.db",
                colour=Colour.green(),
            ),
            ephemeral=True,
        )

    @group.command(name="deletegame", description="Deletes a finished game")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(game_id="Finished game id")
    async def deletegame(self, interaction: Interaction, game_id: str):
        session: SQLAlchemySession
        with Session() as session:
            finished_game: FinishedGame | None = (
                session.query(FinishedGame)
                .filter(FinishedGame.game_id.startswith(game_id))
                .first()
            )
            if not finished_game:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find game: {game_id}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            session.query(FinishedGamePlayer).filter(
                FinishedGamePlayer.finished_game_id == finished_game.id
            ).delete()
            session.delete(finished_game)
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Game: **{finished_game.game_id}** deleted",
                    colour=Colour.green(),
                )
            )
            session.commit()

    @group.command(
        name="delplayer", description="Admin command to delete player from all queues"
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(member="Player to be removed from queues")
    async def delplayer(
        self,
        interaction: Interaction,
        member: Member,
    ):
        """
        Admin command to delete player from all queues
        """
        embed = discord.Embed()
        session: SQLAlchemySession
        with Session() as session:
            queues_del_from = del_player_from_queues_and_waitlists(session, member.id)
            for queue in queues_del_from:
                # TODO: generify this into queue status util
                players_in_queue = (
                    session.query(Player)
                    .join(QueuePlayer)
                    .filter(QueuePlayer.queue_id == queue.id)
                    .order_by(QueuePlayer.added_at.asc())
                    .all()
                )
                player_names: list[str] = [player.name for player in players_in_queue]
                queue_title_str = f"(**{queue.ordinal}**) {queue.name} [{len(player_names)}/{queue.size}]"
                newline = "\n"
                embed.add_field(
                    name=queue_title_str,
                    value=(
                        f">>> {newline.join(player_names)}"
                        if player_names
                        else "> \n** **"  # creates an empty quote
                    ),
                    inline=True,
                )
            if queues_del_from:
                embed.description = f"**{member.display_name}** removed from **{', '.join([queue.name for queue in queues_del_from])}**"
                embed.color = discord.Color.green()
                add_empty_field(embed)
                await interaction.response.send_message(embed=embed)
                session.commit()
            else:
                embed.description = f"**{member.display_name}** is not in any queues"
                embed.color = discord.Color.yellow()
                await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(
        name="editgamewinner", description="Edit the winner of a finished game"
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(game_id="Finished game id", outcome="Tie, BE, DS")
    async def editgamewinner(
        self,
        interaction: Interaction,
        game_id: str,
        outcome: Literal["Tie", "BE", "DS"],
    ):
        # TODO: Move away from BE/DS to Team0/Team1
        session: SQLAlchemySession
        with Session() as session:
            game: FinishedGame | None = (
                session.query(FinishedGame)
                .filter(FinishedGame.game_id.startswith(game_id))
                .first()
            )
            if not game:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find game: {game_id}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            outcome_lower = outcome.lower()
            if outcome_lower == "tie":
                game.winning_team = -1
            elif outcome_lower == "be":
                game.winning_team = 0
            elif outcome_lower == "ds":
                game.winning_team = 1
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description="Outcome must be tie, be, or ds",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            session.add(game)
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Game {game_id} outcome changed:\n\n"
                    + finished_game_str(game),
                    colour=Colour.green(),
                )
            )
            session.commit()

    @group.command(name="remove", description="Remove an admin")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(member="Player to be removed as admin")
    async def removeadmin(self, interaction: Interaction, member: Member):
        session: SQLAlchemySession
        with Session() as session:
            player: Player | None = session.query(Player).filter(Player.id == member.id).first()
            if not player or not player.is_admin:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"{escape_markdown(member.name)} is not an admin",
                        colour=Colour.red(),
                    )
                )
                return

            player.is_admin = False
            await interaction.response.send_message(
                embed=Embed(
                    description=f"{escape_markdown(member.name)} removed from admins",
                    colour=Colour.green(),
                )
            )
            session.commit()

    @group.command(name="removerole", description="Remove an admin role")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.guild_only()
    @app_commands.describe(role="Role to be removed as admin")
    async def removeadminrole(self, interaction: Interaction, role: Role):
        assert interaction.guild

        if role not in interaction.guild.roles:
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
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Removed admin role: {role.name}",
                        colour=Colour.green(),
                    )
                )
                session.commit()
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
    @app_commands.check(is_command_channel)
    @app_commands.describe(name="Name of existing custom command")
    @app_commands.autocomplete(name=command_autocomplete)
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
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Command `{name}` removed",
                    colour=Colour.green(),
                ),
                ephemeral=True,
            )
            session.commit()
            _log.info(
                f"[removecommand] Command {name} removed by {interaction.user.name} ({interaction.user.id})"
            )

    @group.command(name="removedbbackup", description="Remove a database backup")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(db_filename="Name of backup file")
    async def removedbbackup(self, interaction: Interaction, db_filename: str):
        # Only functions for SQLite
        # TODO: Covert to work for Postgres

        # Check for Postgres URI
        if config.DATABASE_URI:
            await interaction.response.send_message(
                embed=Embed(
                    description="This command does not support Postgres databases",
                    colour=Colour.yellow(),
                ),
                ephemeral=True
            )
            return


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
    @app_commands.check(is_command_channel)
    async def restart(self, interaction: Interaction):
        await interaction.response.send_message(
            embed=Embed(
                description="Restarting bot...",
                colour=Colour.blue(),
            )
        )
        os.execv(sys.executable, ["python", "-m", "discord_bots.main"])

    @group.command(name="setbias", description="Set team bias")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(member="Member to bias", amount="Bias value")
    async def setbias(self, interaction: Interaction, member: Member, amount: float):
        if amount < -100 or amount > 100:
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Amount must be between -100 and 100",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=Embed(
                description=f"Team bias for {member.name} set to `{amount}%`",
                colour=Colour.green(),
            )
        )

    @group.command(name="setcaptainbias", description="Set captain bias")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(member="Member to bias", amount="Bias value")
    async def setcaptainbias(
        self, interaction: Interaction, member: Member, amount: float
    ):
        if amount < -100 or amount > 100:
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Amount must be between -100 and 100",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=Embed(
                description=f"Captain bias for {member.name} set to `{amount}%`",
                colour=Colour.green(),
            )
        )

    @group.command(
        name="setcommandprefix", description="Sets the prefix for context commands"
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(prefix="New command prefix")
    async def setcommandprefix(self, interaction: Interaction, prefix: str):
        # TODO move to db-config
        global COMMAND_PREFIX
        COMMAND_PREFIX = prefix
        await interaction.response.send_message(
            embed=Embed(
                description=f"Command prefix set to {COMMAND_PREFIX}",
                colour=Colour.green(),
            )
        )

    @group.command(name="unban", description="Unban player")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(member="Player to be unbanned")
    async def unban(self, interaction: Interaction, member: Member):
        session: SQLAlchemySession
        with Session() as session:
            player: Player | None = session.query(Player).filter(Player.id == member.id).first()
            if not player or not player.is_banned:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"{escape_markdown(member.name)} is not banned",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            player.is_banned = False
            await interaction.response.send_message(
                embed=Embed(
                    description=f"{escape_markdown(member.name)} unbanned",
                    colour=Colour.green(),
                )
            )
            session.commit()

    @group.command(
        name="resetleaderboard", description="Resets & updates the leaderboards"
    )
    @app_commands.check(is_command_channel)
    @app_commands.check(is_admin_app_command)
    async def resetleaderboardchannel(self, interaction: Interaction):
        if not config.LEADERBOARD_CHANNEL:
            await interaction.response.send_message(
                "Leaderboard channel ID not configured", ephemeral=True
            )
            return
        channel = bot.get_channel(config.LEADERBOARD_CHANNEL)
        if not isinstance(channel, TextChannel):
            await interaction.response.send_message(
                "Could not find leaderboard channel, check ID", ephemeral=True
            )
            return
        try:
            await interaction.response.defer(ephemeral=True)
            await channel.purge()
            await print_leaderboard()
        except:
            _log.exception(
                "[resetleaderboardchannel] Leaderboard failed to reset due to:"
            )
            await interaction.followup.send(
                embed=Embed(
                    description="Leaderboard failed to reset",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=Embed(
                    description=f"{channel.jump_url} reset",
                    colour=Colour.green(),
                ),
                ephemeral=True,
            )

    @group.command(description="Set the map for a game")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(game_id="In progress game id", map_short_name="Map name")
    @app_commands.autocomplete(
        game_id=in_progress_game_autocomplete,
        map_short_name=map_short_name_autocomplete,
    )
    @app_commands.rename(game_id="game", map_short_name="map")
    async def setgamemap(
        self, interaction: Interaction, game_id: str, map_short_name: str
    ):
        """
        Change the map for a game
        TODO: tests
        """
        session: SQLAlchemySession
        with Session() as session:
            ipg = (
                session.query(InProgressGame)
                .filter(InProgressGame.id.startswith(game_id))
                .first()
            )
            finished_game = (
                session.query(FinishedGame)
                .filter(FinishedGame.game_id.startswith(game_id))
                .first()
            )
            game: InProgressGame | FinishedGame
            if ipg:
                game = ipg
            elif finished_game:
                game = finished_game
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find game: **{game_id}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            map: Map | None = (
                session.query(Map).filter(Map.short_name.ilike(map_short_name)).first()
            )
            if not map:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find map: **{map_short_name}**. Add to map pool first.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            game.map_id = map.id
            game.map_full_name = map.full_name
            game.map_short_name = map.short_name
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Map for game **{game_id}** changed to **{map.short_name}**",
                    colour=Colour.green(),
                )
            )

    @group.command(
        description="Set the map for a queue (note: affects all queues sharing the same rotation)",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Queue Name", map_short_name="Map Name")
    @app_commands.rename(queue_name="queue", map_short_name="map")
    @app_commands.autocomplete(
        queue_name=queue_autocomplete, map_short_name=map_short_name_autocomplete
    )
    async def setqueuemap(
        self, interaction: Interaction, queue_name: str, map_short_name: str
    ):
        """
        Change the next map for a queue (note: affects all queues sharing that rotation)
        TODO: tests
        """

        session: SQLAlchemySession
        with Session() as session:
            queue: Queue | None = (
                session.query(Queue).filter(Queue.name.ilike(queue_name)).first()
            )
            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue: **{queue_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            map: Map | None = (
                session.query(Map).filter(Map.short_name.ilike(map_short_name)).first()
            )
            if not map:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find map: **{map_short_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            rotation: Rotation | None = (
                session.query(Rotation).filter(queue.rotation_id == Rotation.id).first()
            )
            if not rotation:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"**{queue.name}** has not been assigned a rotation.\nPlease assign one with `/setqueuerotation`.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            next_rotation_map: RotationMap | None = (
                session.query(RotationMap)
                .filter(
                    RotationMap.rotation_id == rotation.id, RotationMap.map_id == map.id
                )
                .first()
            )
            if not next_rotation_map:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"The rotation for **{queue.name}** doesn't have that map.\nPlease add it to the **{rotation.name}** rotation with `/addrotationmap`.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            await update_next_map(rotation.id, next_rotation_map.id)
            await interaction.response.send_message(
                embed=Embed(
                    description="Map changed successfully",
                    colour=Colour.green(),
                ),
                ephemeral=True
            )

    @group.command(name="cancel", description="Cancels the specified game")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(game_id="The game ID")
    @app_commands.autocomplete(game_id=in_progress_game_autocomplete)
    @app_commands.rename(game_id="game")
    @app_commands.guild_only()
    async def cancelgame(self, interaction: Interaction, game_id: str):
        in_progress_game_cog = self.bot.get_cog("InProgressGameCommands")
        if not isinstance(in_progress_game_cog, InProgressGameCommands):
            _log.error(f"[cancelgame] InProgressGameCommands cog is not loaded")
            await interaction.response.send_message(
                embed=Embed(
                    description=f"This funcationality is not enabled, please contact the bot owner",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        else:
            await in_progress_game_cog.cancelgame_callback(interaction, game_id)


class CustomCommandModal(discord.ui.Modal):
    """
    Sends a discord.ui.Modal that handles both Creating/Editing custom commands
    For existing commands, the existing output will be autofilled to allow easy editing.
    """

    def __init__(self, name: str, session: SQLAlchemySession):
        super().__init__(
            title=f"Custom Command {config.COMMAND_PREFIX}{name}", timeout=None
        )
        self.session = session
        self.name = name
        self.custom_command: CustomCommand | None = (
            self.session.query(CustomCommand)
            .filter(CustomCommand.name == self.name)
            .first()
        )
        self.form = discord.ui.TextInput(
            style=discord.TextStyle.long,
            label="Command Ouput",
            placeholder="Enter the command output here...",
            default=self.custom_command.output if self.custom_command else None,
            required=True,
        )
        self.add_item(self.form)

    async def on_submit(self, interaction: Interaction) -> None:
        if self.custom_command:
            self.custom_command.output = self.form.value
            embed_description: str = (
                f"Command `{config.COMMAND_PREFIX}{self.name}` updated!"
            )
        else:
            self.custom_command = CustomCommand(name=self.name, output=self.form.value)
            self.session.add(self.custom_command)
            embed_description: str = (
                f"Command `{config.COMMAND_PREFIX}{self.name}` added!"
            )
        self.session.commit()
        await interaction.response.send_message(
            embed=Embed(description=embed_description, colour=Colour.green()),
            ephemeral=True,
        )

    async def on_error(
        self,
        interaction: Interaction,
        error: Exception,
    ) -> None:
        self.session.rollback()
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=Embed(
                    description="Oops! Something went wrong ☹️",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        else:
            # fallback case that responds to the interaction, since there always needs to be a response
            await interaction.response.send_message(
                embed=Embed(
                    description="Oops! Something went wrong ☹️",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        await super().on_error(interaction, error)
