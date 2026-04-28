import logging

from discord import Colour, Embed, Interaction, Member, TextChannel, app_commands
from discord.ext.commands import Bot
from discord.utils import escape_markdown
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.checks import is_admin_app_command, is_ladder_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.models import (
    Ladder,
    LadderMatch,
    LadderTeam,
    LadderTeamInvite,
    LadderTeamPlayer,
    Player,
    Rotation,
    Session,
)
from discord_bots.utils import (
    ladder_autocomplete,
    ladder_team_autocomplete,
    rotation_autocomplete,
)

_log = logging.getLogger(__name__)

MAX_MAPS_PER_MATCH = 5
IN_FLIGHT_STATUSES = ("pending", "accepted")


def _ensure_player(session: SQLAlchemySession, member: Member) -> Player:
    """Look up a Player row by Discord member, creating it if missing."""
    player: Player | None = session.query(Player).filter(Player.id == member.id).first()
    if not player:
        player = Player(id=member.id, name=member.name)
        session.add(player)
        session.flush()
    return player


def _find_ladder(session: SQLAlchemySession, name: str) -> Ladder | None:
    return session.query(Ladder).filter(Ladder.name == name).first()


def _find_team(
    session: SQLAlchemySession, ladder_id: str, name: str
) -> LadderTeam | None:
    return (
        session.query(LadderTeam)
        .filter(LadderTeam.ladder_id == ladder_id, LadderTeam.name == name)
        .first()
    )


def _find_player_team_in_ladder(
    session: SQLAlchemySession, ladder_id: str, player_id: int
) -> LadderTeam | None:
    return (
        session.query(LadderTeam)
        .join(LadderTeamPlayer, LadderTeamPlayer.team_id == LadderTeam.id)
        .filter(
            LadderTeam.ladder_id == ladder_id,
            LadderTeamPlayer.player_id == player_id,
        )
        .first()
    )


def _team_roster_size(session: SQLAlchemySession, team_id: str) -> int:
    return (
        session.query(func.count(LadderTeamPlayer.id))
        .filter(LadderTeamPlayer.team_id == team_id)
        .scalar()
        or 0
    )


def _team_has_in_flight_match(session: SQLAlchemySession, team_id: str) -> bool:
    return (
        session.query(LadderMatch.id)
        .filter(
            LadderMatch.status.in_(IN_FLIGHT_STATUSES),
            (LadderMatch.challenger_team_id == team_id)
            | (LadderMatch.defender_team_id == team_id),
        )
        .first()
        is not None
    )


def _next_position(session: SQLAlchemySession, ladder_id: str) -> int:
    max_pos = (
        session.query(func.max(LadderTeam.position))
        .filter(LadderTeam.ladder_id == ladder_id)
        .scalar()
    )
    return (max_pos or 0) + 1


async def _err(interaction: Interaction, msg: str) -> None:
    embed = Embed(description=msg, colour=Colour.red())
    if not interaction.response.is_done():
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.followup.send(embed=embed, ephemeral=True)


async def _ok(interaction: Interaction, msg: str, ephemeral: bool = False) -> None:
    embed = Embed(description=msg, colour=Colour.green())
    if not interaction.response.is_done():
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
    else:
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)


class LadderCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    ladder_group = app_commands.Group(
        name="ladder", description="Challenge ladder commands"
    )
    admin_group = app_commands.Group(
        name="admin", parent=ladder_group, description="Ladder admin commands"
    )
    team_group = app_commands.Group(
        name="team", parent=ladder_group, description="Ladder team commands"
    )

    @ladder_group.command(name="list", description="List all ladders")
    @app_commands.check(is_ladder_channel)
    async def list_ladders(self, interaction: Interaction):
        session: SQLAlchemySession
        with Session() as session:
            ladders: list[Ladder] = session.query(Ladder).order_by(Ladder.name).all()
            if not ladders:
                await interaction.response.send_message(
                    embed=Embed(
                        description="No ladders configured.",
                        colour=Colour.blue(),
                    )
                )
                return

            embed = Embed(title="Ladders", colour=Colour.blue())
            for ladder in ladders:
                rotation: Rotation | None = (
                    session.query(Rotation)
                    .filter(Rotation.id == ladder.rotation_id)
                    .first()
                )
                rotation_name = rotation.name if rotation else "(missing)"
                lines = [
                    f"Rotation: **{rotation_name}**",
                    f"Maps per match: **{ladder.maps_per_match}**",
                    f"Max team size: **{ladder.max_team_size}**",
                    f"Challenge distance: **{ladder.max_challenge_distance}**",
                    f"Active: **{ladder.is_active}**",
                ]
                embed.add_field(name=ladder.name, value="\n".join(lines), inline=False)
            await interaction.response.send_message(embed=embed)

    @admin_group.command(name="create", description="Create a new ladder")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.describe(
        name="Unique ladder name",
        rotation="Existing rotation that supplies maps for matches",
        maps_per_match=f"Number of maps per match (1-{MAX_MAPS_PER_MATCH})",
        max_team_size="Required roster size for matches",
    )
    @app_commands.autocomplete(rotation=rotation_autocomplete)
    async def create_ladder(
        self,
        interaction: Interaction,
        name: str,
        rotation: str,
        maps_per_match: int,
        max_team_size: int,
    ):
        if maps_per_match < 1 or maps_per_match > MAX_MAPS_PER_MATCH:
            await interaction.response.send_message(
                embed=Embed(
                    description=(
                        f"`maps_per_match` must be between 1 and "
                        f"{MAX_MAPS_PER_MATCH}."
                    ),
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        if max_team_size < 1:
            await interaction.response.send_message(
                embed=Embed(
                    description="`max_team_size` must be at least 1.",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        session: SQLAlchemySession
        with Session() as session:
            rotation_row: Rotation | None = (
                session.query(Rotation).filter(Rotation.name == rotation).first()
            )
            if not rotation_row:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Rotation **{rotation}** not found.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            try:
                ladder = Ladder(
                    name=name,
                    rotation_id=rotation_row.id,
                    maps_per_match=maps_per_match,
                    max_team_size=max_team_size,
                )
                session.add(ladder)
                session.commit()
            except IntegrityError:
                session.rollback()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Ladder **{name}** already exists.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                embed=Embed(
                    description=(
                        f"Ladder **{name}** created on rotation "
                        f"**{rotation}** with {maps_per_match} maps per "
                        f"match and team size {max_team_size}."
                    ),
                    colour=Colour.green(),
                )
            )

    @admin_group.command(name="delete", description="Delete a ladder")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.describe(ladder="Ladder to delete")
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    async def delete_ladder(self, interaction: Interaction, ladder: str):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row: Ladder | None = (
                session.query(Ladder).filter(Ladder.name == ladder).first()
            )
            if not ladder_row:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Ladder **{ladder}** not found.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            session.delete(ladder_row)
            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=f"Ladder **{ladder}** deleted.",
                    colour=Colour.green(),
                )
            )

    @admin_group.command(
        name="setchannels",
        description="Set leaderboard and history channels for a ladder",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.describe(
        ladder="Ladder to configure",
        leaderboard_channel="Channel where the leaderboard message will be posted",
        history_channel="Channel where match history will be posted",
    )
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    async def set_channels(
        self,
        interaction: Interaction,
        ladder: str,
        leaderboard_channel: TextChannel,
        history_channel: TextChannel,
    ):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row: Ladder | None = (
                session.query(Ladder).filter(Ladder.name == ladder).first()
            )
            if not ladder_row:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Ladder **{ladder}** not found.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            ladder_row.leaderboard_channel_id = leaderboard_channel.id
            ladder_row.leaderboard_message_id = None
            ladder_row.history_channel_id = history_channel.id
            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=(
                        f"Ladder **{ladder}** channels set. "
                        f"Leaderboard: {leaderboard_channel.mention}, "
                        f"History: {history_channel.mention}."
                    ),
                    colour=Colour.green(),
                )
            )

    @admin_group.command(
        name="setmapspermatch",
        description="Set the number of maps per match for a ladder",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(
        ladder="Ladder to configure",
        value=f"Maps per match (1-{MAX_MAPS_PER_MATCH})",
    )
    async def set_maps_per_match(
        self, interaction: Interaction, ladder: str, value: int
    ):
        if value < 1 or value > MAX_MAPS_PER_MATCH:
            await interaction.response.send_message(
                embed=Embed(
                    description=(
                        f"`maps_per_match` must be between 1 and "
                        f"{MAX_MAPS_PER_MATCH}."
                    ),
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        await self._set_int_field(
            interaction, ladder, "maps_per_match", value, "Maps per match"
        )

    @admin_group.command(
        name="setmaxteamsize",
        description="Set the required roster size for a ladder",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder to configure", value="Roster size")
    async def set_max_team_size(
        self, interaction: Interaction, ladder: str, value: int
    ):
        if value < 1:
            await interaction.response.send_message(
                embed=Embed(
                    description="`max_team_size` must be at least 1.",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        await self._set_int_field(
            interaction, ladder, "max_team_size", value, "Max team size"
        )

    @admin_group.command(
        name="setchallengedistance",
        description="Set the maximum challenge distance for a ladder",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(
        ladder="Ladder to configure",
        value="Max positions a team may challenge above itself",
    )
    async def set_challenge_distance(
        self, interaction: Interaction, ladder: str, value: int
    ):
        if value < 1:
            await interaction.response.send_message(
                embed=Embed(
                    description="`max_challenge_distance` must be at least 1.",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        await self._set_int_field(
            interaction,
            ladder,
            "max_challenge_distance",
            value,
            "Max challenge distance",
        )

    @admin_group.command(
        name="setactive",
        description="Enable or disable writes for a ladder",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder to configure", value="True or False")
    async def set_active(self, interaction: Interaction, ladder: str, value: bool):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row: Ladder | None = (
                session.query(Ladder).filter(Ladder.name == ladder).first()
            )
            if not ladder_row:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Ladder **{ladder}** not found.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            ladder_row.is_active = value
            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=(f"Ladder **{ladder}** is_active set to **{value}**."),
                    colour=Colour.green(),
                )
            )

    async def _set_int_field(
        self,
        interaction: Interaction,
        ladder_name: str,
        column: str,
        value: int,
        display_name: str,
    ) -> None:
        session: SQLAlchemySession
        with Session() as session:
            ladder_row: Ladder | None = (
                session.query(Ladder).filter(Ladder.name == ladder_name).first()
            )
            if not ladder_row:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Ladder **{ladder_name}** not found.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            setattr(ladder_row, column, value)
            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=(
                        f"Ladder **{ladder_name}** {display_name} set to "
                        f"**{value}**."
                    ),
                    colour=Colour.green(),
                )
            )

    # ------------------------------------------------------------------
    # Team commands
    # ------------------------------------------------------------------

    @team_group.command(
        name="create", description="Create a new ladder team (you become captain)"
    )
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder to create the team in", name="Team name")
    async def team_create(self, interaction: Interaction, ladder: str, name: str):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            if not ladder_row.is_active:
                await _err(interaction, f"Ladder **{ladder}** is inactive.")
                return

            existing = _find_player_team_in_ladder(
                session, ladder_row.id, interaction.user.id
            )
            if existing:
                await _err(
                    interaction,
                    f"You are already on team **{existing.name}** in this ladder.",
                )
                return

            captain = _ensure_player(session, interaction.user)
            position = _next_position(session, ladder_row.id)

            try:
                team = LadderTeam(
                    ladder_id=ladder_row.id,
                    name=name,
                    captain_id=captain.id,
                    position=position,
                )
                session.add(team)
                session.flush()
                session.add(LadderTeamPlayer(team_id=team.id, player_id=captain.id))
                session.commit()
            except IntegrityError:
                session.rollback()
                await _err(
                    interaction, f"Team **{name}** already exists in this ladder."
                )
                return

            await _ok(
                interaction,
                (
                    f"Team **{escape_markdown(name)}** created in ladder "
                    f"**{ladder}** at position {position}. You are the captain."
                ),
            )

    @team_group.command(name="invite", description="Invite a player to your team")
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder", member="Player to invite")
    async def team_invite(self, interaction: Interaction, ladder: str, member: Member):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            if not ladder_row.is_active:
                await _err(interaction, f"Ladder **{ladder}** is inactive.")
                return

            team = _find_player_team_in_ladder(
                session, ladder_row.id, interaction.user.id
            )
            if not team:
                await _err(interaction, "You are not on a team in this ladder.")
                return
            if team.captain_id != interaction.user.id:
                await _err(interaction, "Only the team captain can invite players.")
                return

            roster_size = _team_roster_size(session, team.id)
            if roster_size >= ladder_row.max_team_size:
                await _err(
                    interaction,
                    (
                        f"Team is at the roster cap "
                        f"({ladder_row.max_team_size}). Kick someone first."
                    ),
                )
                return

            invitee = _ensure_player(session, member)
            if invitee.id == interaction.user.id:
                await _err(interaction, "You can't invite yourself.")
                return

            existing_team = _find_player_team_in_ladder(
                session, ladder_row.id, invitee.id
            )
            if existing_team:
                await _err(
                    interaction,
                    (
                        f"{escape_markdown(member.name)} is already on team "
                        f"**{existing_team.name}** in this ladder."
                    ),
                )
                return

            try:
                session.add(
                    LadderTeamInvite(
                        team_id=team.id,
                        player_id=invitee.id,
                        invited_by_id=interaction.user.id,
                    )
                )
                session.commit()
            except IntegrityError:
                session.rollback()
                await _err(
                    interaction,
                    (
                        f"{escape_markdown(member.name)} already has a pending "
                        f"invite to **{team.name}**."
                    ),
                )
                return

            await _ok(
                interaction,
                (
                    f"Invited {member.mention} to **{escape_markdown(team.name)}**. "
                    f"They can accept with `/ladder team accept ladder:{ladder} "
                    f"team_name:{team.name}`."
                ),
            )

    @team_group.command(
        name="uninvite", description="Revoke a pending invite from your team"
    )
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder", member="Player whose invite to revoke")
    async def team_uninvite(
        self, interaction: Interaction, ladder: str, member: Member
    ):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return

            team = _find_player_team_in_ladder(
                session, ladder_row.id, interaction.user.id
            )
            if not team or team.captain_id != interaction.user.id:
                await _err(interaction, "Only the team captain can revoke invites.")
                return

            invite: LadderTeamInvite | None = (
                session.query(LadderTeamInvite)
                .filter(
                    LadderTeamInvite.team_id == team.id,
                    LadderTeamInvite.player_id == member.id,
                )
                .first()
            )
            if not invite:
                await _err(
                    interaction,
                    f"No pending invite for {escape_markdown(member.name)}.",
                )
                return
            session.delete(invite)
            session.commit()

            await _ok(
                interaction,
                f"Invite for {member.mention} to **{team.name}** revoked.",
            )

    @team_group.command(name="accept", description="Accept an invite to a team")
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(
        ladder=ladder_autocomplete, team_name=ladder_team_autocomplete
    )
    @app_commands.describe(ladder="Ladder", team_name="Team you were invited to")
    async def team_accept(self, interaction: Interaction, ladder: str, team_name: str):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            if not ladder_row.is_active:
                await _err(interaction, f"Ladder **{ladder}** is inactive.")
                return
            team = _find_team(session, ladder_row.id, team_name)
            if not team:
                await _err(
                    interaction, f"Team **{team_name}** not found in this ladder."
                )
                return

            invite: LadderTeamInvite | None = (
                session.query(LadderTeamInvite)
                .filter(
                    LadderTeamInvite.team_id == team.id,
                    LadderTeamInvite.player_id == interaction.user.id,
                )
                .first()
            )
            if not invite:
                await _err(
                    interaction,
                    f"No pending invite to **{team.name}** for you.",
                )
                return

            existing = _find_player_team_in_ladder(
                session, ladder_row.id, interaction.user.id
            )
            if existing:
                await _err(
                    interaction,
                    (
                        f"You are already on team **{existing.name}** in this "
                        f"ladder. Leave it first with `/ladder team leave`."
                    ),
                )
                return

            roster_size = _team_roster_size(session, team.id)
            if roster_size >= ladder_row.max_team_size:
                await _err(
                    interaction,
                    f"Team is full (cap {ladder_row.max_team_size}).",
                )
                return

            _ensure_player(session, interaction.user)
            session.add(
                LadderTeamPlayer(team_id=team.id, player_id=interaction.user.id)
            )
            session.delete(invite)
            session.commit()

            await _ok(
                interaction,
                f"You joined **{escape_markdown(team.name)}**.",
            )

    @team_group.command(name="decline", description="Decline an invite to a team")
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(
        ladder=ladder_autocomplete, team_name=ladder_team_autocomplete
    )
    @app_commands.describe(ladder="Ladder", team_name="Team to decline")
    async def team_decline(self, interaction: Interaction, ladder: str, team_name: str):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            team = _find_team(session, ladder_row.id, team_name)
            if not team:
                await _err(
                    interaction, f"Team **{team_name}** not found in this ladder."
                )
                return
            invite: LadderTeamInvite | None = (
                session.query(LadderTeamInvite)
                .filter(
                    LadderTeamInvite.team_id == team.id,
                    LadderTeamInvite.player_id == interaction.user.id,
                )
                .first()
            )
            if not invite:
                await _err(
                    interaction, f"No pending invite to **{team.name}** for you."
                )
                return
            session.delete(invite)
            session.commit()

            await _ok(
                interaction,
                f"Declined invite to **{escape_markdown(team.name)}**.",
                ephemeral=True,
            )

    @team_group.command(name="leave", description="Leave your team")
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder")
    async def team_leave(self, interaction: Interaction, ladder: str):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            team = _find_player_team_in_ladder(
                session, ladder_row.id, interaction.user.id
            )
            if not team:
                await _err(interaction, "You are not on a team in this ladder.")
                return
            if team.captain_id == interaction.user.id:
                roster_size = _team_roster_size(session, team.id)
                if roster_size > 1:
                    await _err(
                        interaction,
                        (
                            "You are the captain. Transfer captaincy with "
                            "`/ladder team transfer` before leaving, or disband "
                            "the team with `/ladder team disband`."
                        ),
                    )
                    return
                # Solo captain leaving: equivalent to disband (after in-flight
                # check).
                if _team_has_in_flight_match(session, team.id):
                    await _err(
                        interaction,
                        ("Team has an in-flight match. Resolve it before " "leaving."),
                    )
                    return
                team_name = team.name
                session.query(LadderTeamInvite).filter(
                    LadderTeamInvite.team_id == team.id
                ).delete()
                session.query(LadderTeamPlayer).filter(
                    LadderTeamPlayer.team_id == team.id
                ).delete()
                self._compact_positions(session, ladder_row.id, team.position)
                session.delete(team)
                session.commit()
                await _ok(
                    interaction,
                    f"You left and disbanded **{escape_markdown(team_name)}**.",
                )
                return

            session.query(LadderTeamPlayer).filter(
                LadderTeamPlayer.team_id == team.id,
                LadderTeamPlayer.player_id == interaction.user.id,
            ).delete()
            session.commit()

            await _ok(interaction, f"You left **{escape_markdown(team.name)}**.")

    @team_group.command(name="kick", description="Kick a player from your team")
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder", member="Player to kick")
    async def team_kick(self, interaction: Interaction, ladder: str, member: Member):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            team = _find_player_team_in_ladder(
                session, ladder_row.id, interaction.user.id
            )
            if not team or team.captain_id != interaction.user.id:
                await _err(interaction, "Only the team captain can kick players.")
                return
            if member.id == interaction.user.id:
                await _err(
                    interaction,
                    "You can't kick yourself. Use `/ladder team transfer` "
                    "or `/ladder team disband`.",
                )
                return
            roster_row: LadderTeamPlayer | None = (
                session.query(LadderTeamPlayer)
                .filter(
                    LadderTeamPlayer.team_id == team.id,
                    LadderTeamPlayer.player_id == member.id,
                )
                .first()
            )
            if not roster_row:
                await _err(
                    interaction,
                    f"{escape_markdown(member.name)} is not on this team.",
                )
                return
            session.delete(roster_row)
            session.commit()

            await _ok(
                interaction,
                f"{member.mention} kicked from **{escape_markdown(team.name)}**.",
            )

    @team_group.command(
        name="transfer", description="Transfer captaincy to another team member"
    )
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder", member="New captain (must be on the team)")
    async def team_transfer(
        self, interaction: Interaction, ladder: str, member: Member
    ):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            team = _find_player_team_in_ladder(
                session, ladder_row.id, interaction.user.id
            )
            if not team or team.captain_id != interaction.user.id:
                await _err(interaction, "Only the team captain can transfer captaincy.")
                return
            if member.id == interaction.user.id:
                await _err(interaction, "You are already the captain.")
                return
            on_team: LadderTeamPlayer | None = (
                session.query(LadderTeamPlayer)
                .filter(
                    LadderTeamPlayer.team_id == team.id,
                    LadderTeamPlayer.player_id == member.id,
                )
                .first()
            )
            if not on_team:
                await _err(
                    interaction,
                    f"{escape_markdown(member.name)} is not on this team.",
                )
                return
            team.captain_id = member.id
            session.commit()

            await _ok(
                interaction,
                (
                    f"Captaincy of **{escape_markdown(team.name)}** transferred to "
                    f"{member.mention}."
                ),
            )

    @team_group.command(name="disband", description="Disband your team")
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder")
    async def team_disband(self, interaction: Interaction, ladder: str):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            team = _find_player_team_in_ladder(
                session, ladder_row.id, interaction.user.id
            )
            if not team or team.captain_id != interaction.user.id:
                await _err(interaction, "Only the team captain can disband.")
                return
            if _team_has_in_flight_match(session, team.id):
                await _err(
                    interaction,
                    (
                        "Team has an in-flight match. Cancel or play it first "
                        "(or ask an admin to resolve it)."
                    ),
                )
                return
            team_name = team.name
            session.query(LadderTeamInvite).filter(
                LadderTeamInvite.team_id == team.id
            ).delete()
            session.query(LadderTeamPlayer).filter(
                LadderTeamPlayer.team_id == team.id
            ).delete()
            self._compact_positions(session, ladder_row.id, team.position)
            session.delete(team)
            session.commit()

            await _ok(
                interaction,
                f"Team **{escape_markdown(team_name)}** disbanded.",
            )

    @team_group.command(name="info", description="Show a team's roster and record")
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(
        ladder=ladder_autocomplete, team_name=ladder_team_autocomplete
    )
    @app_commands.describe(ladder="Ladder", team_name="Team")
    async def team_info(self, interaction: Interaction, ladder: str, team_name: str):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            team = _find_team(session, ladder_row.id, team_name)
            if not team:
                await _err(
                    interaction, f"Team **{team_name}** not found in this ladder."
                )
                return

            roster: list[tuple[Player, LadderTeamPlayer]] = (
                session.query(Player, LadderTeamPlayer)
                .join(LadderTeamPlayer, LadderTeamPlayer.player_id == Player.id)
                .filter(LadderTeamPlayer.team_id == team.id)
                .order_by(LadderTeamPlayer.joined_at)
                .all()
            )
            captain_mention = f"<@{team.captain_id}>"
            roster_lines = []
            for player, _ in roster:
                marker = " (C)" if player.id == team.captain_id else ""
                roster_lines.append(f"- <@{player.id}>{marker}")
            roster_str = "\n".join(roster_lines) if roster_lines else "*(empty)*"

            embed = Embed(
                title=f"{team.name} — {ladder_row.name}",
                colour=Colour.blue(),
            )
            embed.add_field(name="Position", value=str(team.position), inline=True)
            embed.add_field(
                name="Record",
                value=f"{team.wins}-{team.losses}-{team.draws}",
                inline=True,
            )
            embed.add_field(name="Captain", value=captain_mention, inline=True)
            embed.add_field(
                name=f"Roster ({len(roster)}/{ladder_row.max_team_size})",
                value=roster_str,
                inline=False,
            )
            await interaction.response.send_message(embed=embed)

    @team_group.command(name="list", description="List all teams in a ladder")
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder")
    async def team_list(self, interaction: Interaction, ladder: str):
        session: SQLAlchemySession
        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            teams: list[LadderTeam] = (
                session.query(LadderTeam)
                .filter(LadderTeam.ladder_id == ladder_row.id)
                .order_by(LadderTeam.position)
                .all()
            )
            if not teams:
                await _ok(
                    interaction,
                    f"Ladder **{ladder}** has no teams yet.",
                )
                return
            lines = []
            for team in teams:
                roster_size = _team_roster_size(session, team.id)
                lines.append(
                    f"`{team.position:>2}.` **{escape_markdown(team.name)}** "
                    f"— {team.wins}-{team.losses}-{team.draws} "
                    f"({roster_size}/{ladder_row.max_team_size})"
                )
            embed = Embed(
                title=f"Teams — {ladder_row.name}",
                description="\n".join(lines),
                colour=Colour.blue(),
            )
            await interaction.response.send_message(embed=embed)

    def _compact_positions(
        self,
        session: SQLAlchemySession,
        ladder_id: str,
        removed_position: int,
    ) -> None:
        """Shift teams below `removed_position` up by one to close the gap."""
        below = (
            session.query(LadderTeam)
            .filter(
                LadderTeam.ladder_id == ladder_id,
                LadderTeam.position > removed_position,
            )
            .order_by(LadderTeam.position)
            .all()
        )
        for team in below:
            team.position -= 1
