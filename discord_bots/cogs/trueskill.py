import logging
from typing import List

from discord import Colour, Embed, Interaction, Member, app_commands
from discord.ext.commands import Bot
from discord.utils import escape_markdown
from numpy import std
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.checks import is_admin_app_command, is_command_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.config import DEFAULT_TRUESKILL_MU, DEFAULT_TRUESKILL_SIGMA
from discord_bots.models import Player, PlayerCategoryTrueskill, Queue, Session
from discord_bots.utils import mean, print_leaderboard

_log = logging.getLogger(__name__)


class TrueskillCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(name="trueskill", description="Trueskill commands")

    @group.command(name="info", description="Explanation of Trueskill")
    @app_commands.check(is_command_channel)
    async def trueskill(self, interaction: Interaction):
        output = ""
        output += "**mu (μ)**: The average skill of the gamer"
        output += "\n**sigma (σ)**: The degree of uncertainty in the gamer's skill"
        output += "\n**Reference**: https://www.microsoft.com/en-us/research/project/trueskill-ranking-system"
        output += "\n**Implementation**: https://trueskill.org/"
        thumbnail = "https://www.microsoft.com/en-us/research/uploads/prod/2016/02/trueskill-skilldia.jpg"
        embed = Embed(title="Trueskill", description=output, colour=Colour.blue())
        embed.set_thumbnail(url=thumbnail)
        await interaction.response.send_message(embed=embed)

    @group.command(
        name="resetplayer", description="Resets a players trueskill values to default"
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(member="Player to be reset")
    async def resetplayertrueskill(self, interaction: Interaction, member: Member):
        session: SQLAlchemySession
        with Session() as session:
            player: Player | None = (
                session.query(Player).filter(Player.id == member.id).first()
            )
            if not player:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"{member.name} is not a player",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            pcts: List[PlayerCategoryTrueskill] = (
                session.query(PlayerCategoryTrueskill)
                .filter(PlayerCategoryTrueskill.player_id == player.id)
                .all()
            )
            for pct in pcts:
                pct.mu = DEFAULT_TRUESKILL_MU
                pct.sigma = DEFAULT_TRUESKILL_SIGMA
                pct.rank = pct.mu - (3 * pct.sigma)

            player.rated_trueskill_mu = DEFAULT_TRUESKILL_MU
            player.rated_trueskill_sigma = DEFAULT_TRUESKILL_SIGMA

            session.commit()
        await interaction.response.send_message(
            embed=Embed(
                description=f"{escape_markdown(member.name)} trueskill reset.",
                colour=Colour.green(),
            )
        )

    @group.command(name="showsigma", description="Returns the player's base sigma")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(member="Existing player")
    async def showsigma(self, interaction: Interaction, member: Member):
        """
        Returns the player's base sigma
        """
        session: SQLAlchemySession
        with Session() as session:
            player: Player | None = (
                session.query(Player).filter(Player.id == member.id).first()
            )
            if not player:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"{member.name} is not a player",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            output = embed_title = (
                f"**{member.name}'s** sigma: **{round(player.rated_trueskill_sigma, 4)}**"
            )

            await interaction.response.send_message(
                embed=Embed(
                    description=output,
                    colour=Colour.blue(),
                ),
                ephemeral=True,
            )

    @group.command(
        name="shownormaldist",
        description="Print the normal distribution of the trueskill in a given queue",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue")
    async def showtrueskillnormdist(self, interaction: Interaction, queue_name: str):
        """
        Print the normal distribution of the trueskill in a given queue.

        Useful for setting queue mu ranges
        """
        session: SQLAlchemySession
        with Session() as session:
            queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue: **{queue_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            trueskill_mus = []
            if queue.category_id:
                player_category_trueskills: List[PlayerCategoryTrueskill] = (
                    session.query(PlayerCategoryTrueskill)
                    .filter(
                        PlayerCategoryTrueskill.category_id == queue.category_id,
                    )
                    .all()
                )
                trueskill_mus = [pct.mu for pct in player_category_trueskills]
            else:
                players = (
                    session.query(Player)
                    .filter(Player.finished_game_players.any())
                    .all()
                )
                trueskill_mus = [p.rated_trueskill_mu for p in players]

        std_dev = std(trueskill_mus)
        average = mean(trueskill_mus)
        output = []
        output.append(f"**Data points**: {len(trueskill_mus)}")
        output.append(f"**Mean**: {round(average, 2)}")
        output.append(f"**Stddev**: {round(std_dev, 2)}\n")
        output.append(f"**2%** (+2σ): {round(average + 2 * std_dev, 2)}")
        output.append(f"**7%** (+1.5σ): {round(average + 1.5 * std_dev, 2)}")
        output.append(f"**16%** (+1σ): {round(average + 1 * std_dev, 2)}")
        output.append(f"**31%** (+0.5σ): {round(average + 0.5 * std_dev, 2)}")
        output.append(f"**50%** (0σ): {round(average, 2)}")
        output.append(f"**69%** (-0.5σ): {round(average - 0.5 * std_dev, 2)}")
        output.append(f"**84%** (-1σ): {round(average - 1 * std_dev, 2)}")
        output.append(f"**93%** (-1.5σ): {round(average - 1.5 * std_dev, 2)}")
        output.append(f"**98%** (+2σ): {round(average - 2 * std_dev, 2)}")

        await interaction.response.send_message(
            embed=Embed(
                title=f"'{queue.name}' Trueskill Distribution",
                description="\n".join(output),
                colour=Colour.blue(),
            )
        )

    @group.command(name="testleaderboard", description="Test print the leaderboard")
    @app_commands.check(is_command_channel)
    async def testleaderboard(self, interaction: Interaction):
        await print_leaderboard()

    @showtrueskillnormdist.autocomplete("queue_name")
    async def queue_autocomplete(self, interaction: Interaction, current: str):
        result = []
        session: SQLAlchemySession
        with Session() as session:
            queues: list[Queue] | None = (
                session.query(Queue).order_by(Queue.name).limit(25).all()
            )
            if queues:
                for queue in queues:
                    if current in queue.name:
                        result.append(
                            app_commands.Choice(name=queue.name, value=queue.name)
                        )
        return result

    ####################
    # Commands removed #
    ####################
    """
    @bot.command()
    @commands.check(is_admin)
    async def decayplayer(ctx: Context, member: Member, decay_amount_percent: str):
        message = ctx.message
        # Manually adjust a player's trueskill rating downward by a percentage
        if not decay_amount_percent.endswith("%"):
            await send_message(
                message.channel,
                embed_description="Decay amount must end with %",
                colour=Colour.red(),
            )
            return

        decay_amount = int(decay_amount_percent[:-1])
        if decay_amount < 1 or decay_amount > 100:
            await send_message(
                message.channel,
                embed_description="Decay amount must be between 1-100",
                colour=Colour.red(),
            )
            return

        session = ctx.session
        player: Player = (
            session.query(Player).filter(Player.id == message.mentions[0].id).first()
        )
        rated_trueskill_mu_before = player.rated_trueskill_mu
        rated_trueskill_mu_after = player.rated_trueskill_mu * (100 - decay_amount) / 100
        player.rated_trueskill_mu = rated_trueskill_mu_after
        await send_message(
            message.channel,
            embed_description=f"{escape_markdown(member.name)} decayed by {decay_amount}%",
            colour=Colour.green(),
        )
        session.add(
            PlayerDecay(
                player.id,
                decay_amount,
                rated_trueskill_mu_before=rated_trueskill_mu_before,
                rated_trueskill_mu_after=rated_trueskill_mu_after,
            )
        )
        session.commit()

    @bot.command()
    @commands.check(is_admin)
    async def setsigma(ctx: Context, member: Member, sigma: float):
        if sigma < 1 or sigma > 8.33:
            await send_message(
                ctx.message.channel,
                embed_description=f"Amount must be between 1 and 8.33",
                colour=Colour.red(),
            )
            return

        session = ctx.session
        player: Player = session.query(Player).filter(Player.id == member.id).first()
        sigma_before = player.rated_trueskill_sigma
        player.rated_trueskill_sigma = sigma
        session.commit()
        session.close()

        await send_message(
            ctx.message.channel,
            embed_description=f"Sigma for **{member.name}** changed from **{round(sigma_before, 4)}** to **{sigma}**",
            colour=Colour.blue(),
        )

    @bot.command()
    @commands.check(is_admin)
    async def listplayerdecays(ctx: Context, member: Member):
        message = ctx.message
        session = ctx.session
        player = session.query(Player).filter(Player.id == member.id).first()
        player_decays: list[PlayerDecay] = session.query(PlayerDecay).filter(
            PlayerDecay.player_id == player.id
        )
        output = f"Decays for {escape_markdown(player.name)}:"
        for player_decay in player_decays:
            output += f"\n- {player_decay.decayed_at.strftime('%Y-%m-%d')} - Amount: {player_decay.decay_percentage}%"

        await send_message(message.channel, embed_description=output, colour=Colour.blue())
    """
