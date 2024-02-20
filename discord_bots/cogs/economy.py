from typing import Any, Literal, Optional
from datetime import datetime, timedelta, timezone
from pytz import utc

from discord import (
    app_commands,
    ButtonStyle,
    Client,
    Colour,
    Embed,
    Interaction,
    Message,
    TextChannel,
    TextStyle,
    VoiceChannel,
)
from discord.member import Member
from discord.ext.commands import Bot, check, Cog
from discord.ui import Modal, TextInput, View, Button

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.bot import bot
from discord_bots.checks import is_admin
from discord_bots.cogs.base import BaseCog
from discord_bots.config import ECONOMY_ENABLED, CURRENCY_NAME, PREDICTION_TIMEOUT
from discord_bots.models import (
    Player,
    FinishedGame,
    InProgressGame,
    InProgressGameChannel,
    InProgressGamePlayer,
    Session,
    EconomyDonation,
    EconomyPrediction,
    EconomyTransaction,
)


class EconomyCommands(BaseCog):
    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        """
        Called every time the bot restarts
        Recreates view and re-links to existing message id
        """
        if not ECONOMY_ENABLED:
            return

        session = Session()
        in_progress_games: list[InProgressGame] = session.query(InProgressGame).all()
        for game in in_progress_games:
            if game.prediction_message_id:
                self.bot.add_view(
                    EconomyPredictionView(game.id),
                    message_id=game.prediction_message_id,
                )
        session.close()

    @app_commands.command(
        name="addcurrency", description=f"Admin; Add {CURRENCY_NAME} to a player"
    )
    @check(is_admin)
    async def addcurrency(
        self, interaction: Interaction, member: Member, add_value: int
    ) -> None:
        session = Session()
        sender = Player(id=None, name="Admin")
        receiver: Player = session.query(Player).filter(Player.id == member.id).first()

        if not ECONOMY_ENABLED:
            await interaction.response.send_message(
                embed=Embed(
                    description="Player economy is disabled",
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
            sending_player_id=sender.id,
            sending_player_name=sender.name,
            receiving_player_id=receiver.id,
            receiving_player_name=receiver.name,
            value=add_value,
        )
        session.add(donation)
        session.commit()

        try:
            await EconomyCommands.create_transaction(
                sender, receiver, donation.value, donation
            )
        except TypeError as te:
            await interaction.response.send_message(
                embed=Embed(
                    description="Currency add failed. Type Error",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            print(f"Currency add Type Error: {te}")
            session.delete(donation)
        except ValueError as ve:
            await interaction.response.send_message(
                embed=Embed(
                    description="Currency add failed. Value Error",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            print(f"Currency add Value Error: {ve}")
            session.delete(donation)
        except IntegrityError as exc:
            await interaction.response.send_message(
                embed=Embed(
                    description="Currency add failed. Integrity Error",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            print(f"Currency add Integrity Error: {exc}")
            session.delete(donation)
        except Exception as e:
            await interaction.response.send_message(
                embed=Embed(
                    description="Currency add failed. Exception",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            print(f"Currency add Exception: {e}")
            session.delete(donation)
        else:
            receiver.currency += add_value
            await interaction.response.send_message(
                embed=Embed(
                    description=f"{interaction.user.display_name} added {add_value} {CURRENCY_NAME} to {receiver.name}",
                    colour=Colour.green(),
                )
            )
        finally:
            session.commit()
            session.close()

    async def cancel_predictions(interaction: Interaction, game_id: str):
        session = Session()

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
            except Exception as e:
                print(f"Exception while refunding predictions for game {game_id}: {e}")
                raise
            else:
                player: Player = (
                    session.query(Player)
                    .filter(Player.id == prediction.player_id)
                    .first()
                )
                player.currency += prediction.prediction_value
                session.commit()
        session.close()

    async def close_predictions(in_progress_games: list[InProgressGame]):
        session = Session()
        for game in in_progress_games:
            time_compare: datetime = game.created_at + timedelta(
                seconds=PREDICTION_TIMEOUT
            )
            time_compare = utc.localize(time_compare)
            if datetime.now(timezone.utc) > time_compare:
                game.prediction_open = False
                session.commit()
        session.close()

    async def create_prediction_message(
        in_progress_game: InProgressGame, match_channel: TextChannel
    ) -> int | None:
        # async def predict(self, interaction: Interaction) -> None:

        if not ECONOMY_ENABLED:
            return None

        short_game_id: str = in_progress_game.id.split("-")[0]
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
        except Exception as e:
            print(f"prediction failed for game: {in_progress_game.id}")
            return None
        else:
            return prediction_message.id

    async def _create_prediction(
        member: Member, game: InProgressGame, selection: int, prediction_value: int
    ) -> EconomyPrediction:
        session = Session()
        sender: Player = session.query(Player).filter(Player.id == member.id).first()

        if not ECONOMY_ENABLED:
            raise Exception("Player economy is disabled")
        elif not sender:
            raise Exception("Player does not exist")
        elif not game:
            raise Exception("Game does not exist")
        elif not sender.currency >= prediction_value:
            raise ValueError(
                f"You do not have enough {CURRENCY_NAME}. Current Balance: {sender.currency}"
            )

        prediction = EconomyPrediction(
            player_id=sender.id,
            player_name=sender.name,
            in_progress_game_id=game.id,
            finished_game_id=None,
            team=int(selection),
            prediction_value=prediction_value,
            outcome=None,
        )
        return prediction

    @classmethod
    async def create_transaction(
        self,
        account: Player | FinishedGame | InProgressGame,
        destination_account: Player | FinishedGame | InProgressGame | None,
        transaction_value: int,
        source: EconomyDonation | EconomyPrediction,
    ):
        session = Session()

        if not ECONOMY_ENABLED:
            raise Exception("Player economy is disabled")
        if not account:
            raise TypeError("Transaction account not a player or game")
        elif not destination_account:
            raise TypeError("Destination account not a player or game")

        if not source:
            source_str = "Manual"
        else:
            source_str = source.__class__.__name__

        # Outbound Transaction
        session.add(
            EconomyTransaction(
                player_id=(account.id if isinstance(account, Player) else None),
                player_name=(account.name if isinstance(account, Player) else None),
                finished_game_id=(
                    account.game_id if isinstance(account, FinishedGame) else None
                ),
                in_progress_game_id=(
                    account.id if isinstance(account, InProgressGame) else None
                ),
                debit=0,
                credit=transaction_value,
                transaction_type=source_str,
                prediction_id=(
                    source.id if isinstance(source, EconomyPrediction) else None
                ),
                donation_id=(
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
                player_name=(
                    destination_account.name
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
                transaction_type=source_str,
                prediction_id=(
                    source.id if isinstance(source, EconomyPrediction) else None
                ),
                donation_id=(
                    source.id if isinstance(source, EconomyDonation) else None
                ),
            )
        )

        try:
            session.commit()
        except IntegrityError as exc:
            print("integrity error?", exc)
            session.rollback()
            raise
        finally:
            session.close()

    @app_commands.command(
        name="donate", description=f"Donate {CURRENCY_NAME} to another player"
    )
    async def donate(
        self, interaction: Interaction, member: Member, donation_value: int
    ) -> None:
        session = Session()
        sender: Player = (
            session.query(Player).filter(Player.id == interaction.user.id).first()
        )
        receiver: Player = session.query(Player).filter(Player.id == member.id).first()

        # Check sender & receiver
        if not ECONOMY_ENABLED:
            await interaction.response.send_message(
                embed=Embed(
                    description="Player economy is disabled",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        elif not sender:
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
            sending_player_name=sender.name,
            receiving_player_id=receiver.id,
            receiving_player_name=receiver.name,
            value=donation_value,
        )
        session.add(donation)
        session.commit()

        try:
            await self.create_transaction(sender, receiver, donation.value, donation)
        except TypeError as te:
            await interaction.response.send_message(
                embed=Embed(
                    description="Donation failed. Type Error",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            print(f"Donation Type Error: {te}")
            session.delete(donation)
        except ValueError as ve:
            await interaction.response.send_message(
                embed=Embed(
                    description="Donation failed. Value Error",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            print(f"Donation Value Error: {ve}")
            session.delete(donation)
        except IntegrityError as exc:
            await interaction.response.send_message(
                embed=Embed(
                    description="Donation failed. Integrity Error",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            print(f"Donation Integrity Error: {exc}")
            session.delete(donation)
        except Exception as e:
            await interaction.response.send_message(
                embed=Embed(
                    description="Donation failed. Exception",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            print(f"Donation Exception: {e}")
            session.delete(donation)
        else:
            sender.currency -= donation_value
            receiver.currency += donation_value
            await interaction.response.send_message(
                embed=Embed(
                    description=f"{sender.name} donated {donation_value} {CURRENCY_NAME} to {receiver.name}",
                    colour=Colour.green(),
                )
            )
        finally:
            session.commit()
            session.close()
        interaction.response.send_message()

    async def resolve_predictions(
        interaction: Interaction,
        outcome: Literal["win", "loss", "tie"],
        game_id: Optional[str],
    ):
        session: SQLAlchemySession = Session()
        game_player: InProgressGamePlayer = (
            session.query(InProgressGamePlayer)
            .filter(InProgressGamePlayer.player_id == interaction.user.id)
            .first()
        )
        if not game_player:
            session.close()
            return

        if game_id:
            in_progress_game: InProgressGame | None = (
                session.query(InProgressGame)
                .filter(InProgressGame.id == game_player.in_progress_game_id)
                .filter(InProgressGame.id == game_id)
                .first()
            )
            if not in_progress_game:
                session.close()
                return
        else:
            in_progress_game: InProgressGame | None = (
                session.query(InProgressGame)
                .filter(InProgressGame.id == game_player.in_progress_game_id)
                .first()
            )
            game_id = in_progress_game.id
            if not in_progress_game:
                session.close()
                return

        predictions: list[EconomyPrediction] = (
            session.query(EconomyPrediction)
            .filter(EconomyPrediction.in_progress_game_id == in_progress_game.id)
            .all()
        )
        # Stop processing if no predictions
        if len(predictions) == 0:
            short_game_id: str = in_progress_game.id.split("-")[0]
            await interaction.channel.send(
                embed=Embed(
                    description=f"No predictions on game {short_game_id}",
                    colour=Colour.blue(),
                )
            )
            return

        winning_team = -1
        if outcome == "win":
            winning_team = game_player.team
        elif outcome == "loss":
            winning_team = (game_player.team + 1) % 2
        else:  # tie
            winning_team = -1
        print(f"Wining Team: {winning_team}")
        
        # Cancel prediction on tie
        if winning_team == -1:
            try:
                await EconomyCommands.cancel_predictions(interaction, game_id)
            except ValueError as ve:
                # Raised if there are no predictions on this game
                await interaction.channel.send(
                    embed=Embed(
                        description="No predictions to be refunded",
                        colour=Colour.blue(),
                    )
                )
            except Exception as e:
                await interaction.channel.send(
                    embed=Embed(
                        description=f"Predictions failed to refund: {e}",
                        colour=Colour.red(),
                    )
                )
            else:
                await interaction.channel.send(
                    embed=Embed(
                        description="Predictions refunded", colour=Colour.blue()
                    )
                )
            finally:
                session.close()
                return

        # Set up winners & losers
        winning_predictions: list[EconomyPrediction] = []
        losing_predictions: list[EconomyPrediction] = []
        for prediction in predictions:
            if prediction.team == winning_team:
                winning_predictions.append(prediction)
            else:
                losing_predictions.append(prediction)
        print(f"Winning prediction count: {len(winning_predictions)}")
        print(f"Losing prediction count: {len(losing_predictions)}")
        
        # Cancel if either team has no predicitons
        if len(winning_predictions) == 0 or len(losing_predictions) == 0:
            short_game_id: str = in_progress_game.id.split("-")[0]
            await interaction.channel.send(
                embed=Embed(
                    description=f"Not enough predictions on game {short_game_id}",
                    colour=Colour.blue(),
                )
            )
            try:
                await EconomyCommands.cancel_predictions(interaction, game_id)
            except ValueError as ve:
                # Raised if there are no predictions on this game
                await interaction.channel.send(
                    embed=Embed(
                        description="No predictions to be refunded",
                        colour=Colour.blue(),
                    )
                )
            except Exception as e:
                await interaction.channel.send(
                    embed=Embed(
                        description=f"Predictions failed to refund: {e}",
                        colour=Colour.red(),
                    )
                )
            else:
                await interaction.channel.send(
                    embed=Embed(
                        description="Predictions refunded", colour=Colour.blue()
                    )
                )
            finally:
                session.close()
                return

        winning_total: int = sum(wt.prediction_value for wt in winning_predictions)
        losing_total: int = sum(lt.prediction_value for lt in losing_predictions)
        print(f"Winning total: {winning_total}")
        print(f"Losing total: {losing_total}")
        
        # Initialize dictionary for summing return messages
        summed_winners: dict = dict()

        for winning_prediction in winning_predictions:
            win_value: int = round(
                (winning_total + losing_total)
                * (winning_prediction.prediction_value / winning_total),
                None,
            )
            print(f"Win Value: {win_value}")

            try:
                await EconomyCommands.create_transaction(
                    winning_prediction.in_progress_game,
                    winning_prediction.player,
                    win_value,
                    winning_prediction,
                )
            except Exception as e:
                await interaction.channel.send(
                    embed=Embed(
                        description="No predictions to be refunded",
                        colour=Colour.blue(),
                    )
                )
            else:
                player: Player = (
                    session.query(Player)
                    .filter(Player.id == winning_prediction.player_id)
                    .first()
                )
                player.currency += win_value
                winning_prediction.outcome = True
                session.commit()
                
                # Adds win_value to player in dictionary, or creates new dict item
                # Combines multiple predictions into one win value to be returned
                # Mutliple transactions are still created (one per prediction)
                if any(x == winning_prediction.player_name for x in iter(summed_winners.keys())):
                    summed_winners[winning_prediction.player_name] += win_value
                else:
                    summed_winners[winning_prediction.player_name] = win_value
        
        for key, value in summed_winners.items():
            await interaction.channel.send(
                embed=Embed(
                    description=f"{key} won {round(value, None)} {CURRENCY_NAME}!",
                    colour=Colour.blue(),
                )
            )

        session.close()

    @app_commands.command(
        name="showcurrency", description=f"Show how many {CURRENCY_NAME} you have"
    )
    async def showcurrency(self, interaction: Interaction):
        session = Session()
        player: Player = (
            session.query(Player).filter(Player.id == interaction.user.id).first()
        )

        if not ECONOMY_ENABLED:
            await interaction.response.send_message(
                embed=Embed(
                    description="Player economy is disabled",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        elif player:
            await interaction.response.send_message(
                embed=Embed(
                    description=f"{player.name} has {player.currency} {CURRENCY_NAME}",
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

    async def update_embeds(in_progress_games: list[InProgressGame]):
        session = Session()

        for game in in_progress_games:
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
                    # message: Message = channel.fetch_message(game.prediction_message_id)
                    message: Message = channel.get_partial_message(
                        game.prediction_message_id
                    )
                    if message:
                        message = await message.fetch()
                    else:
                        message = channel.fetch_message(game.prediction_message_id)

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
        self.session = Session()
        self.add_items()

    async def interaction_check(self, interaction: Interaction[Client]):
        player: Player = (
            self.session.query(Player).filter(Player.id == interaction.user.id).first()
        )
        if not player:
            await interaction.response.send_message(
                "You are not a player, please add to queue once to be created",
                ephemeral=True,
            )
            return False

        if self.game.prediction_open:
            return self.game.prediction_open
        else:
            short_game_id: str = self.game.id.split("-")[0]
            await interaction.response.send_message(
                f"Prediction is closed for game {short_game_id}",
                ephemeral=True,
            )
            return self.game.prediction_open

    def add_items(self):
        game: InProgressGame = (
            self.session.query(InProgressGame)
            .filter(InProgressGame.id.startswith(self.game_id))
            .first()
        )
        self.game = game

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

    async def callback(self, interaction: Interaction[Client]) -> Any:
        session = Session()
        player: Player = (
            session.query(Player).filter(Player.id == interaction.user.id).first()
        )
        session.close()
        if self.game and player:
            if await self.prediction_check(interaction):
                await interaction.response.send_modal(
                    EconomyPredictionModal(
                        self.label, self.team_value, self.game, player
                    )
                )

    async def prediction_check(self, interaction: Interaction[Client]) -> bool:
        session = Session()
        ipg_player: InProgressGamePlayer = (
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
        self.input: TextChannel = TextInput(
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

        await interaction.response.defer(thinking=True)
        try:
            prediction: EconomyPrediction = await EconomyCommands._create_prediction(
                interaction.user, self.game, self.team_value, self.value
            )
            session = Session()
            sender: Player = (
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
            await EconomyCommands.create_transaction(
                sender, self.game, self.value, prediction
            )
        except TypeError as te:
            await interaction.followup.send(
                f"Prediction Type Error: {te}", ephemeral=True
            )
            session.delete(prediction)
            return
        except ValueError as ve:
            await interaction.followup.send(
                f"Prediction Value Error: {ve}", ephemeral=True
            )
            session.delete(prediction)
            return
        except IntegrityError as exc:
            await interaction.followup.send(
                f"Prediction Integrity Error: {exc}", ephemeral=True
            )
            session.delete(prediction)
            return
        except Exception as e:
            await interaction.followup.send(
                f"Prediction Exception: {e}", ephemeral=True
            )
            session.delete(prediction)
            return
        else:
            sender.currency -= self.value
            await interaction.followup.send(
                f"{sender.name} predicted {self.team_name} for {self.value} {CURRENCY_NAME}"
            )
        finally:
            session.commit()
            session.close()
