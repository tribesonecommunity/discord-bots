from typing import Any
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

# from discord.ext import commands
from discord.ext.commands import Bot, check
from discord.ui import Modal, Select, TextInput, View, Button

from sqlalchemy.exc import IntegrityError

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
        # Is not returning any predictions for some reason
        print(f"{len(predictions)} predictions found for game {game_id}")

        for prediction in predictions:
            try:
                print(f"Prediction Player: {prediction.player_id}")
                EconomyCommands.create_transaction(
                    prediction.in_progress_game, prediction.player,
                    prediction.prediction_value, prediction
                )
            except Exception as e:
                raise
            else:
                player: Player = session.query(Player).filter(Player.id == prediction.player_id).first()
                print(f"Player: {player.name}")
                player.currency += prediction.prediction_value
                session.commit()
            finally:
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
                    source.prediction_id
                    if isinstance(source, EconomyPrediction)
                    else None
                ),
                donation_id=(
                    source.donation_id if isinstance(source, EconomyDonation) else None
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
                    source.prediction_id
                    if isinstance(source, EconomyPrediction)
                    else None
                ),
                donation_id=(
                    source.donation_id if isinstance(source, EconomyDonation) else None
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
                        value=f"> Total: {team0_total}\n> Win Ratio: 1 : {team0_ratio}\n> Predictors: {len(team0_predictors)}",
                    )
                    embed.set_field_at(
                        index=1,
                        name=embed.fields[1].name,
                        value=f"> Total: {team1_total}\n> Win Ratio: 1 : {team1_ratio}\n> Predictors: {len(team1_predictors)}",
                    )

                    await message.edit(embed=embed)
        session.close()


class EconomyPredictionView(View):
    def __init__(self, game_id: str):
        super().__init__(timeout=None)
        self.session = Session()
        self.game_id: str = game_id
        self.game: InProgressGame
        self.add_items(self, self.game_id)

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

        game: InProgressGame = (
            self.session.query(InProgressGame)
            .filter(InProgressGame.id.startswith(interaction.channel.name[-8:]))
            .first()
        )
        self.game_id = game.id
        self.game = game
        for child in self.children:
            if type(child) == EconomyPredictionButton:
                child.in_progress_game = self.game

        prediction_open = self.game.prediction_open

        if prediction_open:
            return prediction_open
        else:
            short_game_id = self.game_id.split("-")[0]
            await interaction.response.send_message(
                f"Prediction is closed for game {(short_game_id)}",
                ephemeral=True,
            )
            return prediction_open

    def add_items(self, interaction: Interaction, game_id: str):
        team0_name: str = ""
        team1_name: str = ""

        # if not self.game_id == "":
        game: InProgressGame = (
            self.session.query(InProgressGame)
            .filter(InProgressGame.id.startswith(self.game_id))
            .first()
        )
        if game:
            team0_name = game.team0_name
            team1_name = game.team1_name

        self.add_item(
            EconomyPredictionButton(
                team0_name, 0, game, ButtonStyle.grey, team0_name.replace(" ", "_")
            )
        )
        self.add_item(
            EconomyPredictionButton(
                team1_name, 1, game, ButtonStyle.grey, team1_name.replace(" ", "_")
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
        game: InProgressGame = (
            session.query(InProgressGame)
            .filter(InProgressGame.id.startswith(interaction.channel.name[-8:]))
            .first()
        )
        player: Player = (
            session.query(Player).filter(Player.id == interaction.user.id).first()
        )
        session.close()
        self.game = game
        if game and player:
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
            await interaction.response.send_message(
                f"Error creating predicton: {e}", ephemeral=True
            )
            return

        try:
            await EconomyCommands.create_transaction(
                sender, self.game, self.value, prediction
            )
        except TypeError as te:
            await interaction.response.send_message(
                f"Prediction Type Error: {te}", ephemeral=True
            )
            session.delete(prediction)
            return
        except ValueError as ve:
            await interaction.response.send_message(
                f"Prediction Value Error: {ve}", ephemeral=True
            )
            session.delete(prediction)
            return
        except IntegrityError as exc:
            await interaction.response.send_message(
                f"Prediction Integrity Error: {exc}", ephemeral=True
            )
            session.delete(prediction)
            return
        except Exception as e:
            await interaction.response.send_message(
                f"Prediction Exception: {e}", ephemeral=True
            )
            session.delete(prediction)
            return
        else:
            sender.currency -= self.value
            await interaction.response.send_message(
                f"{sender.name} predicted {self.team_name} for {self.value} {CURRENCY_NAME}"
            )
        finally:
            session.commit()
            session.close()
