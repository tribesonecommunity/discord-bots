import asyncio
import logging
from bisect import bisect
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from discord import Colour, Embed, Interaction, Message, TextChannel, app_commands
from discord.ext.commands import Bot
from sqlalchemy.orm.session import Session as SQLAlchemySession
from table2ascii import Alignment, PresetStyle, table2ascii
from trueskill import Rating

from discord_bots.bot import bot
from discord_bots.checks import is_command_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.config import (
    DEFAULT_RAFFLE_VALUE,
    DEFAULT_TRUESKILL_MU,
    DEFAULT_TRUESKILL_SIGMA,
    ENABLE_RAFFLE,
    LEADERBOARD_CHANNEL,
    SHOW_TRUESKILL,
)
from discord_bots.models import (
    Category,
    FinishedGame,
    FinishedGamePlayer,
    InProgressGame,
    InProgressGamePlayer,
    Player,
    PlayerCategoryTrueskill,
    Session,
)
from discord_bots.utils import (
    MU_LOWER_UNICODE,
    SIGMA_LOWER_UNICODE,
    add_empty_field,
    category_autocomplete_with_user_id,
    code_block,
    get_guild_partial_message,
    print_leaderboard,
    send_in_guild_message,
    short_uuid,
)

_log = logging.getLogger(__name__)


class CommonCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    @app_commands.command(
        name="resetleaderboardchannel", description="Resets & updates the leaderboards"
    )
    @app_commands.check(is_command_channel)
    async def resetleaderboardchannel(self, interaction: Interaction):
        if not LEADERBOARD_CHANNEL:
            await interaction.response.send_message(
                "Leaderboard channel ID not configured", ephemeral=True
            )
            return
        channel: TextChannel = bot.get_channel(LEADERBOARD_CHANNEL)
        if not channel:
            await interaction.response.send_message(
                "Could not find leaderboard channel, check ID", ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
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
                )
            )
        else:
            await interaction.followup.send(
                embed=Embed(
                    description="Leaderboard channel reset",
                    colour=Colour.green(),
                )
            )

    @app_commands.command(
        name="setgamecode", description="Sets lobby code for your current game"
    )
    @app_commands.check(is_command_channel)
    @app_commands.guild_only()
    @app_commands.describe(code="Game lobby code")
    async def setgamecode(self, interaction: Interaction, code: str):
        assert interaction.guild
        session: SQLAlchemySession
        with Session() as session:
            ipgp = (
                session.query(InProgressGamePlayer)
                .filter(InProgressGamePlayer.player_id == interaction.user.id)
                .first()
            )
            if not ipgp:
                await interaction.response.send_message(
                    embed=Embed(
                        description="You must be in game to set the game code!",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            ipg = (
                session.query(InProgressGame)
                .filter(InProgressGame.id == ipgp.in_progress_game_id)
                .first()
            )
            if not ipg:
                await interaction.response.send_message(
                    embed=Embed(
                        description="You must be in game to set the game code!",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            if ipg.code == code:
                await interaction.response.send_message(
                    embed=Embed(
                        description="This is already the current game code!",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            ipg.code = code
            await interaction.response.defer(ephemeral=True)
            title: str = f"Lobby code for ({short_uuid(ipg.id)})"
            if ipg.channel_id and ipg.message_id:
                partial_message = get_guild_partial_message(
                    interaction.guild, ipg.channel_id, ipg.message_id
                )
                channel = interaction.guild.get_channel(ipg.channel_id)
                if isinstance(channel, TextChannel):
                    try:
                        message: Message = await channel.fetch_message(ipg.message_id)
                        if len(message.embeds) > 0:
                            embed: Embed = message.embeds[0]
                            replaced_code = False
                            for i, field in enumerate(embed.fields):
                                if field.name == "🔢 Game Code":
                                    field.value = f"`{code}`"
                                    embed.set_field_at(
                                        i,
                                        name="🔢 Game Code",
                                        value=f"`{code}`",
                                        inline=True,
                                    )
                                    replaced_code = True
                                    break
                            if not replaced_code:
                                last = embed.fields[-1]
                                if (
                                    last.name == ""
                                    and last.value == ""
                                    and last.inline == True
                                ):
                                    embed.remove_field(-1)
                                embed.add_field(
                                    name="🔢 Game Code", value=f"`{code}`", inline=True
                                )
                                add_empty_field(embed, offset=3)
                            await message.edit(embed=embed)
                    except:
                        _log.exception(
                            f"[setgamecode] Failed to get message with guild_id={interaction.guild_id}, channel_id={ipg.channel_id}, message_id={ipg.message_id}:"
                        )
                if partial_message:
                    title = f"Lobby code for {partial_message.jump_url}"

            embed = Embed(
                title=title,
                description=f"`{code}`",
                colour=Colour.green(),
            )
            embed.set_footer(
                text=f"set by {interaction.user.display_name} ({interaction.user.name})"
            )
            coroutines = []
            result = (
                session.query(InProgressGamePlayer.player_id)
                .filter(
                    InProgressGamePlayer.in_progress_game_id == ipg.id,
                    InProgressGamePlayer.player_id
                    != interaction.user.id,  # don't send the code to the one who wants to send it out
                )
                .all()
            )
            ipg_player_ids: list[int] = (
                [player_id[0] for player_id in result if player_id] if result else []
            )
            for player_id in ipg_player_ids:
                coroutines.append(
                    send_in_guild_message(interaction.guild, player_id, embed=embed)
                )
            if ipg_player_ids:
                try:
                    await asyncio.gather(*coroutines)
                except:
                    _log.exception(
                        "[setgamecode] Ignoring exception in asyncio.gather:"
                    )
                else:
                    await interaction.followup.send(
                        embed=Embed(
                            description="Lobby code sent to each player",
                            colour=Colour.blue(),
                        ),
                        ephemeral=True,
                    )
            else:
                _log.warn("No in_progress_game_players to send a lobby code to")
                await interaction.followup.send(
                    embed=Embed(
                        description="There are no in-game players to send this lobby code to!",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
            session.commit()

    @app_commands.command(
        name="stats", description="Privately displays your TrueSkill statistics"
    )
    @app_commands.check(is_command_channel)
    @app_commands.describe(category_name="Category to show stats for")
    @app_commands.autocomplete(category_name=category_autocomplete_with_user_id)
    @app_commands.rename(category_name="category")
    async def stats(
        self, interaction: Interaction, category_name: Optional[str] | None
    ):
        """
        Replies to the user with their TrueSkill statistics. Can be used both inside and out of a Guild
        """
        session: SQLAlchemySession
        with Session() as session:
            player: Player | None = (
                session.query(Player).filter(Player.id == interaction.user.id).first()
            )
            if not player:
                # Edge case where user has no record in the Players table
                await interaction.response.send_message(
                    embed=Embed(
                        description="You have not played any games",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return
            if not player.stats_enabled:
                await interaction.response.send_message(
                    embed=Embed(
                        description="You have disabled `/stats`",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

            fgps: List[FinishedGamePlayer] | None = (
                session.query(FinishedGamePlayer)
                .filter(FinishedGamePlayer.player_id == player.id)
                .all()
            )
            if not fgps:
                await interaction.response.send_message(
                    embed=Embed(
                        description="You have not played any games",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

            finished_game_ids: List[str] | None = [fgp.finished_game_id for fgp in fgps]
            fgs: List[FinishedGame] | None = (
                session.query(FinishedGame)
                .filter(FinishedGame.id.in_(finished_game_ids))
                .all()
            )
            if not fgs:
                await interaction.response.send_message(
                    embed=Embed(
                        description="You have not played any games",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                session.close()
                return

            fgps_by_finished_game_id: dict[str, FinishedGamePlayer] = {
                fgp.finished_game_id: fgp for fgp in fgps
            }

            players: list[Player] = session.query(Player).all()

            default_rating = Rating()
            # Filter players that haven't played a game
            players = list(
                filter(
                    lambda x: (
                        x.rated_trueskill_mu != default_rating.mu
                        and x.rated_trueskill_sigma != default_rating.sigma
                    )
                    and (
                        x.rated_trueskill_mu != DEFAULT_TRUESKILL_MU
                        and x.rated_trueskill_sigma != DEFAULT_TRUESKILL_SIGMA
                    ),
                    players,
                )
            )
            trueskills = list(
                sorted(
                    [
                        round(p.rated_trueskill_mu - 3 * p.rated_trueskill_sigma, 2)
                        for p in players
                    ]
                )
            )
            trueskill_index = bisect(
                trueskills,
                round(player.rated_trueskill_mu - 3 * player.rated_trueskill_sigma, 2),
            )
            trueskill_ratio = (len(trueskills) - trueskill_index) / (
                len(trueskills) or 1
            )
            if trueskill_ratio <= 0.05:
                trueskill_pct = "Top 5%"
            elif trueskill_ratio <= 0.10:
                trueskill_pct = "Top 10%"
            elif trueskill_ratio <= 0.25:
                trueskill_pct = "Top 25%"
            elif trueskill_ratio <= 0.50:
                trueskill_pct = "Top 50%"
            elif trueskill_ratio <= 0.75:
                trueskill_pct = "Top 75%"
            else:
                trueskill_pct = "Top 100%"

            # all of this below can probably be done more gracefull with a pandas dataframe
            def wins_losses_ties_last_ndays(
                finished_games: List[FinishedGame], n: int = -1
            ) -> tuple[list[FinishedGame], list[FinishedGame], list[FinishedGame]]:
                if n == -1:
                    # all finished games
                    last_nfgs = finished_games
                else:
                    # last n
                    last_nfgs = [
                        fg
                        for fg in finished_games
                        if fg.finished_at.replace(tzinfo=timezone.utc)
                        > datetime.now(timezone.utc) - timedelta(days=n)
                    ]
                wins = [
                    fg
                    for fg in last_nfgs
                    if fg.winning_team == fgps_by_finished_game_id[fg.id].team
                ]
                losses = [
                    fg
                    for fg in last_nfgs
                    if fg.winning_team != fgps_by_finished_game_id[fg.id].team
                    and fg.winning_team != -1
                ]
                ties = [fg for fg in last_nfgs if fg.winning_team == -1]
                return wins, losses, ties

            def win_rate(wins, losses, ties):
                denominator = max(wins + losses + ties, 1)
                return round(100 * (wins + 0.5 * ties) / denominator, 1)

            def get_table_col(games: List[FinishedGame]):
                cols = []
                for num_days in [7, 30, 90, 365, -1]:
                    wins, losses, ties = wins_losses_ties_last_ndays(games, num_days)
                    num_wins, num_losses, num_ties = len(wins), len(losses), len(ties)
                    winrate = round(win_rate(num_wins, num_losses, num_ties))
                    col = [
                        "Total" if num_days == -1 else f"{num_days}D",
                        len(wins),
                        len(losses),
                        len(ties),
                        num_wins + num_losses + num_ties,
                        f"{winrate}%",
                    ]
                    cols.append(col)
                return cols

            embeds: list[Embed] = []
            footer_text = f"Rating = {MU_LOWER_UNICODE} - 3*{SIGMA_LOWER_UNICODE}"
            cols = []
            conditions = []
            conditions.append(PlayerCategoryTrueskill.player_id == player.id)
            if category_name:
                conditions.append(Category.name == category_name)
            player_category_trueskills: list[PlayerCategoryTrueskill] | None = (
                session.query(PlayerCategoryTrueskill)
                .join(Category)
                .filter(*conditions)
                .order_by(Category.name)
                .all()
            )
            # assume that if a guild uses categories, they will use them exclusively, i.e., no mixing categorized and uncategorized queues
            if player_category_trueskills:
                num_pct = len(player_category_trueskills)
                for i, pct in enumerate(player_category_trueskills):
                    category: Category | None = (
                        session.query(Category)
                        .filter(Category.id == pct.category_id)
                        .first()
                    )
                    if not category:
                        # should never happen
                        _log.error(
                            f"No Category found for player_category_trueskill with id {pct.id}"
                        )
                        await interaction.response.send_message(
                            embed=Embed(description="Could not find your stats")
                        )
                        return
                    title = f"TrueSkill for {category.name}"
                    if category.is_rated and SHOW_TRUESKILL:
                        description = (
                            f"Rating: **{round(pct.rank, 1)}**"
                            f" (`{MU_LOWER_UNICODE}: {round(pct.mu, 1)}`, "
                            f"`{SIGMA_LOWER_UNICODE}: {round(pct.sigma, 1)}`)"
                        )
                    else:
                        description = f"Rating: {trueskill_pct}"

                    category_games = [
                        game
                        for game in fgs
                        if game.category_name and category.name == game.category_name
                    ]
                    cols = get_table_col(category_games)
                    table = table2ascii(
                        header=["Last", "W", "L", "T", "Total", "WR"],
                        body=cols,
                        first_col_heading=True,
                        style=PresetStyle.plain,
                        alignments=[
                            Alignment.RIGHT,
                            Alignment.DECIMAL,
                            Alignment.DECIMAL,
                            Alignment.DECIMAL,
                            Alignment.DECIMAL,
                            Alignment.RIGHT,
                        ],
                    )
                    description += code_block(table)
                    embed = Embed(title=title, description=description)
                    if i == (num_pct - 1):
                        # only add the footer to the last embed so we don't duplicate the information
                        embed.set_footer(text=footer_text)
                    embeds.append(embed)
            else:
                # no categories defined, display their global trueskill stats
                description = ""
                if SHOW_TRUESKILL:
                    rank = player.rated_trueskill_mu - 3 * player.rated_trueskill_sigma
                    description = (
                        f"Rating: **{round(rank, 1)}**"
                        f" (`{MU_LOWER_UNICODE}: {round(player.rated_trueskill_mu, 1)}`, "
                        f"`{SIGMA_LOWER_UNICODE}: {round(player.rated_trueskill_sigma, 1)}`)"
                    )
                else:
                    description = f"Rating: {trueskill_pct}"
                cols = get_table_col(fgs)
                table = table2ascii(
                    header=["Period", "Wins", "Losses", "Ties", "Total", "Win %"],
                    body=cols,
                    first_col_heading=True,
                    style=PresetStyle.plain,
                    alignments=[
                        Alignment.LEFT,
                        Alignment.DECIMAL,
                        Alignment.DECIMAL,
                        Alignment.DECIMAL,
                        Alignment.DECIMAL,
                        Alignment.DECIMAL,
                    ],
                )
                description += code_block(table)
                embed = Embed(
                    title="Overall Stats",
                    description=description,
                )
                embed.set_footer(text=footer_text)
                embeds.append(embed)
            try:
                await interaction.response.send_message(embeds=embeds, ephemeral=True)
            except Exception:
                _log.exception(f"Caught exception trying to send stats message")
