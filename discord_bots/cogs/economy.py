import logging
from datetime import datetime, timedelta, timezone
from operator import itemgetter
from typing import Any, Literal, Optional

from discord import (
    ButtonStyle,
    Client,
    Colour,
    Embed,
    Interaction,
    Message,
    TextChannel,
    TextStyle,
    VoiceChannel,
    app_commands,
)
from discord.ext.commands import Bot, check
from discord.member import Member
from discord.ui import Button, Modal, TextInput, View
from discord.utils import get
from pytz import utc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.bot import bot
from discord_bots.checks import economy_enabled, is_admin_app_command
from discord_bots.cogs.base import BaseCog
from discord_bots.config import (
    CHANNEL_ID,
    CURRENCY_AWARD,
    CURRENCY_NAME,
    ECONOMY_ENABLED,
    GAME_HISTORY_CHANNEL,
    PREDICTION_TIMEOUT,
)
from discord_bots.models import (
    EconomyDonation,
    EconomyPrediction,
    EconomyTransaction,
    FinishedGame,
    InProgressGame,
    InProgressGameChannel,
    InProgressGamePlayer,
    Player,
    Queue,
    Session,
)
from discord_bots.utils import short_uuid

_log = logging.getLogger(__name__)


class EconomyCommands(BaseCog):
    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.bot = bot
        self.views: list[EconomyPredictionView] = []

    @app_commands.check(economy_enabled)
    async def cog_load(self) -> None:
        """
        Called every time the bot restarts
        Recreates view and re-links to existing message id
        """
        session: SQLAlchemySession = Session()
        in_progress_games: list[InProgressGame] = session.query(InProgressGame).all()
        for game in in_progress_games:
            if game.prediction_message_id:
                self.views.append(EconomyPredictionView(game.id))
                self.bot.add_view(
                    EconomyPredictionView(game.id),
                    message_id=game.prediction_message_id,
                )
        session.close()

    async def cog_unload(self) -> None:
        for view in self.views:
            view.stop()

    @app_commands.command(
        name="addcurrency", description=f"Admin; Add {CURRENCY_NAME} to a player"
    )
    @app_commands.check(economy_enabled)
    @app_commands.check(is_admin_app_command)
    async def addcurrency(
        self, interaction: Interaction, member: Member, add_value: int
    ) -> None:
        session: SQLAlchemySession = Session()
        admin: Player | None = (
            session.query(Player).filter(Player.id == interaction.user.id).first()
        )
        receiver: Player | None = (
            session.query(Player).filter(Player.id == member.id).first()
        )

        if not admin:
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Admin {interaction.user.display_name} does not exist",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        elif not receiver:
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Player {member.display_name} does not exist",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        donation = EconomyDonation(
            sending_player_id=None,
            admin_player_id=admin.id,
            receiving_player_id=receiver.id,
            value=add_value,
        )
        session.add(donation)
        session.commit()

        try:
            await EconomyCommands.create_transaction(
                admin, receiver, donation.value, donation
            )
        except Exception:
            await interaction.response.send_message(
                embed=Embed(
                    description="Currency add failed. Exception",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            _log.exception("Caught Exception in add_currency")
            session.delete(donation)
        else:
            receiver.currency += add_value
            await interaction.response.send_message(
                embed=Embed(
                    description=f"<@{interaction.user.id}> added {add_value} {CURRENCY_NAME} to <@{receiver.id}>",
                    colour=Colour.green(),
                )
            )
        finally:
            session.commit()
            session.close()

    async def award_currency(
        self, interaction: Interaction, in_progress_game: InProgressGame
    ) -> Embed:
        session: SQLAlchemySession = Session()

        queue: Queue | None = (
            session.query(Queue).filter(Queue.id == in_progress_game.queue_id).first()
        )
        if queue.currency_award:
            award_value = queue.currency_award
        else:
            award_value = CURRENCY_AWARD

        game_players: list[InProgressGamePlayer] = (
            session.query(InProgressGamePlayer)
            .filter(InProgressGamePlayer.in_progress_game_id == in_progress_game.id)
            .all()
        )

        short_game_id: str = short_uuid(in_progress_game.id)
        embed: Embed = Embed(
            title=f"Game '{queue.name}' ({short_game_id}) Prediction Results",
            description="",
            colour=Colour.green(),
        )
        for game_player in game_players:
            try:
                await EconomyCommands.create_transaction(
                    in_progress_game,
                    game_player.player,
                    award_value,
                    "Award",
                )
            except Exception as e:
                embed.add_field(
                    name="",
                    value=f"Currency award failed for <@{game_player.player_id}> | Award Value = {award_value} | Exception: {e}",
                    inline=False,
                )
                pass
            else:
                player: Player | None = (
                    session.query(Player)
                    .filter(Player.id == game_player.player_id)
                    .first()
                )
                player.currency += award_value
                session.commit()

        embed.add_field(
            name="",
            value=f"{award_value} {CURRENCY_NAME} awarded to participants",
            inline=False,
        )

        session.close()
        return embed

    async def cancel_predictions(self, game_id: str):
        session: SQLAlchemySession = Session()

        predictions: list[EconomyPrediction] = (
            session.query(EconomyPrediction)
            .filter(EconomyPrediction.in_progress_game_id.startswith(game_id))
            .all()
        )

        if len(predictions) == 0:
            raise ValueError(f"No Predictions on game {game_id}")

        for prediction in predictions:
            try:
                await EconomyCommands.create_transaction(
                    prediction.in_progress_game,
                    prediction.player,
                    prediction.prediction_value,
                    prediction,
                )
            except Exception:
                _log.exception(
                    f"Exception while refunding prediction {prediction.id} for game {game_id}"
                )
                raise
            else:
                player: Player | None = (
                    session.query(Player)
                    .filter(Player.id == prediction.player_id)
                    .first()
                )
                player.currency += prediction.prediction_value
                prediction.cancelled = True
                session.delete(prediction)
                session.commit()
        session.close()

    async def close_predictions(self, in_progress_games: list[InProgressGame]):
        session: SQLAlchemySession = Session()
        for game in in_progress_games:
            if not game.prediction_message_id:
                continue
            time_compare: datetime = game.created_at + timedelta(
                seconds=PREDICTION_TIMEOUT
            )
            time_compare = utc.localize(time_compare)
            if datetime.now(timezone.utc) > time_compare:
                game: InProgressGame | None = session.query(InProgressGame).filter(InProgressGame.id == game.id).first()
                game.prediction_open = False
                session.commit()
        session.close()

    async def create_prediction_message(
        self, in_progress_game: InProgressGame, match_channel: TextChannel
    ) -> int | None:

        if not ECONOMY_ENABLED:
            return None

        short_game_id: str = short_uuid(in_progress_game.id)
        embed = Embed(
            title=f"Game {short_game_id} Prediction",
            colour=Colour.blue(),
        )
        embed.add_field(
            name=f"{in_progress_game.team0_name}",
            value="> Total: 0\n> Win Ratio: 1:1.0\n> Predictors: 0",
            inline=True,
        )
        embed.add_field(
            name=f"{in_progress_game.team1_name}",
            value="> Total: 0\n> Win Ratio: 1:1.0\n> Predictors: 0",
            inline=True,
        )

        try:
            prediction_message: Message = await match_channel.send(
                embed=embed, view=EconomyPredictionView(in_progress_game.id)
            )
        except Exception:
            _log.exception(f"prediction failed for game: {in_progress_game.id}")
            return None
        else:
            return prediction_message.id

    async def create_prediction(
        self,
        member: Member,
        game: InProgressGame,
        selection: int,
        prediction_value: int,
    ) -> EconomyPrediction:
        session: SQLAlchemySession = Session()
        sender: Player | None = (
            session.query(Player).filter(Player.id == member.id).first()
        )

        if not ECONOMY_ENABLED:
            raise Exception("Player economy is disabled")
        elif not sender:
            raise Exception("Player does not exist")
        elif not sender.currency >= prediction_value:
            raise ValueError(
                f"You do not have enough {CURRENCY_NAME}. Current Balance: {sender.currency}"
            )

        prediction = EconomyPrediction(
            player_id=sender.id,
            in_progress_game_id=game.id,
            finished_game_id=None,
            team=int(selection),
            prediction_value=prediction_value,
            is_correct=None,
            cancelled=None,
        )
        return prediction

    @classmethod
    async def create_transaction(
        self,
        source_account: Player | FinishedGame | InProgressGame,
        destination_account: Player | FinishedGame | InProgressGame | None,
        transaction_value: int,
        source: EconomyDonation | EconomyPrediction | str,
    ):
        session: SQLAlchemySession = Session()

        if not ECONOMY_ENABLED:
            raise Exception("Player economy is disabled")
        if not source_account:
            raise TypeError("Transaction account not a player or game")
        elif not destination_account:
            raise TypeError("Destination account not a player or game")

        if not source:
            source_str = "Manual"
        elif type(source) == str:
            source_str = source
        else:
            source_str = source.__class__.__name__

        # Outbound Transaction
        session.add(
            EconomyTransaction(
                player_id=(
                    source_account.id if isinstance(source_account, Player) else None
                ),
                finished_game_id=(
                    source_account.game_id
                    if isinstance(source_account, FinishedGame)
                    else None
                ),
                in_progress_game_id=(
                    source_account.id
                    if isinstance(source_account, InProgressGame)
                    else None
                ),
                debit=0,
                credit=transaction_value,
                new_balance=(
                    source_account.currency - transaction_value
                    if isinstance(source_account, Player) and source_account.id
                    else 0
                ),
                transaction_type=source_str,
                economy_prediction_id=(
                    source.id if isinstance(source, EconomyPrediction) else None
                ),
                economy_donation_id=(
                    source.id if isinstance(source, EconomyDonation) else None
                ),
            )
        )

        # Inbound Transaction
        session.add(
            EconomyTransaction(
                player_id=(
                    destination_account.id
                    if isinstance(destination_account, Player)
                    else None
                ),
                finished_game_id=(
                    destination_account.game_id
                    if isinstance(destination_account, FinishedGame)
                    else None
                ),
                in_progress_game_id=(
                    destination_account.id
                    if isinstance(destination_account, InProgressGame)
                    else None
                ),
                debit=transaction_value,
                credit=0,
                new_balance=(
                    destination_account.currency + transaction_value
                    if isinstance(destination_account, Player)
                    and destination_account.id
                    else 0
                ),
                transaction_type=source_str,
                economy_prediction_id=(
                    source.id if isinstance(source, EconomyPrediction) else None
                ),
                economy_donation_id=(
                    source.id if isinstance(source, EconomyDonation) else None
                ),
            )
        )

        try:
            session.commit()
        except IntegrityError:
            _log.exception("integrity error?")
            session.rollback()
            raise
        finally:
            session.close()

    @app_commands.command(
        name="donatecurrency", description=f"Donate {CURRENCY_NAME} to another player"
    )
    @app_commands.check(economy_enabled)
    async def donatecurrency(
        self, interaction: Interaction, member: Member, donation_value: int
    ) -> None:
        session: SQLAlchemySession = Session()
        sender: Player | None = (
            session.query(Player).filter(Player.id == interaction.user.id).first()
        )
        receiver: Player | None = (
            session.query(Player).filter(Player.id == member.id).first()
        )

        # Check sender & receiver
        if not sender:
            await interaction.response.send_message(
                embed=Embed(
                    description="Sending player does not exist",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        elif not receiver:
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Player {member.display_name} does not exist",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        elif sender == receiver:
            await interaction.response.send_message(
                embed=Embed(
                    description=f"You cannot donate {CURRENCY_NAME} to yourself",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        elif not sender.currency >= donation_value:
            await interaction.response.send_message(
                embed=Embed(
                    description=f"You do not have enough {CURRENCY_NAME}. Current Balance: {sender.currency}",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        donation = EconomyDonation(
            sending_player_id=sender.id,
            admin_player_id=None,
            receiving_player_id=receiver.id,
            value=donation_value,
        )
        session.add(donation)
        session.commit()

        try:
            await self.create_transaction(sender, receiver, donation.value, donation)
        except Exception:
            await interaction.response.send_message(
                embed=Embed(
                    description="Donation failed. Exception",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            _log.exception(f"Donation Exception")
            session.delete(donation)
        else:
            sender.currency -= donation_value
            receiver.currency += donation_value
            await interaction.response.send_message(
                embed=Embed(
                    description=f"<@{sender.id}> donated {donation_value} {CURRENCY_NAME} to {receiver.name}",
                    colour=Colour.green(),
                )
            )
        finally:
            session.commit()
            session.close()

    async def resolve_predictions(
        self,
        interaction: Interaction,
        outcome: Literal["win", "loss", "tie"],
        game_id: Optional[str] = None,
    ):
        session: SQLAlchemySession = Session()
        game_player: InProgressGamePlayer | None = (
            session.query(InProgressGamePlayer)
            .filter(InProgressGamePlayer.player_id == interaction.user.id)
            .first()
        )
        if not game_player:
            session.close()
            return

        # Required as /finishgame can be called without a game_id, which infers from game_player
        in_progress_game: InProgressGame | None
        if game_id:
            in_progress_game = (
                session.query(InProgressGame)
                .filter(InProgressGame.id == game_player.in_progress_game_id)
                .filter(InProgressGame.id == game_id)
                .first()
            )
            if not in_progress_game:
                session.close()
                return
        else:
            in_progress_game = (
                session.query(InProgressGame)
                .filter(InProgressGame.id == game_player.in_progress_game_id)
                .first()
            ) 
            if not in_progress_game:
                session.close()
                return
            else:
                game_id = in_progress_game.id

        # Embed created with player award at index 0
        embed: Embed = await EconomyCommands.award_currency(
            self, interaction, in_progress_game
        )

        predictions: list[EconomyPrediction] = (
            session.query(EconomyPrediction)
            .filter(EconomyPrediction.in_progress_game_id == in_progress_game.id)
            .all()
        )
        # Stop processing if no predictions
        if len(predictions) == 0:
            embed.insert_field_at(
                index=0, name="", value=f"No predictions on game", inline=False
            )
        else:
            winning_team = -1
            if outcome == "win":
                winning_team = game_player.team
            elif outcome == "loss":
                winning_team = (game_player.team + 1) % 2
            else:  # tie
                winning_team = -1

            # Cancel prediction on tie
            if winning_team == -1:
                try:
                    await EconomyCommands.cancel_predictions(self, game_id)
                except ValueError as ve:
                    # Raised if there are no predictions on this game
                    embed.insert_field_at(
                        index=0,
                        name="Tie game",
                        value=f"No predictions to be refunded",
                        inline=False,
                    )
                    pass
                except Exception as e:
                    embed.insert_field_at(
                        index=0,
                        name="Tie game",
                        value=f"Predictions failed to refund: {e}",
                        inline=False,
                    )
                    pass
                else:
                    embed.insert_field_at(
                        index=0,
                        name="Tie game",
                        value=f"Predictions refunded",
                        inline=False,
                    )
                    pass
                finally:
                    session.close()
            else:
                # Set up winners & losers
                winning_predictions: list[EconomyPrediction] = []
                losing_predictions: list[EconomyPrediction] = []
                for prediction in predictions:
                    if prediction.team == winning_team:
                        winning_predictions.append(prediction)
                    else:
                        losing_predictions.append(prediction)

                # Cancel if either team has no predicitons
                if len(winning_predictions) == 0 or len(losing_predictions) == 0:
                    try:
                        await EconomyCommands.cancel_predictions(
                            self, game_id
                        )
                    except ValueError as ve:
                        # Raised if there are no predictions on this game
                        embed.insert_field_at(
                            index=0,
                            name=f"Not enough predictions on game",
                            value="No predictions to be refunded",
                            inline=False,
                        )
                        pass
                    except Exception as e:
                        embed.insert_field_at(
                            index=0,
                            name=f"Not enough predictions on game",
                            value=f"Predictions failed to refund: {e}",
                            inline=False,
                        )
                        pass
                    else:
                        embed.insert_field_at(
                            index=0,
                            name=f"Not enough predictions on game",
                            value="Predictions refunded",
                            inline=False,
                        )
                        pass
                    finally:
                        session.close()
                else:
                    winning_total: int = sum(
                        wt.prediction_value for wt in winning_predictions
                    )
                    losing_total: int = sum(
                        lt.prediction_value for lt in losing_predictions
                    )

                    # Initialize dictionary for summing return messages
                    summed_winners: dict = dict()
                    summed_losers: dict = dict()

                    for losing_prediction in losing_predictions:
                        if any(
                            x == losing_prediction.player_id
                            for x in iter(summed_losers.keys())
                        ):
                            summed_losers[
                                losing_prediction.player_id
                            ] += losing_prediction.prediction_value
                        else:
                            summed_losers[losing_prediction.player_id] = (
                                losing_prediction.prediction_value
                            )

                    for winning_prediction in winning_predictions:
                        win_value: int = round(
                            (winning_total + losing_total)
                            * (winning_prediction.prediction_value / winning_total),
                            None,
                        )

                        try:
                            await EconomyCommands.create_transaction(
                                winning_prediction.in_progress_game,
                                winning_prediction.player,
                                win_value,
                                winning_prediction,
                            )
                        except Exception as e:
                            embed.insert_field_at(
                                index=0,
                                name="",
                                value=f"Prediction resolution failed for <@{winning_prediction.player_id}> | Win Value = {win_value} | Exception: {e}",
                                inline=False,
                            )
                            pass
                        else:
                            player: Player | None = (
                                session.query(Player)
                                .filter(Player.id == winning_prediction.player_id)
                                .first()
                            )
                            player.currency += win_value
                            winning_prediction.is_correct = True
                            session.commit()

                            # Adds win_value to player in dictionary, or creates new dict item
                            # Combines multiple predictions into one win value to be returned
                            # Mutliple transactions are still created (one per prediction)
                            if any(
                                x == winning_prediction.player_id
                                for x in iter(summed_winners.keys())
                            ):
                                summed_winners[
                                    winning_prediction.player_id
                                ] += win_value
                            else:
                                summed_winners[winning_prediction.player_id] = win_value

                    sorted_winners: dict = dict(
                        reversed(sorted(summed_winners.items(), key=itemgetter(1)))
                    )
                    sorted_losers: dict = dict(
                        reversed(sorted(summed_losers.items(), key=itemgetter(1)))
                    )

                    prediction_winners: str = ">>> "
                    prediction_losers: str = ">>> "
                    for key, value in list(sorted_winners.items())[:10]:
                        prediction_winners += (
                            f"<@{key}> **+{round(value, None)}** {CURRENCY_NAME}\n"
                        )
                    for key, value in list(sorted_losers.items())[:10]:
                        prediction_losers += (
                            f"<@{key}> **-{round(value, None)}** {CURRENCY_NAME}\n"
                        )

                    if len(sorted_winners) > 10:
                        prediction_winners += "..."
                    if len(sorted_losers) > 10:
                        prediction_losers += "..."

                    embed.insert_field_at(
                        index=0,
                        name="📈 Winners",
                        value=prediction_winners,
                        inline=True,
                    )
                    embed.insert_field_at(
                        index=1, name="📉 Losers", value=prediction_losers, inline=True
                    )

        await interaction.channel.send(embed=embed)
        if CHANNEL_ID and CHANNEL_ID != interaction.channel_id:
            channel: TextChannel | None = get(
                interaction.guild.text_channels, id=CHANNEL_ID
            )
            if channel:
                await channel.send(embed=embed)
        if GAME_HISTORY_CHANNEL and GAME_HISTORY_CHANNEL != interaction.channel_id:
            history_channel: TextChannel | None = get(
                interaction.guild.text_channels, id=GAME_HISTORY_CHANNEL
            )
            if history_channel:
                await history_channel.send(embed=embed)
        session.close()

    @app_commands.command(
        name="showcurrency", description=f"Show how many {CURRENCY_NAME} you have"
    )
    @app_commands.check(economy_enabled)
    async def showcurrency(self, interaction: Interaction):
        session: SQLAlchemySession = Session()
        player: Player | None = (
            session.query(Player).filter(Player.id == interaction.user.id).first()
        )

        if player:
            await interaction.response.send_message(
                embed=Embed(
                    description=f"<@{player.id}> has {player.currency} {CURRENCY_NAME}",
                    colour=Colour.blue(),
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=Embed(
                    description="Player not found",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )

    async def update_embeds(self, in_progress_games: list[InProgressGame]):
        session: SQLAlchemySession = Session()

        for game in in_progress_games:
            if not game.prediction_message_id:
                continue
            igp_channels: list[InProgressGameChannel] = (
                session.query(InProgressGameChannel)
                .filter(InProgressGameChannel.in_progress_game_id == game.id)
                .all()
            )
            for igp_channel in igp_channels:
                channel: TextChannel | VoiceChannel = bot.get_channel(
                    igp_channel.channel_id
                )
                if type(channel) == TextChannel:
                    message: Message = channel.get_partial_message(
                        game.prediction_message_id
                    )
                    if message:
                        message = await message.fetch()
                    else:
                        message = await channel.fetch_message(
                            game.prediction_message_id
                        )

                    predictions: list[EconomyPrediction] = (
                        session.query(EconomyPrediction)
                        .filter(EconomyPrediction.in_progress_game_id == game.id)
                        .all()
                    )

                    team0_total: int = 0
                    team0_predictors: list[str] = []
                    team1_total: int = 0
                    team1_predictors: list[str] = []
                    for prediction in predictions:
                        if prediction.team == 0:
                            team0_total += prediction.prediction_value
                            if not prediction.player_id in team0_predictors:
                                team0_predictors.append(prediction.player_id)
                        else:
                            team1_total += prediction.prediction_value
                            if not prediction.player_id in team1_predictors:
                                team1_predictors.append(prediction.player_id)

                    team0_ratio = "1.0"
                    team1_ratio = "1.0"
                    if not team0_total + team1_total == 0:
                        if not team0_total == 0:
                            team0_ratio = f"{round(1/(team0_total / (team0_total + team1_total)), 1)}"
                        if not team1_total == 0:
                            team1_ratio = f"{round(1/(team1_total / (team1_total + team0_total)), 1)}"

                    embed: Embed = message.embeds[0]
                    embed.set_field_at(
                        index=0,
                        name=embed.fields[0].name,
                        value=f"> Total: {team0_total}\n> Win Ratio: 1:{team0_ratio}\n> Predictors: {len(team0_predictors)}",
                    )
                    embed.set_field_at(
                        index=1,
                        name=embed.fields[1].name,
                        value=f"> Total: {team1_total}\n> Win Ratio: 1:{team1_ratio}\n> Predictors: {len(team1_predictors)}",
                    )

                    await message.edit(embed=embed)
        session.close()


class EconomyPredictionView(View):
    def __init__(self, game_id: str):
        super().__init__(timeout=None)
        self.game_id: str = game_id
        self.game: InProgressGame
        self.add_items()

    async def interaction_check(self, interaction: Interaction[Client]):
        session: SQLAlchemySession = Session()
        player: Player | None = (
            session.query(Player).filter(Player.id == interaction.user.id).first()
        )

        if not player:
            await interaction.response.send_message(
                "You are not a player, please add to queue once to be created",
                ephemeral=True,
            )
            session.close()
            return False

        if self.game.prediction_open:
            session.close()
            return self.game.prediction_open
        else:
            short_game_id: str = short_uuid(self.game.id)
            await interaction.response.send_message(
                f"Prediction is closed for game {short_game_id}",
                ephemeral=True,
            )
            session.close()
            return self.game.prediction_open

    def add_items(self):
        session: SQLAlchemySession = Session()
        game: InProgressGame | None = (
            session.query(InProgressGame)
            .filter(InProgressGame.id.startswith(self.game_id))
            .first()
        )
        self.game = game
        session.close()

        self.add_item(
            EconomyPredictionButton(
                game.team0_name,
                0,
                game,
                ButtonStyle.grey,
                game.team0_name.replace(" ", "_"),
            )
        )
        self.add_item(
            EconomyPredictionButton(
                game.team1_name,
                1,
                game,
                ButtonStyle.grey,
                game.team1_name.replace(" ", "_"),
            )
        )


class EconomyPredictionButton(Button):
    def __init__(
        self,
        team_name: str,
        team_value: int,
        in_progress_game: InProgressGame,
        style: ButtonStyle,
        custom_id: str,
    ):
        super().__init__(
            label=team_name, style=style, custom_id=f"persistent_view:{custom_id}"
        )
        self.game: InProgressGame = in_progress_game
        self.team_value: int = team_value
        self.team_name: str = team_name

    async def callback(self, interaction: Interaction[Client]) -> Any:
        session: SQLAlchemySession = Session()
        player: Player | None = (
            session.query(Player).filter(Player.id == interaction.user.id).first()
        )
        session.close()
        if self.game and player:
            if await self.prediction_check(interaction):
                await interaction.response.send_modal(
                    EconomyPredictionModal(
                        self.team_name, self.team_value, self.game, player
                    ),
                )

    async def prediction_check(self, interaction: Interaction[Client]) -> bool:
        session: SQLAlchemySession = Session()
        ipg_player: InProgressGamePlayer | None = (
            session.query(InProgressGamePlayer)
            .filter(
                InProgressGamePlayer.player_id == interaction.user.id,
                InProgressGamePlayer.in_progress_game_id == self.game.id,
                InProgressGamePlayer.team != self.team_value,
            )
            .first()
        )
        if ipg_player:
            await interaction.response.send_message(
                "You cannot predict against yourself", ephemeral=True
            )
            session.close()
            return False

        predictions: list[EconomyPrediction] = (
            session.query(EconomyPrediction)
            .filter(
                EconomyPrediction.player_id == interaction.user.id,
                EconomyPrediction.in_progress_game_id == self.game.id,
                EconomyPrediction.team != self.team_value,
            )
            .all()
        )
        session.close()
        if predictions:
            await interaction.response.send_message(
                "You cannot predict for both teams", ephemeral=True
            )
            return False
        return True


class EconomyPredictionModal(Modal):
    def __init__(
        self,
        team_name: str,
        team_value: int,
        in_progress_game: InProgressGame,
        player: Player,
    ):
        super().__init__(title=team_name, timeout=30)
        self.game: InProgressGame = in_progress_game
        self.team_name: str = team_name
        self.team_value: int = team_value
        self.input: TextInput = TextInput(
            label="Prediction Value",
            style=TextStyle.short,
            required=True,
            placeholder=f"Current Balance: {player.currency} {CURRENCY_NAME}",
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: Interaction[Client]) -> None:
        try:
            self.value = int(str(self.input))
        except Exception as e:
            await interaction.response.send_message(
                "Prediction value must be an integer", ephemeral=True
            )
            return

        if self.value <= 0:
            await interaction.response.send_message(
                "Prediction value must be greater than 0", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            prediction: EconomyPrediction = await EconomyCommands.create_prediction(
                self, interaction.user, self.game, self.team_value, self.value
            )
            session: SQLAlchemySession = Session()
            sender: Player | None = (
                session.query(Player).filter(Player.id == prediction.player_id).first()
            )
            session.add(prediction)
            session.commit()
        except Exception as e:
            await interaction.followup.send(
                f"Error creating predicton: {e}", ephemeral=True
            )
            return

        try:
            if not sender:
                raise Exception("Player not found for transaction")
            await EconomyCommands.create_transaction(
                sender, self.game, self.value, prediction
            )
        except Exception as e:
            await interaction.followup.send(
                f"Prediction Exception: {e}", ephemeral=True
            )
            session.delete(prediction)
            return
        else:
            sender.currency -= self.value
            await interaction.followup.send(
                embed=Embed(
                    description=f"<@{sender.id}> predicted {self.team_name} for {self.value} {CURRENCY_NAME}",
                    colour=Colour.blue(),
                ),
                ephemeral=True,
            )
        finally:
            session.commit()
            session.close()
