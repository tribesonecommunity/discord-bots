import logging
import random
from datetime import datetime, timezone

import discord
from discord import (
    Colour,
    Embed,
    Interaction,
    Member,
    TextChannel,
    TextStyle,
    app_commands,
)
from discord.ext.commands import Bot
from discord.ui import Modal, TextInput
from discord.utils import escape_markdown
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.bot import bot as discord_bot
from discord_bots.checks import is_admin_app_command, is_ladder_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.models import (
    Ladder,
    LadderMatch,
    LadderMatchGame,
    LadderTeam,
    LadderTeamInvite,
    LadderTeamPlayer,
    Map,
    Player,
    Rotation,
    RotationMap,
    Session,
)
from discord_bots.utils import (
    ladder_autocomplete,
    ladder_match_autocomplete,
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


def _team_in_flight_match(
    session: SQLAlchemySession, team_id: str
) -> LadderMatch | None:
    return (
        session.query(LadderMatch)
        .filter(
            LadderMatch.status.in_(IN_FLIGHT_STATUSES),
            (LadderMatch.challenger_team_id == team_id)
            | (LadderMatch.defender_team_id == team_id),
        )
        .first()
    )


def _team_outgoing_pending(
    session: SQLAlchemySession, team_id: str
) -> LadderMatch | None:
    return (
        session.query(LadderMatch)
        .filter(
            LadderMatch.status == "pending",
            LadderMatch.challenger_team_id == team_id,
        )
        .first()
    )


def _team_incoming_pending(
    session: SQLAlchemySession, team_id: str
) -> LadderMatch | None:
    return (
        session.query(LadderMatch)
        .filter(
            LadderMatch.status == "pending",
            LadderMatch.defender_team_id == team_id,
        )
        .first()
    )


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _post_history(ladder: Ladder, embed: Embed) -> None:
    """Post an embed to the ladder's history channel, if configured."""
    if not ladder.history_channel_id:
        return
    channel = discord_bot.get_channel(ladder.history_channel_id)
    if isinstance(channel, TextChannel):
        try:
            await channel.send(embed=embed)
        except Exception:
            _log.exception(
                "Failed to post to ladder history channel %s", ladder.history_channel_id
            )


def _build_leaderboard_embed(session: SQLAlchemySession, ladder: Ladder) -> Embed:
    teams: list[LadderTeam] = (
        session.query(LadderTeam)
        .filter(LadderTeam.ladder_id == ladder.id)
        .order_by(LadderTeam.position)
        .all()
    )

    # Pre-fetch team-name lookup for opponent annotations.
    team_by_id = {t.id: t for t in teams}

    in_flight: list[LadderMatch] = (
        session.query(LadderMatch)
        .filter(
            LadderMatch.ladder_id == ladder.id,
            LadderMatch.status.in_(IN_FLIGHT_STATUSES),
        )
        .all()
    )
    annotation_by_team: dict[str, str] = {}
    for match in in_flight:
        challenger = team_by_id.get(match.challenger_team_id)
        defender = team_by_id.get(match.defender_team_id)
        if not challenger or not defender:
            continue
        if match.status == "pending":
            annotation_by_team[challenger.id] = f"[challenging {defender.name}]"
            annotation_by_team[defender.id] = f"[challenged by {challenger.name}]"
        else:
            annotation_by_team[challenger.id] = (
                f"[challenge accepted vs {defender.name}]"
            )
            annotation_by_team[defender.id] = (
                f"[challenge accepted vs {challenger.name}]"
            )

    if not teams:
        body = "*No teams yet.*"
    else:
        lines = []
        for t in teams:
            ann = annotation_by_team.get(t.id, "")
            line = (
                f"`{t.position:>2}.` **{escape_markdown(t.name)}** "
                f"— {t.wins}-{t.losses}-{t.draws}"
            )
            if ann:
                line += f"  {ann}"
            lines.append(line)
        body = "\n".join(lines)

    header = (
        f"maps/match: **{ladder.maps_per_match}**   "
        f"roster: **{ladder.max_team_size}**   "
        f"challenge range: **{ladder.max_challenge_distance}**"
    )
    embed = Embed(
        title=f"Ladder: {ladder.name}",
        description=f"{header}\n\n{body}",
        colour=Colour.gold(),
    )
    return embed


async def _refresh_leaderboard(ladder: Ladder) -> None:
    """Edit (or post) the per-ladder leaderboard message."""
    if not ladder.leaderboard_channel_id:
        return
    channel = discord_bot.get_channel(ladder.leaderboard_channel_id)
    if not isinstance(channel, TextChannel):
        return
    with Session() as session:
        # Re-fetch ladder to ensure fresh state for the embed.
        fresh = session.query(Ladder).filter(Ladder.id == ladder.id).first()
        if not fresh:
            return
        embed = _build_leaderboard_embed(session, fresh)
        try:
            if fresh.leaderboard_message_id:
                try:
                    msg = await channel.fetch_message(fresh.leaderboard_message_id)
                    await msg.edit(embed=embed)
                    return
                except Exception:
                    # Fall through to send a new message.
                    pass
            sent = await channel.send(embed=embed)
            fresh.leaderboard_message_id = sent.id
            session.commit()
        except Exception:
            _log.exception("Failed to refresh ladder leaderboard for %s", ladder.name)


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


def _apply_position_swap(
    session: SQLAlchemySession,
    ladder_id: str,
    challenger: LadderTeam,
    defender: LadderTeam,
) -> tuple[int, int]:
    """
    Move challenger immediately above defender. Defender drops by 1; teams
    between also drop by 1. Returns (old_challenger_pos, old_defender_pos).

    Caller must guarantee defender.position < challenger.position.
    """
    old_defender = defender.position
    old_challenger = challenger.position

    # Park challenger at a safe negative position to avoid the (ladder_id,
    # position) unique constraint while we shift the middle teams down.
    challenger.position = -1
    session.flush()

    middle = (
        session.query(LadderTeam)
        .filter(
            LadderTeam.ladder_id == ladder_id,
            LadderTeam.position >= old_defender,
            LadderTeam.position < old_challenger,
        )
        .order_by(LadderTeam.position.desc())
        .all()
    )
    for t in middle:
        t.position = t.position + 1
        session.flush()

    challenger.position = old_defender
    session.flush()
    return old_challenger, old_defender


def _compute_match_winner_team_id(
    session: SQLAlchemySession, match: LadderMatch
) -> tuple[str | None, int, int]:
    """
    Tally map wins from LadderMatchGame rows. Returns
    (winner_team_id_or_none_for_draw, challenger_map_wins, defender_map_wins).
    """
    games: list[LadderMatchGame] = (
        session.query(LadderMatchGame)
        .filter(LadderMatchGame.match_id == match.id)
        .all()
    )
    challenger_wins = sum(1 for g in games if g.winner_team == 0)
    defender_wins = sum(1 for g in games if g.winner_team == 1)
    if challenger_wins > defender_wins:
        return match.challenger_team_id, challenger_wins, defender_wins
    if defender_wins > challenger_wins:
        return match.defender_team_id, challenger_wins, defender_wins
    return None, challenger_wins, defender_wins


def _undo_records_and_position(
    session: SQLAlchemySession,
    ladder_id: str,
    match: LadderMatch,
) -> None:
    """
    Reverse a previously-applied finalization. Used by admin editmatch when a
    completed match is re-resolved with a different outcome.
    """
    challenger = (
        session.query(LadderTeam)
        .filter(LadderTeam.id == match.challenger_team_id)
        .first()
    )
    defender = (
        session.query(LadderTeam)
        .filter(LadderTeam.id == match.defender_team_id)
        .first()
    )
    if not challenger or not defender:
        return
    if match.winner_team_id == match.challenger_team_id:
        challenger.wins = max(0, challenger.wins - 1)
        defender.losses = max(0, defender.losses - 1)
    elif match.winner_team_id == match.defender_team_id:
        defender.wins = max(0, defender.wins - 1)
        challenger.losses = max(0, challenger.losses - 1)
    else:
        challenger.draws = max(0, challenger.draws - 1)
        defender.draws = max(0, defender.draws - 1)
    session.flush()

    # Reverse position swap if challenger had won and current positions reflect
    # the swap.
    if match.winner_team_id == match.challenger_team_id:
        old_challenger_pos = match.challenger_position_at_challenge
        old_defender_pos = match.defender_position_at_challenge
        if (
            old_defender_pos < old_challenger_pos
            and challenger.position == old_defender_pos
            and defender.position == old_defender_pos + 1
        ):
            # Park challenger, shift middle teams up by 1, restore challenger.
            challenger.position = -1
            session.flush()
            middle = (
                session.query(LadderTeam)
                .filter(
                    LadderTeam.ladder_id == ladder_id,
                    LadderTeam.position > old_defender_pos,
                    LadderTeam.position <= old_challenger_pos,
                )
                .order_by(LadderTeam.position)
                .all()
            )
            for t in middle:
                t.position = t.position - 1
                session.flush()
            challenger.position = old_challenger_pos
            session.flush()


def _finalize_match_outcome(
    session: SQLAlchemySession,
    ladder: Ladder,
    match: LadderMatch,
    forced_winner_team_id: str | None,
    is_draw: bool,
    challenger_map_wins: int,
    defender_map_wins: int,
) -> tuple[LadderTeam, LadderTeam, str, str | None]:
    """
    Apply records + position rule for a finalized match. Caller must have
    already updated LadderMatchGame rows (or be force-ending without per-game
    detail). Returns (challenger, defender, winner_label, winner_team_id).
    """
    challenger = (
        session.query(LadderTeam)
        .filter(LadderTeam.id == match.challenger_team_id)
        .first()
    )
    defender = (
        session.query(LadderTeam)
        .filter(LadderTeam.id == match.defender_team_id)
        .first()
    )
    if not challenger or not defender:
        raise RuntimeError("Match references missing team rows")

    winner_team_id: str | None
    if is_draw:
        winner_team_id = None
    else:
        winner_team_id = forced_winner_team_id

    match.challenger_map_wins = challenger_map_wins
    match.defender_map_wins = defender_map_wins
    match.status = "completed"
    match.completed_at = _utc_now_naive()
    match.winner_team_id = winner_team_id

    if winner_team_id == challenger.id:
        challenger.wins += 1
        defender.losses += 1
        winner_label = challenger.name
    elif winner_team_id == defender.id:
        defender.wins += 1
        challenger.losses += 1
        winner_label = defender.name
    else:
        challenger.draws += 1
        defender.draws += 1
        winner_label = "draw"

    if winner_team_id == challenger.id and defender.position < challenger.position:
        _apply_position_swap(session, ladder.id, challenger, defender)

    session.flush()
    return challenger, defender, winner_label, winner_team_id


class _ReportMatchModal(Modal):
    def __init__(
        self,
        *,
        match_id: str,
        ladder_id: str,
        ladder_name: str,
        challenger_name: str,
        defender_name: str,
        is_admin_edit: bool,
    ):
        title = f"Report: {challenger_name} vs {defender_name}"
        super().__init__(title=title[:45], timeout=None)
        self.match_id = match_id
        self.ladder_id = ladder_id
        self.is_admin_edit = is_admin_edit
        self._inputs: list[TextInput] = []

        with Session() as session:
            games: list[tuple[LadderMatchGame, Map]] = (
                session.query(LadderMatchGame, Map)
                .join(Map, Map.id == LadderMatchGame.map_id)
                .filter(LadderMatchGame.match_id == match_id)
                .order_by(LadderMatchGame.ordinal)
                .all()
            )
            for game, m in games:
                current = ""
                if (
                    game.challenger_score is not None
                    and game.defender_score is not None
                ):
                    current = f"{game.challenger_score}-{game.defender_score}"
                label = f"Map {game.ordinal}: {m.full_name}"
                ti = TextInput(
                    label=label[:45],
                    style=TextStyle.short,
                    placeholder="our-their, e.g. 3-1 (or 3-3 for draw)",
                    default=current,
                    required=True,
                    max_length=20,
                )
                self.add_item(ti)
                self._inputs.append(ti)

    @staticmethod
    def _parse_score(raw: str) -> tuple[int, int] | None:
        s = raw.strip()
        if "-" not in s:
            return None
        a, _, b = s.partition("-")
        try:
            ai = int(a.strip())
            bi = int(b.strip())
        except ValueError:
            return None
        if ai < 0 or bi < 0:
            return None
        return ai, bi

    async def on_submit(self, interaction: Interaction) -> None:
        await interaction.response.defer()

        parsed: list[tuple[int, int]] = []
        for ti in self._inputs:
            r = self._parse_score(ti.value or "")
            if r is None:
                await interaction.followup.send(
                    embed=Embed(
                        description=(
                            f"Invalid score in `{ti.label}`: "
                            f"`{ti.value}`. Use `our-their`, e.g. `3-1`."
                        ),
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            parsed.append(r)

        ladder_snapshot: Ladder | None = None
        history_embed: Embed | None = None

        with Session() as session:
            match: LadderMatch | None = (
                session.query(LadderMatch)
                .filter(LadderMatch.id == self.match_id)
                .first()
            )
            if not match:
                await interaction.followup.send(
                    embed=Embed(
                        description="Match disappeared. Aborting.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            ladder = session.query(Ladder).filter(Ladder.id == match.ladder_id).first()
            if not ladder:
                return

            if self.is_admin_edit and match.status == "completed":
                _undo_records_and_position(session, ladder.id, match)
                match.status = "accepted"
                session.flush()

            games: list[LadderMatchGame] = (
                session.query(LadderMatchGame)
                .filter(LadderMatchGame.match_id == match.id)
                .order_by(LadderMatchGame.ordinal)
                .all()
            )
            if len(games) != len(parsed):
                await interaction.followup.send(
                    embed=Embed(
                        description="Map count mismatch. Re-run the command.",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            now = _utc_now_naive()
            for game, (cs, ds) in zip(games, parsed):
                game.challenger_score = cs
                game.defender_score = ds
                if cs > ds:
                    game.winner_team = 0
                elif ds > cs:
                    game.winner_team = 1
                else:
                    game.winner_team = -1
                game.reported_at = now
                game.reported_by_id = interaction.user.id
            session.flush()

            winner_team_id, c_wins, d_wins = _compute_match_winner_team_id(
                session, match
            )
            challenger, defender, winner_label, _ = _finalize_match_outcome(
                session,
                ladder,
                match,
                forced_winner_team_id=winner_team_id,
                is_draw=winner_team_id is None,
                challenger_map_wins=c_wins,
                defender_map_wins=d_wins,
            )
            session.commit()
            ladder_snapshot = (
                session.query(Ladder).filter(Ladder.id == ladder.id).first()
            )

            score_lines = []
            for g, (cs, ds) in zip(games, parsed):
                tag = ""
                if g.winner_team == 0:
                    tag = " (challenger)"
                elif g.winner_team == 1:
                    tag = " (defender)"
                score_lines.append(f"Map {g.ordinal}: {cs}-{ds}{tag}")

            if winner_label == "draw":
                outcome = "Draw — no position change."
            elif winner_team_id == challenger.id:
                outcome = (
                    f"**{escape_markdown(challenger.name)}** wins and moves "
                    f"to position **{challenger.position}**. "
                    f"**{escape_markdown(defender.name)}** drops to "
                    f"**{defender.position}**."
                )
            else:
                outcome = (
                    f"**{escape_markdown(defender.name)}** holds. "
                    f"No position change."
                )

            history_embed = Embed(
                title="Match completed",
                description=(
                    f"**{escape_markdown(challenger.name)}** vs "
                    f"**{escape_markdown(defender.name)}** — "
                    f"{c_wins}-{d_wins}\n\n" + "\n".join(score_lines) + f"\n\n{outcome}"
                ),
                colour=Colour.green(),
            )
            if self.is_admin_edit:
                history_embed.title = "Match edited (admin)"

            await interaction.followup.send(
                embed=Embed(
                    description=(f"Match recorded. {outcome}"),
                    colour=Colour.green(),
                )
            )

        if history_embed and ladder_snapshot:
            await _post_history(ladder_snapshot, history_embed)
            await _refresh_leaderboard(ladder_snapshot)


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

    @ladder_group.command(name="help", description="Show all ladder commands")
    @app_commands.check(is_ladder_channel)
    async def help_command(self, interaction: Interaction):
        embed = Embed(
            title="Ladder commands",
            description=(
                "All commands run in the ladder channel. Captains issue "
                "challenges and reports; admins manage ladders and resolve "
                "stuck matches."
            ),
            colour=Colour.blue(),
        )
        embed.add_field(
            name="Read",
            value=(
                "`/ladder list` — list all ladders\n"
                "`/ladder rankings <ladder>` — current standings\n"
                "`/ladder team list <ladder>` — all teams in a ladder\n"
                "`/ladder team info <ladder> <team_name>` — roster, record, position\n"
                "`/ladder matchinfo <match_id>` — match details and per-map scores"
            ),
            inline=False,
        )
        embed.add_field(
            name="Team management",
            value=(
                "`/ladder team create <ladder> <name>` — create a team (you become captain)\n"
                "`/ladder team invite <ladder> <member>` — captain only\n"
                "`/ladder team uninvite <ladder> <member>` — revoke a pending invite\n"
                "`/ladder team accept <ladder> <team_name>` — accept an invite\n"
                "`/ladder team decline <ladder> <team_name>` — decline an invite\n"
                "`/ladder team leave <ladder>` — leave your team\n"
                "`/ladder team kick <ladder> <member>` — captain only\n"
                "`/ladder team transfer <ladder> <member>` — give captaincy\n"
                "`/ladder team disband <ladder>` — captain only; blocked while in-flight"
            ),
            inline=False,
        )
        embed.add_field(
            name="Match flow",
            value=(
                "`/ladder challenge <ladder> <opponent>` — challenger captain\n"
                "`/ladder accept <ladder>` — defender captain (rolls maps)\n"
                "`/ladder decline <ladder>` — defender captain\n"
                "`/ladder cancel <ladder>` — challenger captain (only while pending)\n"
                "`/ladder report <ladder>` — opens a modal to enter scores"
            ),
            inline=False,
        )
        embed.add_field(
            name="Admin",
            value=(
                "`/ladder admin create <name> <rotation> <maps_per_match> <max_team_size>`\n"
                "`/ladder admin delete <ladder>`\n"
                "`/ladder admin setchannels <ladder> <leaderboard_channel> <history_channel>`\n"
                "`/ladder admin setmapspermatch <ladder> <value>`\n"
                "`/ladder admin setmaxteamsize <ladder> <value>`\n"
                "`/ladder admin setchallengedistance <ladder> <value>`\n"
                "`/ladder admin setactive <ladder> <value>`\n"
                "`/ladder admin editmatch <match_id>` — re-open report modal\n"
                "`/ladder admin forceendmatch <match_id> <winner>` — force outcome\n"
                "`/ladder admin cancelmatch <match_id>` — void without recording\n"
                "`/ladder admin forceadjust <ladder> <team_name> <new_position>`\n"
                "`/ladder admin removeteam <ladder> <team_name>`"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

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

    # ------------------------------------------------------------------
    # Challenge / accept / decline / cancel
    # ------------------------------------------------------------------

    @ladder_group.command(
        name="challenge", description="Challenge another team to a match"
    )
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(
        ladder=ladder_autocomplete, opponent=ladder_team_autocomplete
    )
    @app_commands.describe(ladder="Ladder", opponent="Team to challenge")
    async def challenge(self, interaction: Interaction, ladder: str, opponent: str):
        await interaction.response.defer()
        ladder_snapshot: Ladder | None = None
        history_embed: Embed | None = None
        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            if not ladder_row.is_active:
                await _err(interaction, f"Ladder **{ladder}** is inactive.")
                return

            challenger = _find_player_team_in_ladder(
                session, ladder_row.id, interaction.user.id
            )
            if not challenger:
                await _err(interaction, "You are not on a team in this ladder.")
                return
            if challenger.captain_id != interaction.user.id:
                await _err(interaction, "Only the team captain can issue challenges.")
                return
            defender = _find_team(session, ladder_row.id, opponent)
            if not defender:
                await _err(
                    interaction, f"Team **{opponent}** not found in this ladder."
                )
                return
            if defender.id == challenger.id:
                await _err(interaction, "You can't challenge your own team.")
                return

            if defender.position >= challenger.position:
                await _err(
                    interaction,
                    "You can only challenge teams ranked above yours.",
                )
                return
            distance = challenger.position - defender.position
            if distance > ladder_row.max_challenge_distance:
                await _err(
                    interaction,
                    (
                        f"Defender is {distance} positions above you. "
                        f"Max challenge distance is "
                        f"{ladder_row.max_challenge_distance}."
                    ),
                )
                return

            for team, label in (
                (challenger, "Your team"),
                (defender, "The defender"),
            ):
                roster_size = _team_roster_size(session, team.id)
                if roster_size < ladder_row.max_team_size:
                    await _err(
                        interaction,
                        (
                            f"{label} ({team.name}) has {roster_size}/"
                            f"{ladder_row.max_team_size} players. Both rosters "
                            f"must be full to challenge."
                        ),
                    )
                    return
                in_flight = _team_in_flight_match(session, team.id)
                if in_flight:
                    await _err(
                        interaction,
                        (f"{label} ({team.name}) already has an in-flight " f"match."),
                    )
                    return

            match = LadderMatch(
                ladder_id=ladder_row.id,
                challenger_team_id=challenger.id,
                defender_team_id=defender.id,
                challenger_position_at_challenge=challenger.position,
                defender_position_at_challenge=defender.position,
            )
            session.add(match)
            session.commit()

            ladder_snapshot = (
                session.query(Ladder).filter(Ladder.id == ladder_row.id).first()
            )

            history_embed = Embed(
                title="Challenge issued",
                description=(
                    f"**{escape_markdown(challenger.name)}** "
                    f"(#{challenger.position}) challenges "
                    f"**{escape_markdown(defender.name)}** "
                    f"(#{defender.position}) on ladder "
                    f"**{ladder_row.name}**."
                ),
                colour=Colour.orange(),
            )
            await _ok(
                interaction,
                (
                    f"Challenge sent to **{escape_markdown(defender.name)}**. "
                    f"Their captain can `/ladder team accept` or `decline`."
                ),
            )

        if history_embed and ladder_snapshot:
            await _post_history(ladder_snapshot, history_embed)
            await _refresh_leaderboard(ladder_snapshot)

    @ladder_group.command(
        name="accept",
        description="Accept the pending challenge against your team",
    )
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder")
    async def accept(self, interaction: Interaction, ladder: str):
        await interaction.response.defer()
        ladder_snapshot: Ladder | None = None
        history_embed: Embed | None = None
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
            if not team or team.captain_id != interaction.user.id:
                await _err(
                    interaction,
                    "Only the team captain can accept challenges.",
                )
                return
            match = _team_incoming_pending(session, team.id)
            if not match:
                await _err(
                    interaction,
                    "Your team has no pending challenge to accept.",
                )
                return

            challenger = (
                session.query(LadderTeam)
                .filter(LadderTeam.id == match.challenger_team_id)
                .first()
            )
            if not challenger:
                await _err(interaction, "Challenger team is missing.")
                return

            for t in (team, challenger):
                if _team_roster_size(session, t.id) < ladder_row.max_team_size:
                    await _err(
                        interaction,
                        (
                            f"Team **{t.name}** is below the roster cap. "
                            f"Match cannot start until both teams are full."
                        ),
                    )
                    return

            rotation_pairs: list[tuple[RotationMap, Map]] = (
                session.query(RotationMap, Map)
                .join(Map, Map.id == RotationMap.map_id)
                .filter(RotationMap.rotation_id == ladder_row.rotation_id)
                .all()
            )
            map_options: list[Map] = [m for _, m in rotation_pairs]
            if not map_options:
                await _err(
                    interaction,
                    (
                        "The ladder's rotation has no maps. Add maps to the "
                        "rotation before accepting challenges."
                    ),
                )
                return

            duplicates_used = False
            if len(map_options) >= ladder_row.maps_per_match:
                chosen_maps = random.sample(map_options, k=ladder_row.maps_per_match)
            else:
                chosen_maps = random.choices(map_options, k=ladder_row.maps_per_match)
                duplicates_used = True

            for ordinal, m in enumerate(chosen_maps, start=1):
                session.add(
                    LadderMatchGame(
                        match_id=match.id,
                        ordinal=ordinal,
                        map_id=m.id,
                    )
                )

            match.status = "accepted"
            match.accepted_at = _utc_now_naive()
            session.commit()

            ladder_snapshot = (
                session.query(Ladder).filter(Ladder.id == ladder_row.id).first()
            )

            map_list = "\n".join(
                f"{i}. {escape_markdown(m.full_name)}"
                for i, m in enumerate(chosen_maps, start=1)
            )
            history_embed = Embed(
                title="Match accepted",
                description=(
                    f"**{escape_markdown(challenger.name)}** vs "
                    f"**{escape_markdown(team.name)}** on ladder "
                    f"**{ladder_row.name}**.\n\nMaps:\n{map_list}"
                ),
                colour=Colour.green(),
            )
            if duplicates_used:
                history_embed.add_field(
                    name="Note",
                    value=(
                        "Rotation has fewer maps than `maps_per_match`; "
                        "duplicates were allowed."
                    ),
                    inline=False,
                )
            await _ok(
                interaction,
                (
                    f"Challenge accepted. Maps:\n{map_list}\n\nWhen all maps "
                    f"are played, either captain can run `/ladder report`."
                ),
            )

        if history_embed and ladder_snapshot:
            await _post_history(ladder_snapshot, history_embed)
            await _refresh_leaderboard(ladder_snapshot)

    @ladder_group.command(
        name="decline",
        description="Decline the pending challenge against your team",
    )
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder")
    async def decline(self, interaction: Interaction, ladder: str):
        await interaction.response.defer()
        ladder_snapshot: Ladder | None = None
        history_embed: Embed | None = None
        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            team = _find_player_team_in_ladder(
                session, ladder_row.id, interaction.user.id
            )
            if not team or team.captain_id != interaction.user.id:
                await _err(
                    interaction,
                    "Only the team captain can decline challenges.",
                )
                return
            match = _team_incoming_pending(session, team.id)
            if not match:
                await _err(interaction, "Your team has no pending challenge.")
                return
            challenger = (
                session.query(LadderTeam)
                .filter(LadderTeam.id == match.challenger_team_id)
                .first()
            )
            match.status = "cancelled"
            match.completed_at = _utc_now_naive()
            session.commit()

            ladder_snapshot = (
                session.query(Ladder).filter(Ladder.id == ladder_row.id).first()
            )

            history_embed = Embed(
                title="Challenge declined",
                description=(
                    f"**{escape_markdown(team.name)}** declined a challenge "
                    f"from **{escape_markdown(challenger.name) if challenger else '(missing)'}** "
                    f"on ladder **{ladder_row.name}**."
                ),
                colour=Colour.dark_grey(),
            )
            await _ok(interaction, "Challenge declined.")

        if history_embed and ladder_snapshot:
            await _post_history(ladder_snapshot, history_embed)
            await _refresh_leaderboard(ladder_snapshot)

    @ladder_group.command(
        name="cancel",
        description="Cancel your team's outgoing pending challenge",
    )
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder")
    async def cancel(self, interaction: Interaction, ladder: str):
        await interaction.response.defer()
        ladder_snapshot: Ladder | None = None
        history_embed: Embed | None = None
        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            team = _find_player_team_in_ladder(
                session, ladder_row.id, interaction.user.id
            )
            if not team or team.captain_id != interaction.user.id:
                await _err(
                    interaction,
                    "Only the team captain can cancel challenges.",
                )
                return
            match = _team_outgoing_pending(session, team.id)
            if not match:
                await _err(
                    interaction,
                    "Your team has no outgoing pending challenge.",
                )
                return
            defender = (
                session.query(LadderTeam)
                .filter(LadderTeam.id == match.defender_team_id)
                .first()
            )
            match.status = "cancelled"
            match.completed_at = _utc_now_naive()
            session.commit()

            ladder_snapshot = (
                session.query(Ladder).filter(Ladder.id == ladder_row.id).first()
            )

            history_embed = Embed(
                title="Challenge cancelled",
                description=(
                    f"**{escape_markdown(team.name)}** cancelled their "
                    f"challenge to **{escape_markdown(defender.name) if defender else '(missing)'}** "
                    f"on ladder **{ladder_row.name}**."
                ),
                colour=Colour.dark_grey(),
            )
            await _ok(interaction, "Challenge cancelled.")

        if history_embed and ladder_snapshot:
            await _post_history(ladder_snapshot, history_embed)
            await _refresh_leaderboard(ladder_snapshot)

    # ------------------------------------------------------------------
    # Reporting + match info
    # ------------------------------------------------------------------

    @ladder_group.command(
        name="report",
        description="Report scores for your accepted match",
    )
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder")
    async def report(self, interaction: Interaction, ladder: str):
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
            if not team or team.captain_id != interaction.user.id:
                await _err(
                    interaction,
                    "Only the team captain can report scores.",
                )
                return
            match: LadderMatch | None = (
                session.query(LadderMatch)
                .filter(
                    LadderMatch.status == "accepted",
                    (LadderMatch.challenger_team_id == team.id)
                    | (LadderMatch.defender_team_id == team.id),
                )
                .first()
            )
            if not match:
                await _err(
                    interaction,
                    "Your team has no accepted match to report.",
                )
                return
            challenger = (
                session.query(LadderTeam)
                .filter(LadderTeam.id == match.challenger_team_id)
                .first()
            )
            defender = (
                session.query(LadderTeam)
                .filter(LadderTeam.id == match.defender_team_id)
                .first()
            )
            if not challenger or not defender:
                await _err(interaction, "Match references missing teams.")
                return

            modal = _ReportMatchModal(
                match_id=match.id,
                ladder_id=ladder_row.id,
                ladder_name=ladder_row.name,
                challenger_name=challenger.name,
                defender_name=defender.name,
                is_admin_edit=False,
            )

        await interaction.response.send_modal(modal)

    @ladder_group.command(
        name="matchinfo",
        description="Show details for a match",
    )
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(match_id=ladder_match_autocomplete)
    @app_commands.describe(match_id="Match")
    async def match_info(self, interaction: Interaction, match_id: str):
        with Session() as session:
            match = (
                session.query(LadderMatch).filter(LadderMatch.id == match_id).first()
            )
            if not match:
                await _err(interaction, "Match not found.")
                return
            ladder = session.query(Ladder).filter(Ladder.id == match.ladder_id).first()
            challenger = (
                session.query(LadderTeam)
                .filter(LadderTeam.id == match.challenger_team_id)
                .first()
            )
            defender = (
                session.query(LadderTeam)
                .filter(LadderTeam.id == match.defender_team_id)
                .first()
            )
            games: list[tuple[LadderMatchGame, Map]] = (
                session.query(LadderMatchGame, Map)
                .join(Map, Map.id == LadderMatchGame.map_id)
                .filter(LadderMatchGame.match_id == match.id)
                .order_by(LadderMatchGame.ordinal)
                .all()
            )

            game_lines = []
            for g, m in games:
                if g.challenger_score is None or g.defender_score is None:
                    game_lines.append(
                        f"Map {g.ordinal} ({escape_markdown(m.full_name)}): "
                        f"*not reported*"
                    )
                else:
                    tag = ""
                    if g.winner_team == 0:
                        tag = " (challenger)"
                    elif g.winner_team == 1:
                        tag = " (defender)"
                    elif g.winner_team == -1:
                        tag = " (draw)"
                    game_lines.append(
                        f"Map {g.ordinal} ({escape_markdown(m.full_name)}): "
                        f"{g.challenger_score}-{g.defender_score}{tag}"
                    )

            embed = Embed(
                title=(
                    f"{ladder.name if ladder else '?'}: "
                    f"{challenger.name if challenger else '?'} vs "
                    f"{defender.name if defender else '?'}"
                ),
                colour=Colour.blue(),
            )
            embed.add_field(name="Status", value=match.status, inline=True)
            embed.add_field(
                name="Score",
                value=(f"{match.challenger_map_wins}-{match.defender_map_wins}"),
                inline=True,
            )
            if match.winner_team_id:
                winner = (
                    session.query(LadderTeam)
                    .filter(LadderTeam.id == match.winner_team_id)
                    .first()
                )
                if winner:
                    embed.add_field(name="Winner", value=winner.name, inline=True)
            embed.add_field(
                name="Games",
                value="\n".join(game_lines) if game_lines else "*(none)*",
                inline=False,
            )
            embed.add_field(name="Match ID", value=f"`{match.id}`", inline=False)
            await interaction.response.send_message(embed=embed)

    @ladder_group.command(
        name="rankings", description="Show the current ladder rankings"
    )
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(ladder=ladder_autocomplete)
    @app_commands.describe(ladder="Ladder")
    async def rankings(self, interaction: Interaction, ladder: str):
        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            embed = _build_leaderboard_embed(session, ladder_row)
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # Admin: editmatch / forceendmatch / cancelmatch / forceadjust /
    # removeteam
    # ------------------------------------------------------------------

    @admin_group.command(
        name="editmatch",
        description="Open the report modal for a match (admin)",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(match_id=ladder_match_autocomplete)
    @app_commands.describe(match_id="Match to edit")
    async def admin_editmatch(self, interaction: Interaction, match_id: str):
        with Session() as session:
            match = (
                session.query(LadderMatch).filter(LadderMatch.id == match_id).first()
            )
            if not match:
                await _err(interaction, "Match not found.")
                return
            if match.status not in ("accepted", "completed"):
                await _err(
                    interaction,
                    f"Match status is `{match.status}`; nothing to edit.",
                )
                return
            ladder = session.query(Ladder).filter(Ladder.id == match.ladder_id).first()
            challenger = (
                session.query(LadderTeam)
                .filter(LadderTeam.id == match.challenger_team_id)
                .first()
            )
            defender = (
                session.query(LadderTeam)
                .filter(LadderTeam.id == match.defender_team_id)
                .first()
            )
            if not ladder or not challenger or not defender:
                await _err(interaction, "Match references missing rows.")
                return

            modal = _ReportMatchModal(
                match_id=match.id,
                ladder_id=ladder.id,
                ladder_name=ladder.name,
                challenger_name=challenger.name,
                defender_name=defender.name,
                is_admin_edit=True,
            )

        await interaction.response.send_modal(modal)

    @admin_group.command(
        name="forceendmatch",
        description="Force-end an accepted match in a team's favor",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(match_id=ladder_match_autocomplete)
    @app_commands.choices(
        winner=[
            app_commands.Choice(name="challenger", value="challenger"),
            app_commands.Choice(name="defender", value="defender"),
            app_commands.Choice(name="draw", value="draw"),
        ]
    )
    @app_commands.describe(match_id="Match", winner="Outcome")
    async def admin_forceendmatch(
        self,
        interaction: Interaction,
        match_id: str,
        winner: str,
    ):
        await interaction.response.defer()
        ladder_snapshot: Ladder | None = None
        history_embed: Embed | None = None

        with Session() as session:
            match = (
                session.query(LadderMatch).filter(LadderMatch.id == match_id).first()
            )
            if not match:
                await _err(interaction, "Match not found.")
                return
            if match.status not in ("pending", "accepted"):
                await _err(
                    interaction,
                    f"Match is `{match.status}`; force-end only applies to "
                    f"in-flight matches.",
                )
                return
            ladder = session.query(Ladder).filter(Ladder.id == match.ladder_id).first()
            if not ladder:
                return

            if winner == "challenger":
                forced = match.challenger_team_id
                is_draw = False
                c_wins = ladder.maps_per_match
                d_wins = 0
            elif winner == "defender":
                forced = match.defender_team_id
                is_draw = False
                c_wins = 0
                d_wins = ladder.maps_per_match
            else:
                forced = None
                is_draw = True
                c_wins = 0
                d_wins = 0

            challenger, defender, winner_label, _ = _finalize_match_outcome(
                session,
                ladder,
                match,
                forced_winner_team_id=forced,
                is_draw=is_draw,
                challenger_map_wins=c_wins,
                defender_map_wins=d_wins,
            )
            session.commit()
            ladder_snapshot = (
                session.query(Ladder).filter(Ladder.id == ladder.id).first()
            )

            if winner_label == "draw":
                outcome = "Draw — no position change."
            elif winner == "challenger":
                outcome = (
                    f"**{escape_markdown(challenger.name)}** moves to "
                    f"position **{challenger.position}**; "
                    f"**{escape_markdown(defender.name)}** drops to "
                    f"**{defender.position}**."
                )
            else:
                outcome = (
                    f"**{escape_markdown(defender.name)}** holds. "
                    f"No position change."
                )

            history_embed = Embed(
                title="Match force-ended (admin)",
                description=(
                    f"**{escape_markdown(challenger.name)}** vs "
                    f"**{escape_markdown(defender.name)}** — "
                    f"resolved by <@{interaction.user.id}> as "
                    f"**{winner}**.\n\n{outcome}"
                ),
                colour=Colour.dark_green(),
            )
            await _ok(
                interaction,
                f"Match force-ended as **{winner}**. {outcome}",
            )

        if history_embed and ladder_snapshot:
            await _post_history(ladder_snapshot, history_embed)
            await _refresh_leaderboard(ladder_snapshot)

    @admin_group.command(
        name="cancelmatch",
        description="Void a match without recording a result",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(match_id=ladder_match_autocomplete)
    @app_commands.describe(match_id="Match to cancel")
    async def admin_cancelmatch(self, interaction: Interaction, match_id: str):
        await interaction.response.defer()
        ladder_snapshot: Ladder | None = None
        history_embed: Embed | None = None

        with Session() as session:
            match = (
                session.query(LadderMatch).filter(LadderMatch.id == match_id).first()
            )
            if not match:
                await _err(interaction, "Match not found.")
                return
            ladder = session.query(Ladder).filter(Ladder.id == match.ladder_id).first()
            if not ladder:
                return

            if match.status == "completed":
                _undo_records_and_position(session, ladder.id, match)

            match.status = "cancelled"
            match.completed_at = _utc_now_naive()
            match.winner_team_id = None
            match.challenger_map_wins = 0
            match.defender_map_wins = 0
            session.flush()

            challenger = (
                session.query(LadderTeam)
                .filter(LadderTeam.id == match.challenger_team_id)
                .first()
            )
            defender = (
                session.query(LadderTeam)
                .filter(LadderTeam.id == match.defender_team_id)
                .first()
            )
            session.commit()
            ladder_snapshot = (
                session.query(Ladder).filter(Ladder.id == ladder.id).first()
            )

            history_embed = Embed(
                title="Match cancelled (admin)",
                description=(
                    f"<@{interaction.user.id}> cancelled the match between "
                    f"**{escape_markdown(challenger.name) if challenger else '?'}** and "
                    f"**{escape_markdown(defender.name) if defender else '?'}** "
                    f"on ladder **{ladder.name}**."
                ),
                colour=Colour.dark_grey(),
            )
            await _ok(interaction, "Match cancelled.")

        if history_embed and ladder_snapshot:
            await _post_history(ladder_snapshot, history_embed)
            await _refresh_leaderboard(ladder_snapshot)

    @admin_group.command(
        name="forceadjust",
        description="Manually move a team to a new position",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(
        ladder=ladder_autocomplete, team_name=ladder_team_autocomplete
    )
    @app_commands.describe(
        ladder="Ladder",
        team_name="Team to move",
        new_position="Target position (1 = top)",
    )
    async def admin_forceadjust(
        self,
        interaction: Interaction,
        ladder: str,
        team_name: str,
        new_position: int,
    ):
        await interaction.response.defer()
        ladder_snapshot: Ladder | None = None
        history_embed: Embed | None = None

        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            team = _find_team(session, ladder_row.id, team_name)
            if not team:
                await _err(
                    interaction,
                    f"Team **{team_name}** not found in this ladder.",
                )
                return

            total_teams = (
                session.query(func.count(LadderTeam.id))
                .filter(LadderTeam.ladder_id == ladder_row.id)
                .scalar()
                or 0
            )
            if new_position < 1 or new_position > total_teams:
                await _err(
                    interaction,
                    f"Position must be between 1 and {total_teams}.",
                )
                return
            old_position = team.position
            if old_position == new_position:
                await _ok(interaction, "Team is already at that position.")
                return

            # Park team at -1 and shift others to fill / make room.
            team.position = -1
            session.flush()

            if new_position < old_position:
                # Moving up: teams in [new_position, old_position) shift down by 1.
                middle = (
                    session.query(LadderTeam)
                    .filter(
                        LadderTeam.ladder_id == ladder_row.id,
                        LadderTeam.position >= new_position,
                        LadderTeam.position < old_position,
                    )
                    .order_by(LadderTeam.position.desc())
                    .all()
                )
                for t in middle:
                    t.position = t.position + 1
                    session.flush()
            else:
                # Moving down: teams in (old_position, new_position] shift up by 1.
                middle = (
                    session.query(LadderTeam)
                    .filter(
                        LadderTeam.ladder_id == ladder_row.id,
                        LadderTeam.position > old_position,
                        LadderTeam.position <= new_position,
                    )
                    .order_by(LadderTeam.position)
                    .all()
                )
                for t in middle:
                    t.position = t.position - 1
                    session.flush()

            team.position = new_position
            session.commit()
            ladder_snapshot = (
                session.query(Ladder).filter(Ladder.id == ladder_row.id).first()
            )

            history_embed = Embed(
                title="Position adjusted (admin)",
                description=(
                    f"<@{interaction.user.id}> moved "
                    f"**{escape_markdown(team.name)}** from position "
                    f"**{old_position}** to **{new_position}** on ladder "
                    f"**{ladder_row.name}**."
                ),
                colour=Colour.dark_blue(),
            )
            await _ok(
                interaction,
                f"Moved **{escape_markdown(team.name)}** to position "
                f"**{new_position}**.",
            )

        if history_embed and ladder_snapshot:
            await _post_history(ladder_snapshot, history_embed)
            await _refresh_leaderboard(ladder_snapshot)

    @admin_group.command(name="removeteam", description="Force-disband a team (admin)")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_ladder_channel)
    @app_commands.autocomplete(
        ladder=ladder_autocomplete, team_name=ladder_team_autocomplete
    )
    @app_commands.describe(ladder="Ladder", team_name="Team to remove")
    async def admin_removeteam(
        self, interaction: Interaction, ladder: str, team_name: str
    ):
        await interaction.response.defer()
        ladder_snapshot: Ladder | None = None
        history_embed: Embed | None = None

        with Session() as session:
            ladder_row = _find_ladder(session, ladder)
            if not ladder_row:
                await _err(interaction, f"Ladder **{ladder}** not found.")
                return
            team = _find_team(session, ladder_row.id, team_name)
            if not team:
                await _err(
                    interaction,
                    f"Team **{team_name}** not found in this ladder.",
                )
                return

            # Cancel any in-flight matches referencing this team.
            in_flight: list[LadderMatch] = (
                session.query(LadderMatch)
                .filter(
                    LadderMatch.status.in_(IN_FLIGHT_STATUSES),
                    (LadderMatch.challenger_team_id == team.id)
                    | (LadderMatch.defender_team_id == team.id),
                )
                .all()
            )
            for m in in_flight:
                m.status = "cancelled"
                m.completed_at = _utc_now_naive()
            session.flush()

            removed_position = team.position
            session.query(LadderTeamInvite).filter(
                LadderTeamInvite.team_id == team.id
            ).delete()
            session.query(LadderTeamPlayer).filter(
                LadderTeamPlayer.team_id == team.id
            ).delete()
            self._compact_positions(session, ladder_row.id, removed_position)
            session.delete(team)
            session.commit()
            ladder_snapshot = (
                session.query(Ladder).filter(Ladder.id == ladder_row.id).first()
            )

            history_embed = Embed(
                title="Team removed (admin)",
                description=(
                    f"<@{interaction.user.id}> removed team "
                    f"**{escape_markdown(team_name)}** from ladder "
                    f"**{ladder_row.name}**."
                ),
                colour=Colour.dark_red(),
            )
            await _ok(
                interaction,
                f"Team **{escape_markdown(team_name)}** removed.",
            )

        if history_embed and ladder_snapshot:
            await _post_history(ladder_snapshot, history_embed)
            await _refresh_leaderboard(ladder_snapshot)
