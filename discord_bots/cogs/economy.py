from discord.member import Member
from discord.ext.commands import Bot, Context, check, command
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

import discord


class EconomyCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    async def create_transaction(
        self,
        account: Player | FinishedGame | InProgressGame | None,
        destination_account: Player | FinishedGame | InProgressGame | None,
        transaction_value: int,
        source: EconomyDonation | EconomyPrediction | Context,
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

        account.currency -= transaction_value
        destination_account.currency += transaction_value

        try:
            session.commit()
        except IntegrityError as exc:
            print("integrity error?", exc)
            session.rollback()
            raise
        finally:
            session.close()

    @command()
    async def donate(
        self,
        ctx: Context,
        member: Member,
        donation_value: int,
    ):
        message = ctx.message
        session = ctx.session
        sender: Player = (
            session.query(Player).filter(Player.id == message.author.id).first()
        )
        receiver: Player = session.query(Player).filter(Player.id == member.id).first()

        # Check sender & receiver
        if not sender:
            await self.send_error_message("Sending player does not exist")
            return
        elif not receiver:
            await self.send_error_message(
                f"Player {member.display_name} does not exist"
            )
            return
        elif not sender.currency >= donation_value:
            await self.send_error_message(
                f"You do not have enough currency. Current Balance: {sender.currency}"
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
            await self.send_error_message("Donation failed. Type Error")
            print(f"Donation Type Error: {te}")
            session.delete(donation)
        except ValueError as ve:
            await self.send_error_message("Donation failed. Value Error")
            print(f"Donation Value Error: {ve}")
            session.delete(donation)
        except IntegrityError as exc:
            await self.send_error_message("Donation failed. Integrity Error")
            print(f"Donation Integrity Error: {exc}")
            session.delete(donation)
        except Exception as e:
            await self.send_error_message("Donation failed. Exception")
            print(f"Donation Exception: {e}")
            session.delete(donation)
        else:
            await self.send_success_message(
                f"{sender.name} donated {donation_value} to {receiver.name}"
            )
        finally:
            session.commit()
            session.close()

    @command()
    async def showcurrency(self, ctx: Context):
        message = ctx.message
        session = ctx.session
        player: Player = session.query(Player).filter(Player.id == message.author.id).first()

        if player:
            await self.send_info_message(f"{player.name} has {player.currency} {CURRENCY_NAME}")
