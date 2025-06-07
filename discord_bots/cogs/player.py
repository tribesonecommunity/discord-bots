import logging
from random import choice
from typing import List

from discord import Colour, Embed, Interaction, Member, TextChannel, app_commands
from discord.ext.commands import Bot
from discord.utils import escape_markdown
from sqlalchemy import func
from sqlalchemy.orm.session import Session as SQLAlchemySession
from sqlalchemy.sql import select

from discord_bots import config
from discord_bots.bot import bot
from discord_bots.checks import is_admin_app_command, is_command_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.config import ENABLE_VOICE_MOVE, LEADERBOARD_CHANNEL
from discord_bots.models import (
    Commend,
    FinishedGame,
    FinishedGamePlayer,
    Player,
    PlayerCategoryTrueskill,
    Session,
)

_log = logging.getLogger(__name__)


class PlayerCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(name="player", description="Player commands")

    @group.command(name="commend", description="Commend player")
    @app_commands.check(is_command_channel)
    @app_commands.describe(member="Player to be commended")
    async def commend(self, interaction: Interaction, member: Member):
        session: SQLAlchemySession
        with Session() as session:
            commender: Player | None = (
                session.query(Player).filter(Player.id == interaction.user.id).first()
            )
            if not commender:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"<@{interaction.user.id} is not a player",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            commendee: Player | None = (
                session.query(Player).filter(Player.id == member.id).first()
            )
            if not commendee:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"<@{member.id} is not a player",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            emoji = choice(["🔨", "💥", "🤕", "🤌"])
            if interaction.user.id == member.id:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"{emoji}  **BONK**  {emoji}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            if not commendee:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find {escape_markdown(member.name)}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            last_finished_game: FinishedGame | None = (
                session.query(FinishedGame)
                .join(FinishedGamePlayer)
                .filter(FinishedGamePlayer.player_id == commender.id)
                .order_by(FinishedGame.finished_at.desc())
                .first()
            )
            if not last_finished_game:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find last game played for {escape_markdown(member.name)}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            has_commend = (
                session.query(Commend)
                .filter(
                    Commend.finished_game_id == last_finished_game.id,
                    Commend.commender_id == commender.id,
                )
                .first()
            )
            if has_commend is not None:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"You already commended someone for this game",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            players_in_last_game = (
                session.query(FinishedGamePlayer)
                .filter(FinishedGamePlayer.finished_game_id == last_finished_game.id)
                .all()
            )
            player_ids = set(map(lambda x: x.player_id, players_in_last_game))
            if commendee.id not in player_ids:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"{escape_markdown(commendee.name)} was not in your last game",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            session.add(
                Commend(
                    last_finished_game.id,
                    commender.id,
                    commender.name,
                    commendee.id,
                    commendee.name,
                )
            )
            commender.raffle_tickets += 1
            session.add(commender)
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"⭐ {escape_markdown(commendee.name)} received a commend! ⭐",
                    colour=Colour.green(),
                )
            )

    @group.command(name="commendstats", description="Show commend stats")
    @app_commands.check(is_command_channel)
    async def commendstats(self, interaction: Interaction):
        session: SQLAlchemySession
        with Session() as session:
            most_commends_given_statement = (
                select(Player, func.count(Commend.commender_id).label("commend_count"))
                .join(Commend, Commend.commender_id == Player.id)
                .group_by(Player.id)
                .having(func.count(Commend.commender_id) > 0)
                .order_by(func.count(Commend.commender_id).desc())
            )
            most_commends_received_statement = (
                select(Player, func.count(Commend.commendee_id).label("commend_count"))
                .join(Commend, Commend.commendee_id == Player.id)
                .group_by(Player.id)
                .having(func.count(Commend.commendee_id) > 0)
                .order_by(func.count(Commend.commendee_id).desc())
            )

            most_commends_given: List[Player] = session.execute(
                most_commends_given_statement
            ).fetchall()
            most_commends_received: List[Player] = session.execute(
                most_commends_received_statement
            ).fetchall()
            session.close()

            output = "**Most commends given**"
            for i, row in zip(range(20), most_commends_given):
                player = row[Player]
                count = row["commend_count"]
                output += f"\n{i + 1}. {count} - {player.name}"
            output += "\n**Most commends received**"
            for i, row in zip(range(20), most_commends_received):
                player = row[Player]
                count = row["commend_count"]
                output += f"\n{i + 1}. {count} - {player.name}"

            if LEADERBOARD_CHANNEL:
                channel = bot.get_channel(LEADERBOARD_CHANNEL)
                if isinstance(channel, TextChannel):
                    await channel.send(
                        embed=Embed(description=output, colour=Colour.blue())
                    )
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f"Check {channel.mention}!",
                            colour=Colour.blue(),
                        ),
                        ephemeral=True,
                    )
            elif interaction.guild:
                player_id = interaction.user.id
                member_: Member | None = interaction.guild.get_member(player_id)
                if member_:
                    try:
                        await member_.send(
                            embed=Embed(
                                description=f"{output}",
                                colour=Colour.blue(),
                            ),
                        )
                    except Exception:
                        pass

    @group.command(
        name="toggleleaderboard", description="Enable/disable showing on leaderbaord"
    )
    @app_commands.check(is_command_channel)
    @app_commands.describe(option="True/False")
    async def toggleleaderboard(self, interaction: Interaction, option: bool):
        session: SQLAlchemySession
        with Session() as session:
            player = (
                session.query(Player).filter(Player.id == interaction.user.id).first()
            )
            if player:
                player.leaderboard_enabled = option
                session.commit()
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Player {interaction.user.display_name} not found",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
        if option:
            await interaction.response.send_message(
                embed=Embed(
                    description="You are now visible on the leaderboard",
                    colour=Colour.blue(),
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=Embed(
                    description="You are no longer visible on the leaderboard",
                    colour=Colour.blue(),
                ),
                ephemeral=True,
            )

    @group.command(name="togglestats", description="Enable/disable player stats")
    @app_commands.check(is_command_channel)
    @app_commands.describe(option="True/False")
    async def togglestats(self, interaction: Interaction, option: bool):
        session: SQLAlchemySession
        with Session() as session:
            player = (
                session.query(Player).filter(Player.id == interaction.user.id).first()
            )
            if player:
                player.stats_enabled = option
                session.commit()
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Player {interaction.user.display_name} not found",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
        if option:
            await interaction.response.send_message(
                embed=Embed(
                    description="`/Stats` enabled",
                    colour=Colour.blue(),
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=Embed(
                    description="`/Stats` disabled",
                    colour=Colour.blue(),
                ),
                ephemeral=True,
            )

    @group.command(name="togglevoicemove", description="Enable/disable voice movement")
    @app_commands.check(is_command_channel)
    @app_commands.describe(option="True/False")
    async def togglevoicemove(self, interaction: Interaction, option: bool):
        if not ENABLE_VOICE_MOVE:
            await interaction.response.send_message(
                embed=Embed(
                    description="Voice movement is disabled",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        session: SQLAlchemySession
        with Session() as session:
            player = (
                session.query(Player).filter(Player.id == interaction.user.id).first()
            )
            if player:
                player.move_enabled = option
                session.commit()
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Player {interaction.user.display_name} not found",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

        if option:
            await interaction.response.send_message(
                embed=Embed(
                    description="Player moving enabled",
                    colour=Colour.blue(),
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=Embed(
                    description="Player moving disabled",
                    colour=Colour.blue(),
                ),
                ephemeral=True,
            )

    @group.command(name="setmu", description="Directly set a player's mu")
    @app_commands.check(is_command_channel)
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(member="Player to be adjusted", mu="Mu value")
    async def setmu(self, interaction: Interaction, member: Member, mu: float):
        session: SQLAlchemySession
        with Session() as session:
            player = session.query(Player).filter(Player.id == member.id).first()
            if not player:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Player **{member.name}** not found",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            player.rated_trueskill_mu = mu
            player_category_trueskills = (
                session.query(PlayerCategoryTrueskill)
                .filter(PlayerCategoryTrueskill.player_id == player.id)
                .all()
            )
            for player_category_trueskill in player_category_trueskills:
                player_category_trueskill.mu = mu

            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Player **{member.name}** mu set to **{mu}**",
                    colour=Colour.blue(),
                ),
                ephemeral=True,
            )

    @group.command(name="setsigma", description="Directly set a player's sigma")
    @app_commands.check(is_command_channel)
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(member="Player to be adjusted", sigma="Sigma value")
    async def setsigma(self, interaction: Interaction, member: Member, sigma: float):
        session: SQLAlchemySession
        with Session() as session:
            player = session.query(Player).filter(Player.id == member.id).first()
            if not player:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Player **{member.name}** not found",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            min_sigma = max(1.5, config.SIGMA_FLOOR)
            max_sigma = min(8.33, config.DEFAULT_TRUESKILL_SIGMA)
            if sigma < min_sigma or sigma > max_sigma:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Sigma value must be between **{min_sigma}** and **{max_sigma}**: **{sigma}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            player.rated_trueskill_sigma = sigma
            player_category_trueskills = (
                session.query(PlayerCategoryTrueskill)
                .filter(PlayerCategoryTrueskill.player_id == player.id)
                .all()
            )
            for player_category_trueskill in player_category_trueskills:
                player_category_trueskill.sigma = sigma
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Player **{member.name}** sigma set to **{sigma}**",
                    colour=Colour.blue(),
                ),
                ephemeral=True,
            )
