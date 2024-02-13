from discord import app_commands, Interaction, Embed, Colour, ButtonStyle, ActionRow
from discord.member import Member
from discord.ext.commands import Bot, Context, check, command
from discord.ui import View, Button, button

# from discord.ButtonStyle import green
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound

from discord_bots.checks import is_admin
from discord_bots.cogs.base import BaseCog
from discord_bots.config import CURRENCY_NAME
from discord_bots.models import (
    Player,
    FinishedGame,
    InProgressGame,
    Session,
    EconomyDonation,
    EconomyPrediction,
    EconomyTransaction,
)


class EconomyCommands(BaseCog):
    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

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

        if not receiver:
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
            await self.create_transaction(sender, receiver, donation.value, donation)
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

    async def create_transaction(
        self,
        account: Player | FinishedGame | InProgressGame,
        destination_account: Player | FinishedGame | InProgressGame | None,
        transaction_value: int,
        source: EconomyDonation | EconomyPrediction,
    ):
        session = Session()

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

    @app_commands.command(
        name="showcurrency", description=f"Show how many {CURRENCY_NAME} you have"
    )
    async def showcurrency(self, interaction: Interaction):
        session = Session()
        player: Player = (
            session.query(Player).filter(Player.id == interaction.user.id).first()
        )

        if player:
            await interaction.response.send_message(
                embed=Embed(
                    description=f"{player.name} has {player.currency} {CURRENCY_NAME}",
                    colour=Colour.blue(),
                ),
            )

    @app_commands.command(name="predict", description="Test predictions")
    async def predict(self, interaction: Interaction) -> None:
        try:
            await interaction.response.send_message(
                embed=Embed(
                    description="Prediction embed test",
                    colour=Colour.green(),
                ),
                view=EconomyButtons(),
            )
        except Exception as e:
            await interaction.response.send_message(
                embed=Embed(description=f"Exception: {e}", colour=Colour.red()),
                ephemeral=True,
            )


class EconomyButtons(View):
    def __init__(self):
        super().__init__()
        self.value = None

    @button(label="Team 0", style=ButtonStyle.green)
    async def Team0(self, interaction: Interaction, button: Button):
        await interaction.response.send_message("Predicted team 0", ephemeral=True)
        self.value = True
        self.stop()

    @button(label="Team 1", style=ButtonStyle.green)
    async def Team0(self, interaction: Interaction, button: Button):
        await interaction.response.send_message("Predicted team 1", ephemeral=True)
        self.value = True
        self.stop()
