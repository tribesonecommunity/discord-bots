from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal, Optional

from discord import (
    ButtonStyle,
    Client,
    Colour,
    Embed,
    Interaction,
    Member,
    Message,
    Role,
    TextChannel,
    app_commands,
)
from discord.abc import GuildChannel
from discord.ext import commands
from discord.ui import Button, button
from sqlalchemy.orm.session import Session as SQLAlchemySession
from trueskill import Rating, rate

from discord_bots import config
from discord_bots.checks import is_admin_app_command, is_command_channel
from discord_bots.cogs.base import BaseView
from discord_bots.cogs.confirmation import ConfirmationView
from discord_bots.cogs.economy import EconomyCommands
from discord_bots.models import (
    Category,
    FinishedGame,
    FinishedGamePlayer,
    InProgressGame,
    InProgressGameChannel,
    InProgressGamePlayer,
    Map,
    Player,
    PlayerCategoryTrueskill,
    Queue,
    QueueWaitlist,
    Rotation,
    RotationMap,
    Session,
)
from discord_bots.utils import (
    create_cancelled_game_embed,
    create_finished_game_embed,
    get_guild_partial_message,
    move_game_players,
    move_game_players_lobby,
    send_in_guild_message,
    short_uuid,
    upload_stats_screenshot_imgkit_channel,
    upload_stats_screenshot_imgkit_interaction,
)

if TYPE_CHECKING:
    from discord.ext.commands import Bot


_log = logging.getLogger(__name__)
_lock = asyncio.Lock()


class InProgressGameCommands(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot: Bot = bot
        self.views: list[InProgressGameView] = []

    group = app_commands.Group(name="game", description="Game commands")

    async def cog_load(self) -> None:
        session: SQLAlchemySession
        with Session() as session:
            in_progress_games: list[InProgressGame] = session.query(
                InProgressGame
            ).all()
            for game in in_progress_games:
                if game.message_id:
                    self.views.append(InProgressGameView(game.id, self))
                    self.bot.add_view(
                        InProgressGameView(game.id, self),
                        message_id=game.message_id,
                    )

    async def cog_unload(self) -> None:
        for view in self.views:
            view.stop()

    def get_player_and_in_progress_game(
        self,
        session: SQLAlchemySession,
        player_id: int,
        game_id: Optional[str] = None,
    ) -> tuple[InProgressGamePlayer, InProgressGame] | None:
        game_player: InProgressGamePlayer | None = (
            session.query(InProgressGamePlayer)
            .filter(InProgressGamePlayer.player_id == player_id)
            .first()
        )
        if not game_player:
            return None
        in_progress_game: InProgressGame | None
        if game_id:
            in_progress_game = (
                session.query(InProgressGame)
                .filter(InProgressGame.id == game_player.in_progress_game_id)
                .filter(InProgressGame.id == game_id)
                .filter(InProgressGame.is_finished == False)
                .first()
            )
            if not in_progress_game:
                _log.warning(
                    f"No in_progress_game found with id {game_player.in_progress_game_id} for game_player with id {game_player.id}"
                )
                return None
        else:
            in_progress_game = (
                session.query(InProgressGame)
                .filter(InProgressGame.id == game_player.in_progress_game_id)
                .filter(InProgressGame.is_finished == False)
                .first()
            )
            if not in_progress_game:
                _log.warning(
                    f"No in_progress_game found with id {game_player.in_progress_game_id} for game_player with id {game_player.id}"
                )
                return None
        return game_player, in_progress_game

    @group.command(name="cancel", description="Cancels the specified game")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(game_id="In progress game ID")
    @app_commands.guild_only()
    async def cancelgame(self, interaction: Interaction, game_id: str):
        await self.cancelgame_callback(interaction, game_id)

    async def cancelgame_callback(self, interaction: Interaction, game_id: str) -> bool:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        if _lock.locked():
            await interaction.followup.send(
                embed=Embed(
                    description="⏳ A **Cancel** is already in progress, please wait...",
                    color=Colour.yellow(),
                ),
                ephemeral=True,
            )
        async with _lock:
            session: SQLAlchemySession
            with Session() as session:
                game = (
                    session.query(InProgressGame)
                    .filter(InProgressGame.id.startswith(game_id))
                    .first()
                )
                if not game:
                    await interaction.followup.send(
                        embed=Embed(
                            description=f"❌ Game {short_uuid(game_id)} does not exist",
                            colour=Colour.red(),
                        ),
                        ephemeral=True,
                    )
                    return False
                confirmation_buttons = ConfirmationView(interaction.user.id)
                confirmation_buttons.message = await interaction.followup.send(
                    embed=Embed(
                        description=f"⚠️ Are you sure you want to **Cancel** game **{short_uuid(game.id)}**?⚠️",
                        color=Colour.yellow(),
                    ),
                    view=confirmation_buttons,
                    ephemeral=True,
                )
                await confirmation_buttons.wait()
                if not confirmation_buttons.value:
                    return False
                is_game_finished = await self.cancel_in_progress_game(
                    session, interaction, game
                )
                session.commit()
                return is_game_finished

    async def cancel_in_progress_game(
        self,
        session: SQLAlchemySession,
        interaction: Interaction,
        game: InProgressGame,
    ):
        assert interaction.guild
        cancelled_game_embed = create_cancelled_game_embed(
            session, game, interaction.user.name
        )
        game_history_message: Message
        if config.GAME_HISTORY_CHANNEL:
            game_history_channel = interaction.guild.get_channel(
                config.GAME_HISTORY_CHANNEL
            )
            if game_history_channel and isinstance(game_history_channel, TextChannel):
                game_history_message = await game_history_channel.send(
                    embed=cancelled_game_embed
                )

        if game_history_message is not None:
            cancelled_game_embed.description = game_history_message.jump_url

        if config.CHANNEL_ID and interaction.guild:
            main_channel: GuildChannel | None = interaction.guild.get_channel(
                config.CHANNEL_ID
            )
            if main_channel and isinstance(main_channel, TextChannel):
                await main_channel.send(embed=cancelled_game_embed)
                if config.ECONOMY_ENABLED:
                    try:
                        economy_cog = self.bot.get_cog("EconomyCommands")
                        if economy_cog is not None and isinstance(
                            economy_cog, EconomyCommands
                        ):
                            await economy_cog.cancel_predictions(game.id)
                        else:
                            _log.warning("Could not get EconomyCommands cog")
                    except ValueError:
                        # Raised if there are no predictions on this game
                        await main_channel.send(
                            embed=Embed(
                                description="No predictions to be refunded",
                                colour=Colour.blue(),
                            )
                        )
                    except Exception:
                        _log.exception("Predictions failed to refund")
                        await main_channel.send(
                            embed=Embed(
                                description=f"Predictions failed to refund",
                                colour=Colour.red(),
                            )
                        )
                    else:
                        await main_channel.send(
                            embed=Embed(
                                description="Predictions refunded",
                                colour=Colour.blue(),
                            )
                        )

        session.query(InProgressGamePlayer).filter(
            InProgressGamePlayer.in_progress_game_id == game.id
        ).delete()
        session.commit()  # if you remove this commit, then there is a chance for the DB to lockup if someone types a message at the same time

        if config.ENABLE_VOICE_MOVE and config.VOICE_MOVE_LOBBY:
            try:
                await move_game_players_lobby(game.id, interaction.guild)
            except Exception:
                _log.exception("Ignored exception when moving a gameplayer to lobby:")

        for ipg_channel in session.query(InProgressGameChannel).filter(
            InProgressGameChannel.in_progress_game_id == game.id
        ):
            if interaction.guild:
                guild_channel = interaction.guild.get_channel(ipg_channel.channel_id)
                if guild_channel:
                    await guild_channel.delete()
            session.delete(ipg_channel)
        session.query(InProgressGame).filter(InProgressGame.id == game.id).delete()
        return True

    @group.command(name="finish", description="Ends the current game you are in")
    @app_commands.check(is_command_channel)
    @app_commands.describe(outcome="win, loss, or tie")
    @app_commands.guild_only()
    async def finishgame(
        self,
        interaction: Interaction,
        outcome: Literal["win", "loss", "tie"],
    ):
        await self.finishgame_callback(interaction, outcome)

    async def finishgame_callback(
        self,
        interaction: Interaction,
        outcome: Literal["win", "loss", "tie"],
        game_id: Optional[str] = None,
    ) -> bool:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        if _lock.locked():
            await interaction.followup.send(
                embed=Embed(
                    description="⏳ A **Finish** is already in progress, please wait...",
                    color=Colour.yellow(),
                ),
                ephemeral=True,
            )
        async with _lock:
            session: SQLAlchemySession
            with Session() as session:
                result = self.get_player_and_in_progress_game(
                    session, interaction.user.id, game_id
                )
                if result is None:
                    embed = Embed(
                        color=Colour.red(),
                    )
                    embed.description = "❌ You are not in this game"
                    await interaction.followup.send(
                        embed=embed,
                        ephemeral=True,
                    )
                    return False

                game_player: InProgressGamePlayer = result[0]
                game: InProgressGame = result[1]
                confirmation_buttons = ConfirmationView(interaction.user.id)
                confirmation_buttons.message = await interaction.followup.send(
                    embed=Embed(
                        description=f"⚠️ Are you sure you want to **Finish** game **{short_uuid(game.id)}** as a **{outcome}**?⚠️",
                        color=Colour.yellow(),
                    ),
                    view=confirmation_buttons,
                    ephemeral=True,
                )
                await confirmation_buttons.wait()
                if not confirmation_buttons.value:
                    return False
                is_finished = await self.finish_in_progress_game(
                    session, interaction, outcome, game_player, game
                )
                session.commit()
                return is_finished

    async def finish_in_progress_game(
        self,
        session: SQLAlchemySession,
        interaction: Interaction,
        outcome: Literal["win", "loss", "tie"],
        game_player: InProgressGamePlayer,
        in_progress_game: InProgressGame,
    ) -> bool:
        assert interaction is not None
        assert interaction.guild is not None
        queue: Queue | None = (
            session.query(Queue).filter(Queue.id == in_progress_game.queue_id).first()
        )
        if not queue:
            # should never happen
            _log.error(
                f"Could not find queue with id {in_progress_game.queue_id} for in_progress_game with id {in_progress_game.id}"
            )
            await interaction.followup.send(
                embed=Embed(
                    description="Oops, something went wrong...☹️️",
                    color=Colour.red(),
                ),
                ephemeral=True,
            )
            return False

        winning_team = -1
        if outcome == "win":
            winning_team = game_player.team
        elif outcome == "loss":
            winning_team = (game_player.team + 1) % 2
        else:
            # tie
            winning_team = -1

        players = (
            session.query(Player)
            .join(InProgressGamePlayer)
            .filter(
                InProgressGamePlayer.player_id == Player.id,
                InProgressGamePlayer.in_progress_game_id == in_progress_game.id,
            )
        ).all()
        player_ids: list[str] = [player.id for player in players]
        players_by_id: dict[int, Player] = {player.id: player for player in players}
        player_category_trueskills_by_id: dict[int, PlayerCategoryTrueskill] = {}
        if queue.category_id:
            player_category_trueskills: list[PlayerCategoryTrueskill] = (
                session.query(PlayerCategoryTrueskill)
                .filter(
                    PlayerCategoryTrueskill.player_id.in_(player_ids),
                    PlayerCategoryTrueskill.category_id == queue.category_id,
                )
                .all()
            )
            player_category_trueskills_by_id = {
                pct.player_id: pct for pct in player_category_trueskills
            }
        in_progress_game_players = (
            session.query(InProgressGamePlayer)
            .filter(InProgressGamePlayer.in_progress_game_id == in_progress_game.id)
            .all()
        )
        team0_rated_ratings_before = []
        team1_rated_ratings_before = []
        team0_players: list[InProgressGamePlayer] = []
        team1_players: list[InProgressGamePlayer] = []
        for in_progress_game_player in in_progress_game_players:
            player = players_by_id[in_progress_game_player.player_id]
            if in_progress_game_player.team == 0:
                team0_players.append(in_progress_game_player)
                if player.id in player_category_trueskills_by_id:
                    pct = player_category_trueskills_by_id[player.id]
                    team0_rated_ratings_before.append(Rating(pct.mu, pct.sigma))
                else:
                    team0_rated_ratings_before.append(
                        Rating(player.rated_trueskill_mu, player.rated_trueskill_sigma)
                    )
            else:
                team1_players.append(in_progress_game_player)
                if player.id in player_category_trueskills_by_id:
                    pct = player_category_trueskills_by_id[player.id]
                    team1_rated_ratings_before.append(Rating(pct.mu, pct.sigma))
                else:
                    team1_rated_ratings_before.append(
                        Rating(player.rated_trueskill_mu, player.rated_trueskill_sigma)
                    )

        if queue.category_id:
            category: Category | None = (
                session.query(Category).filter(Category.id == queue.category_id).first()
            )
            if category:
                category_name = category.name
            else:
                # should never happen
                _log.error(
                    f"Could not find category with id {queue.category_id} for queue with id {queue.id}"
                )
                await interaction.followup.send(
                    embed=Embed(
                        description="Something went wrong, please contact the server owner",
                        color=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return False
        else:
            category_name = None

        finished_game = FinishedGame(
            average_trueskill=in_progress_game.average_trueskill,
            finished_at=datetime.now(timezone.utc),
            game_id=in_progress_game.id,
            is_rated=queue.is_rated,
            map_full_name=in_progress_game.map_full_name,
            map_short_name=in_progress_game.map_short_name,
            queue_name=queue.name,
            category_name=category_name,
            started_at=in_progress_game.created_at,
            team0_name=in_progress_game.team0_name,
            team1_name=in_progress_game.team1_name,
            win_probability=in_progress_game.win_probability,
            winning_team=winning_team,
        )
        session.add(finished_game)

        result = None
        if winning_team == -1:
            result = [0, 0]
        elif winning_team == 0:
            result = [0, 1]
        elif winning_team == 1:
            result = [1, 0]

        team0_rated_ratings_after: list[Rating]
        team1_rated_ratings_after: list[Rating]
        if len(players) > 1:
            team0_rated_ratings_after, team1_rated_ratings_after = rate(
                [team0_rated_ratings_before, team1_rated_ratings_before], result
            )
        else:
            # Mostly useful for creating solo queues for testing, no real world
            # application
            team0_rated_ratings_after, team1_rated_ratings_after = (
                team0_rated_ratings_before,
                team1_rated_ratings_before,
            )

        def update_ratings(
            team_players: list[InProgressGamePlayer],
            ratings_before: list[Rating],
            ratings_after: list[Rating],
        ):
            for i, team_gip in enumerate(team_players):
                player = players_by_id[team_gip.player_id]
                finished_game_player = FinishedGamePlayer(
                    finished_game_id=finished_game.id,
                    player_id=player.id,
                    player_name=player.name,
                    team=team_gip.team,
                    rated_trueskill_mu_before=ratings_before[i].mu,
                    rated_trueskill_sigma_before=ratings_before[i].sigma,
                    rated_trueskill_mu_after=ratings_after[i].mu,
                    rated_trueskill_sigma_after=ratings_after[i].sigma,
                )
                trueskill_rating = ratings_after[i]
                # Regardless of category, always update the master trueskill. That way
                # when we create new categories off of it the data isn't completely
                # stale
                player.rated_trueskill_mu = trueskill_rating.mu
                player.rated_trueskill_sigma = trueskill_rating.sigma
                if player.id in player_category_trueskills_by_id:
                    pct = player_category_trueskills_by_id[player.id]
                    pct.mu = trueskill_rating.mu
                    pct.sigma = trueskill_rating.sigma
                    pct.rank = trueskill_rating.mu - 3 * trueskill_rating.sigma
                else:
                    session.add(
                        PlayerCategoryTrueskill(
                            player_id=player.id,
                            category_id=queue.category_id,
                            mu=trueskill_rating.mu,
                            sigma=trueskill_rating.sigma,
                            rank=trueskill_rating.mu - 3 * trueskill_rating.sigma,
                        )
                    )
                session.add(finished_game_player)

        update_ratings(
            team0_players, team0_rated_ratings_before, team0_rated_ratings_after
        )
        update_ratings(
            team1_players, team1_rated_ratings_before, team1_rated_ratings_after
        )
        session.commit()  # temporary solution until the foreign key constraint is resolved on EconomyPredictions/EconomyTransactions
        if config.ECONOMY_ENABLED:
            economy_cog = self.bot.get_cog("EconomyCommands")
            if economy_cog is not None and isinstance(economy_cog, EconomyCommands):
                await economy_cog.resolve_predictions(
                    interaction, outcome, in_progress_game.id
                )
            else:
                _log.warning("Could not get EconomyCommands cog")

        session.query(InProgressGamePlayer).filter(
            InProgressGamePlayer.in_progress_game_id == in_progress_game.id
        ).delete()
        in_progress_game.is_finished = True
        session.add(
            QueueWaitlist(
                channel_id=config.CHANNEL_ID,  # not sure about this column and what it's used for
                finished_game_id=finished_game.id,
                in_progress_game_id=in_progress_game.id,
                guild_id=interaction.guild_id,
                queue_id=queue.id,
                end_waitlist_at=datetime.now(timezone.utc)
                + timedelta(seconds=config.RE_ADD_DELAY),
            )
        )

        # Reward raffle tickets
        reward = (
            session.query(RotationMap.raffle_ticket_reward)
            .join(Map, Map.id == RotationMap.map_id)
            .join(Rotation, Rotation.id == RotationMap.rotation_id)
            .join(Queue, Queue.rotation_id == Rotation.id)
            .filter(Map.short_name == in_progress_game.map_short_name)
            .filter(Queue.id == in_progress_game.queue_id)
            .scalar()
        )
        if reward == 0:
            reward = config.DEFAULT_RAFFLE_VALUE

        for player in players:
            player.raffle_tickets += reward
            session.add(player)
        session.commit()

        finished_game_embed = create_finished_game_embed(
            session,
            finished_game.id,
            interaction.guild.id,
            (interaction.user.name, interaction.user.display_name),
        )
        game_history_message: Message
        if config.GAME_HISTORY_CHANNEL:
            game_history_channel: GuildChannel | None = interaction.guild.get_channel(
                config.GAME_HISTORY_CHANNEL
            )
            if game_history_channel and isinstance(game_history_channel, TextChannel):
                game_history_message = await game_history_channel.send(
                    embed=finished_game_embed
                )
                await upload_stats_screenshot_imgkit_channel(game_history_channel)
        elif config.STATS_DIR:
            await upload_stats_screenshot_imgkit_interaction(interaction)

        if game_history_message is not None:
            finished_game_embed.description = game_history_message.jump_url
        if config.CHANNEL_ID:
            main_channel = interaction.guild.get_channel(config.CHANNEL_ID)
            if isinstance(main_channel, TextChannel):
                await main_channel.send(embed=finished_game_embed)
        return True

    @group.command(
        name="moveplayers",
        description="Moves players in an in progress game to their respective voice channels",
    )
    @app_commands.guild_only()
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    async def movegameplayers(self, interaction: Interaction, game_id: str):
        """
        Move players in a given in-progress game to the correct voice channels
        """
        assert interaction.guild

        if not config.ENABLE_VOICE_MOVE:
            await interaction.response.send_message(
                embed=Embed(
                    description="Voice movement is disabled",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        else:
            try:
                await move_game_players(game_id, interaction)
            except Exception:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Failed to move players to voice channels for game {game_id}",
                        colour=Colour.red(),
                    ),
                )
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Players moved to voice channels for game {game_id}",
                        colour=Colour.blue(),
                    ),
                )

    @group.command(name="setcode", description="Sets lobby code for your current game")
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
                                embed_fields_len = (
                                    len(embed.fields) - 3
                                )  # subtract team0, team1, and "newline" fields
                                if embed_fields_len >= 5 and embed_fields_len % 3 == 2:
                                    # embeds are allowed 3 "columns" per "row"
                                    # to line everything up nicely when there's >= 5 fields and only one "column" slot left, we add a blank
                                    embed.add_field(name="", value="", inline=True)
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

    @cancelgame.autocomplete("game_id")
    @movegameplayers.autocomplete("game_id")
    async def game_autocomplete(self, interaction: Interaction, current: str):
        result = []
        session: SQLAlchemySession
        with Session() as session:
            in_progress_games: list[InProgressGame] | None = (
                session.query(InProgressGame).limit(25).all()
            )  # discord only supports up to 25 choices
            if in_progress_games:
                for ipg in in_progress_games:
                    short_game_id = short_uuid(ipg.id)
                    if current in short_game_id:
                        result.append(
                            app_commands.Choice(name=short_game_id, value=short_game_id)
                        )
        return result


class InProgressGameView(BaseView):
    def __init__(self, game_id: str, cog: InProgressGameCommands):
        super().__init__(timeout=None)
        self.game_id: str = game_id
        self.is_game_finished: bool = False
        self.cog = cog

    async def interaction_check(self, interaction: Interaction[Client]):
        if self.is_game_finished:
            embed = Embed(
                description="❌ This game has already been finished",
                colour=Colour.red(),
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)
            return False
        return True

    @button(
        label="Win",
        style=ButtonStyle.primary,
        custom_id="in_progress_game_view:win",
        emoji="🥇",
    )
    async def win_button(self, interaction: Interaction, button: Button):
        self.is_game_finished = await self.cog.finishgame_callback(
            interaction, "win", self.game_id
        )
        if self.is_game_finished:
            await self.disable_buttons(interaction)
            self.stop()

    @button(
        label="Loss",
        style=ButtonStyle.primary,
        custom_id="in_progress_game_view:loss",
        emoji="🥈",
    )
    async def loss_button(self, interaction: Interaction, button: Button):
        self.is_game_finished = await self.cog.finishgame_callback(
            interaction, "loss", self.game_id
        )
        if self.is_game_finished:
            await self.disable_buttons(interaction)
            self.stop()

    @button(
        label="Tie",
        style=ButtonStyle.primary,
        custom_id="in_progress_game_view:tie",
        emoji="🤞",
    )
    async def tie_button(self, interaction: Interaction, button: Button):
        self.is_game_finished = await self.cog.finishgame_callback(
            interaction, "tie", self.game_id
        )
        if self.is_game_finished:
            await self.disable_buttons(interaction)
            self.stop()

    @button(
        label="Cancel",
        style=ButtonStyle.red,
        custom_id="in_progress_game_view:cancel",
    )
    async def cancel_button(self, interaction: Interaction, button: Button):
        if not await is_admin_app_command(interaction):
            return
        self.is_game_finished = await self.cog.cancelgame_callback(
            interaction, self.game_id
        )
        if self.is_game_finished:
            # no need to disable the buttons, since the channel will be deleted immediately
            self.stop()
