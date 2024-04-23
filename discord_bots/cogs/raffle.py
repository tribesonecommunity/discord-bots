from random import choice

import sqlalchemy
from discord import Colour
from discord.ext.commands import Bot, Context, check, command
from discord.member import Member
from emoji import emojize
from sqlalchemy.sql import functions

from discord_bots.checks import is_admin
from discord_bots.cogs.base import BaseCog
from discord_bots.models import Map, Player, Rotation, RotationMap, Session
from discord_bots.utils import send_message

strings = [
    "Don't give up!",
    "Go for it!",
    "Go for the gold!",
    "Go for the win!",
    "Gotta catch 'em all!",
    "Keep going!",
    "Never give up!",
    "Never surrender!",
    "That's amazing!",
    "That's awesome!",
    "That's beautiful!",
    "That's breathtaking!",
    "Wow!",
    "You can do it!",
    "You might win it all!",
    "You're a champ!",
    "You're a hero!",
    "You're a legend!",
    "You're a rockstar!",
    "You're a star!",
    "You're a superstar!",
    "You're a winner in my body!",
    "You're a winner in my book!",
    "You're a winner in my eyes!",
    "You're a winner in my heart!",
    "You're a winner in my mind!",
    "You're a winner in my soul!",
    "You're a winner in my spirit!",
    "You're a winner!",
    "You're a winner!",
    "You're a wizard!",
    "You're a wizard, Harry!",
    "You're amazing!",
    "You're awesome!",
    "You're awesome!",
    "You're beautiful!",
    "You're breathtaking!",
    "You're cool!",
    "You're doing great!",
    "You're fantastic!",
    "You're great!",
    "You're handsome!",
    "You're incredible!",
    "You're lovely!",
    "You're magnificent!",
    "You're marvelous!",
    "You're nearly there!",
    "You're the best!",
]


class RaffleCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    @command()
    async def myraffle(self, ctx, *, member: Member = None):
        """
        Displays how many raffle tickets you have
        """
        member = member or ctx.author
        session: sqlalchemy.orm.Session
        with Session() as session:
            player = session.query(Player).filter(Player.id == member.id).first()
            if not player:
                await send_message(
                    ctx.message.channel,
                    embed_description=f"ERROR: Could not find player!",
                    colour=Colour.red(),
                )
                return
            await send_message(
                ctx.message.channel,
                embed_description=f"{emojize(':partying_face:')} You have **{player.raffle_tickets}** raffle tickets!  {emojize(':party_popper:')}",
                # embed_description=f"{emojize(':partying_face:')} You have **{player.raffle_tickets}** raffle tickets!  {emojize(':party_popper:')} \n\n_{choice(strings)}_",
                colour=Colour.blue(),
            )

    @command()
    async def rafflestatus(self, ctx, *, member: Member = None):
        """
        Displays raffle ticket information and raffle leaderboard
        """
        session: sqlalchemy.orm.Session
        with Session() as session:
            total_tickets = session.query(functions.sum(Player.raffle_tickets)).scalar()
            total_players = (
                session.query(functions.count("*"))
                .filter(Player.raffle_tickets > 0)
                .scalar()
            )
            top_15_players = (
                session.query(Player)
                .filter(Player.raffle_tickets > 0)
                .order_by(Player.raffle_tickets.desc())
                .limit(15)
                .all()
            )
            message = []
            message.append(
                f"**{emojize(':admission_tickets:')} Total tickets:** {total_tickets}\n"
            )
            message.append(f"**Leaderboard:**")
            for player in top_15_players:
                message.append(f"_{player.name}:_ {player.raffle_tickets}")
            await send_message(
                ctx.message.channel,
                embed_description="\n".join(message),
                colour=Colour.blue(),
            )

    @command()
    @check(is_admin)
    async def setrotationmapraffle(
        self, ctx, rotation_name: str, map_short_name: str, raffle_ticket_reward: int
    ):
        """
        Set the raffle ticket reward for a map in a rotation
        """
        if raffle_ticket_reward < 0:
            await self.send_error_message("Raffle ticket reward must be positive")
            return

        session = ctx.session

        rotation_map: RotationMap | None = (
            session.query(RotationMap)
            .join(Map, Map.id == RotationMap.map_id)
            .join(Rotation, Rotation.id == RotationMap.rotation_id)
            .filter(Map.short_name.ilike(map_short_name))
            .filter(Rotation.name.ilike(rotation_name))
            .first()  # type: ignore
        )
        if not rotation_map:
            await self.send_error_message(
                f"Could not find map **{map_short_name}** in rotation **{rotation_name}**"
            )
            return

        rotation_map.raffle_ticket_reward = raffle_ticket_reward
        session.commit()

        await self.send_info_message(
            f"Raffle tickets for **{map_short_name}** in **{rotation_name}** set to **{raffle_ticket_reward}**"
        )

    @command()
    @check(is_admin)
    async def createraffle(self, ctx, *, member: Member = None):
        """
        TODO: Implementation
        """
        pass

    @command()
    @check(is_admin)
    async def runraffle(self, ctx, *, member: Member = None):
        """
        TODO: Implementation
        """
        pass
