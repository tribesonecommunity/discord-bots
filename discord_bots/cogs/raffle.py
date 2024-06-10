import logging

from discord import Colour, Embed, Interaction, Member, app_commands
from discord.ext.commands import Bot
from emoji import emojize
from sqlalchemy.orm.session import Session as SQLAlchemySession
from sqlalchemy.sql import functions

from discord_bots.checks import is_admin_app_command, is_command_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.models import Map, Player, Rotation, RotationMap, Session
from discord_bots.utils import map_short_name_autocomplete, rotation_autocomplete

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

_log = logging.getLogger(__name__)


class RaffleCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(name="raffle", description="Raffle commands")

    @group.command(
        name="showtickets", description="Displays how many raffle tickets you have"
    )
    @app_commands.check(is_command_channel)
    @app_commands.describe(member="Discord member")
    async def myraffle(self, interaction: Interaction, *, member: Member | None = None):
        """
        Displays how many raffle tickets you have
        """
        member = member or interaction.user
        session: SQLAlchemySession
        with Session() as session:
            player = session.query(Player).filter(Player.id == member.id).first()
            if not player:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"ERROR: Could not find player!",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                embed=Embed(
                    description=f"{emojize(':partying_face:')} You have **{player.raffle_tickets}** raffle tickets!  {emojize(':party_popper:')}",
                    colour=Colour.blue(),
                )
            )

    @group.command(
        name="status",
        description="Displays raffle ticket information and raffle leaderboard",
    )
    @app_commands.check(is_command_channel)
    @app_commands.describe(member="Discord member")
    async def rafflestatus(
        self, interaction: Interaction, *, member: Member | None = None
    ):
        """
        Displays raffle ticket information and raffle leaderboard
        """
        session: SQLAlchemySession
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
            await interaction.response.send_message(
                embed=Embed(
                    description="\n".join(message),
                    colour=Colour.blue(),
                )
            )

    @group.command(
        name="setrotationmapreward",
        description="Set the raffle ticket reward for a map in a rotation",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        rotation_name="Existing rotation",
        map_short_name="Existing map",
        raffle_ticket_reward="Raffle award",
    )
    @app_commands.autocomplete(
        rotation_name=rotation_autocomplete, map_short_name=map_short_name_autocomplete
    )
    @app_commands.rename(rotation_name="rotation", map_short_name="map")
    async def setrotationmapraffle(
        self,
        interaction: Interaction,
        rotation_name: str,
        map_short_name: str,
        raffle_ticket_reward: int,
    ):
        """
        Set the raffle ticket reward for a map in a rotation
        """
        if raffle_ticket_reward < 0:
            await interaction.response.send_message(
                embed=Embed(
                    description="Raffle ticket reward must be positive",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        session: SQLAlchemySession
        with Session() as session:
            rotation_map: RotationMap | None = (
                session.query(RotationMap)
                .join(Map, Map.id == RotationMap.map_id)
                .join(Rotation, Rotation.id == RotationMap.rotation_id)
                .filter(Map.short_name.ilike(map_short_name))
                .filter(Rotation.name.ilike(rotation_name))
                .first()  # type: ignore
            )
            if not rotation_map:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find map **{map_short_name}** in rotation **{rotation_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            rotation_map.raffle_ticket_reward = raffle_ticket_reward
            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=f"Raffle tickets for **{map_short_name}** in **{rotation_name}** set to **{raffle_ticket_reward}**",
                    colour=Colour.blue(),
                )
            )

    @group.command(name="create", description="TODO: Implementation")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(member="Discord member")
    async def createraffle(
        self, interaction: Interaction, *, member: Member | None = None
    ):
        """
        TODO: Implementation
        """
        pass

    @group.command(name="run", description="TODO: Implementation")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(member="Discord member")
    async def runraffle(
        self, interaction: Interaction, *, member: Member | None = None
    ):
        """
        TODO: Implementation
        """
        pass
