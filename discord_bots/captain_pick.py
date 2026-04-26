"""
Captain pick draft flow. Imported by commands.create_game when the popped
queue has is_captain_pick=True. Manages the lifecycle of a drafting
InProgressGame: captain selection, board rendering, and finalization.

The state machine itself (button/select interaction handling and message
edits during the draft) lives in cogs.draft. Helpers here are stateless.
"""

from __future__ import annotations

import asyncio
import logging
from random import uniform
from typing import TYPE_CHECKING

import discord
import sqlalchemy
from discord import CategoryChannel, Colour, Embed, Guild, TextChannel
from discord.ext.commands import Bot
from sqlalchemy.orm.session import Session as SQLAlchemySession
from trueskill import Rating

import discord_bots.config as config
from discord_bots.bot import bot
from discord_bots.cogs.in_progress_game import (
    InProgressGameCommands,
    InProgressGameView,
)
from discord_bots.models import (
    Config,
    DraftPick,
    InProgressGame,
    InProgressGameChannel,
    InProgressGamePlayer,
    Map,
    Player,
    PlayerCategoryTrueskill,
    Queue,
    QueuePlayer,
    Rotation,
    RotationMap,
    Session,
)
from discord_bots.names import generate_be_name, generate_ds_name
from discord_bots.utils import (
    create_in_progress_game_embed,
    execute_map_rotation,
    get_category_trueskill,
    get_team_voice_channels,
    mean,
    move_game_players,
    send_in_guild_message,
    send_message,
    short_uuid,
    win_probability_matchmaking,
)

if TYPE_CHECKING:
    from discord_bots.cogs.draft import DraftCommands

_log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Snake-draft turn calculator
# ─────────────────────────────────────────────────────────────────────────────


def picker_for_pick(pick_number: int, first_picker_team: int) -> int:
    """
    Snake-draft team for the given 1-indexed pick_number.

    Pattern when first_picker_team=0:
        pick 1 -> 0
        picks 2,3 -> 1
        picks 4,5 -> 0
        picks 6,7 -> 1
        ...
    """
    if pick_number == 1:
        return first_picker_team
    pair_index = (pick_number - 2) // 2
    return (1 - first_picker_team) if pair_index % 2 == 0 else first_picker_team


# ─────────────────────────────────────────────────────────────────────────────
# Player rating helpers
# ─────────────────────────────────────────────────────────────────────────────


def _player_rating(
    session: SQLAlchemySession,
    db_config: Config,
    player: Player,
    queue: Queue,
    map_id: str,
) -> tuple[float, float]:
    """Return (mu, sigma) for a player, respecting category/map trueskill."""
    if queue.category_id:
        pct = get_category_trueskill(
            session,
            db_config,
            player.id,
            queue.map_trueskill_enabled,
            queue.category_id,
            map_id,
            None,
        )
        return pct.mu, pct.sigma
    return player.rated_trueskill_mu, player.rated_trueskill_sigma


def _player_rank(
    session: SQLAlchemySession,
    db_config: Config,
    player: Player,
    queue: Queue,
    map_id: str,
) -> float:
    mu, sigma = _player_rating(session, db_config, player, queue, map_id)
    return mu - 3 * sigma


def select_captains(
    session: SQLAlchemySession,
    players: list[Player],
    queue: Queue,
    map_id: str,
) -> tuple[Player, Player]:
    """
    Pick the two highest-rated players as captains.

    Tiebreaker: rank (mu - 3*sigma) desc, then mu desc, then player_id asc.
    Returns (captain_a, captain_b) where captain_a is the higher-rated and
    captain_b is the lower-rated.
    """
    db_config: Config = session.query(Config).first()
    sorted_players = sorted(
        players,
        key=lambda p: (
            -_player_rank(session, db_config, p, queue, map_id),
            -_player_rating(session, db_config, p, queue, map_id)[0],
            p.id,
        ),
    )
    return sorted_players[0], sorted_players[1]


# ─────────────────────────────────────────────────────────────────────────────
# Draft state queries
# ─────────────────────────────────────────────────────────────────────────────


def get_first_picker_team(session: SQLAlchemySession, game_id: str) -> int | None:
    """
    Derive the team that picks first from the first DraftPick row.

    Returns None if no picks have been made yet (i.e. the first-pick choice
    hasn't been recorded). When 0 picks exist after a bot restart, the
    cog re-prompts captain B for the choice.
    """
    first_pick = (
        session.query(DraftPick)
        .filter(DraftPick.in_progress_game_id == game_id)
        .order_by(DraftPick.pick_number.asc())
        .first()
    )
    if not first_pick:
        return None
    igp = (
        session.query(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == game_id,
            InProgressGamePlayer.player_id == first_pick.captain_player_id,
        )
        .first()
    )
    return igp.team if igp else None


def get_current_picker(
    session: SQLAlchemySession, game_id: str, first_picker_team: int
) -> InProgressGamePlayer | None:
    """Return the InProgressGamePlayer (captain) whose turn is next, or None
    if the draft is complete."""
    pick_count = (
        session.query(DraftPick)
        .filter(DraftPick.in_progress_game_id == game_id)
        .count()
    )
    total_players = (
        session.query(InProgressGamePlayer)
        .filter(InProgressGamePlayer.in_progress_game_id == game_id)
        .count()
    )
    total_picks = total_players - 2  # subtract the two captains
    next_pick_number = pick_count + 1
    if next_pick_number > total_picks:
        return None
    next_team = picker_for_pick(next_pick_number, first_picker_team)
    return (
        session.query(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == game_id,
            InProgressGamePlayer.is_captain == True,
            InProgressGamePlayer.team == next_team,
        )
        .first()
    )


# ─────────────────────────────────────────────────────────────────────────────
# Embed rendering
# ─────────────────────────────────────────────────────────────────────────────


def _format_player_line(player: Player, is_captain: bool) -> str:
    prefix = "👑 " if is_captain else "• "
    return f"{prefix}<@{player.id}>"


def create_draft_embed(
    session: SQLAlchemySession,
    game: InProgressGame,
) -> Embed:
    """Render the draft state as an embed."""
    igps = (
        session.query(InProgressGamePlayer)
        .filter(InProgressGamePlayer.in_progress_game_id == game.id)
        .all()
    )
    player_ids = [igp.player_id for igp in igps]
    players_by_id = {
        p.id: p for p in session.query(Player).filter(Player.id.in_(player_ids)).all()
    }

    team0_lines: list[str] = []
    team1_lines: list[str] = []
    pool_lines: list[str] = []
    for igp in igps:
        player = players_by_id[igp.player_id]
        if igp.team == 0:
            team0_lines.append(_format_player_line(player, igp.is_captain))
        elif igp.team == 1:
            team1_lines.append(_format_player_line(player, igp.is_captain))
        else:
            pool_lines.append(_format_player_line(player, False))

    first_picker_team = get_first_picker_team(session, game.id)
    pick_count = (
        session.query(DraftPick)
        .filter(DraftPick.in_progress_game_id == game.id)
        .count()
    )
    total_picks = len(igps) - 2

    if first_picker_team is None and pool_lines:
        captain_b = next(
            (igp for igp in igps if igp.is_captain and igp.team == 1), None
        )
        state_desc = (
            f"Awaiting <@{captain_b.player_id}>'s first/second pick choice."
            if captain_b
            else "Awaiting first/second pick choice."
        )
    elif pool_lines:
        next_picker = get_current_picker(session, game.id, first_picker_team)
        if next_picker:
            state_desc = (
                f"<@{next_picker.player_id}>'s turn "
                f"(pick {pick_count + 1}/{total_picks})"
            )
        else:
            state_desc = "Draft complete."
    else:
        state_desc = "Draft complete."

    embed = Embed(
        title=f"📋 Captain pick draft — {game.map_full_name}",
        colour=Colour.blurple(),
    )
    embed.add_field(
        name=f"🔴 {game.team0_name}",
        value="\n".join(team0_lines) if team0_lines else "_(empty)_",
        inline=True,
    )
    embed.add_field(
        name=f"🔵 {game.team1_name}",
        value="\n".join(team1_lines) if team1_lines else "_(empty)_",
        inline=True,
    )
    embed.add_field(
        name="Remaining",
        value="\n".join(pool_lines) if pool_lines else "_(none)_",
        inline=False,
    )
    embed.add_field(name="Status", value=state_desc, inline=False)
    return embed


# ─────────────────────────────────────────────────────────────────────────────
# Draft start
# ─────────────────────────────────────────────────────────────────────────────


async def start_draft(
    session: SQLAlchemySession,
    queue: Queue,
    next_map: Map,
    rolled_random_map: bool,
    player_ids: list[int],
    lobby_channel: TextChannel,
    guild: Guild,
) -> None:
    """
    Begin a captain-pick draft.

    Creates an InProgressGame with is_drafting=True, writes captain rows
    (is_captain=True, team 0/1) and pool rows (team=NULL), creates the
    match text channel, advances the map rotation (matching the existing
    create_game behavior), and posts the FirstPickChoiceView.
    """
    # Lazy import to avoid circular: views/draft -> cogs/draft -> captain_pick
    from discord_bots.views.draft import FirstPickChoiceView

    db_config: Config = session.query(Config).first()
    players: list[Player] = (
        session.query(Player).filter(Player.id.in_(player_ids)).all()
    )

    captain_a, captain_b = select_captains(session, players, queue, next_map.id)
    non_captains = [p for p in players if p.id not in (captain_a.id, captain_b.id)]

    # Use mu (or category mu) average for InProgressGame.average_trueskill so
    # downstream code that reads it still works.
    all_mus = [
        _player_rating(session, db_config, p, queue, next_map.id)[0] for p in players
    ]
    average_trueskill = mean(all_mus)

    game = InProgressGame(
        average_trueskill=average_trueskill,
        map_id=next_map.id,
        map_full_name=next_map.full_name,
        map_short_name=next_map.short_name,
        queue_id=queue.id,
        team0_name=generate_be_name(),
        team1_name=generate_ds_name(),
        win_probability=0.0,  # populated in finalize_draft
        is_drafting=True,
    )
    session.add(game)

    # Captains: team 0 = higher-rated (captain A), team 1 = lower-rated (captain B)
    session.add(
        InProgressGamePlayer(
            in_progress_game_id=game.id,
            player_id=captain_a.id,
            team=0,
            is_captain=True,
        )
    )
    session.add(
        InProgressGamePlayer(
            in_progress_game_id=game.id,
            player_id=captain_b.id,
            team=1,
            is_captain=True,
        )
    )
    for player in non_captains:
        session.add(
            InProgressGamePlayer(
                in_progress_game_id=game.id,
                player_id=player.id,
                team=None,
                is_captain=False,
            )
        )

    # Create the match text channel under the tribes voice category.
    short_game_id = short_uuid(game.id)
    match_channel: TextChannel | None = None
    category_channel: discord.abc.GuildChannel | None = guild.get_channel(
        config.TRIBES_VOICE_CATEGORY_CHANNEL_ID
    )
    if isinstance(category_channel, CategoryChannel):
        match_channel = await guild.create_text_channel(
            f"{queue.name}-({short_game_id})", category=category_channel
        )
        session.add(
            InProgressGameChannel(
                in_progress_game_id=game.id, channel_id=match_channel.id
            )
        )
        game.channel_id = match_channel.id
    else:
        _log.warning(
            f"could not find tribes_voice_category with id "
            f"{config.TRIBES_VOICE_CATEGORY_CHANNEL_ID} in guild"
        )

    # Post the first-pick-choice view in the match channel.
    if match_channel:
        draft_cog = bot.get_cog("DraftCommands")
        embed = create_draft_embed(session, game)
        embed.description = (
            f"<@{captain_a.id}> and <@{captain_b.id}> are captains. "
            f"<@{captain_b.id}>, do you want to pick first or second?"
        )
        view = FirstPickChoiceView(game.id, captain_b.id, draft_cog)
        message = await match_channel.send(embed=embed, view=view)
        game.message_id = message.id

    # Remove queue players (game has started) — same as create_game.
    session.query(QueuePlayer).filter(QueuePlayer.player_id.in_(player_ids)).delete()
    session.commit()

    # Advance rotation if not rolled random. Matches create_game behavior so
    # captain games and traditional games share the same rotation cadence.
    if not rolled_random_map:
        await execute_map_rotation(queue.rotation_id, False)

    # Notify the lobby channel that a draft has started.
    summary = Embed(
        title=f"📋 Captain pick draft '{queue.name}' ({short_game_id}) has begun",
        description=(
            f"Captains: <@{captain_a.id}> (🔴 {game.team0_name}), "
            f"<@{captain_b.id}> (🔵 {game.team1_name}).\n"
            f"Map: {next_map.full_name}.\n"
            f"Drafting in <#{match_channel.id}>."
            if match_channel
            else f"Captains: <@{captain_a.id}>, <@{captain_b.id}>"
        ),
        colour=Colour.blurple(),
    )
    await lobby_channel.send(embed=summary)


# ─────────────────────────────────────────────────────────────────────────────
# Draft finalization
# ─────────────────────────────────────────────────────────────────────────────


async def finalize_draft(game_id: str) -> None:
    """
    Called when the last pick lands. Computes win_probability, creates voice
    channels, posts the standard InProgressGameView (Win/Loss/Tie/Cancel),
    and flips is_drafting=False so the rest of the bot treats it as a
    normal in-progress game.
    """
    session: SQLAlchemySession
    with Session() as session:
        game: InProgressGame | None = (
            session.query(InProgressGame).filter(InProgressGame.id == game_id).first()
        )
        if not game or not game.is_drafting:
            _log.warning(f"[finalize_draft] game {game_id} not found or not drafting")
            return

        guild = bot.get_guild(_guild_id_for_game(game))
        if not guild:
            _log.warning(f"[finalize_draft] guild not found for game {game_id}")
            return

        igps: list[InProgressGamePlayer] = (
            session.query(InProgressGamePlayer)
            .filter(InProgressGamePlayer.in_progress_game_id == game.id)
            .all()
        )
        team0_player_ids = [igp.player_id for igp in igps if igp.team == 0]
        team1_player_ids = [igp.player_id for igp in igps if igp.team == 1]

        db_config: Config = session.query(Config).first()
        queue: Queue | None = (
            session.query(Queue).filter(Queue.id == game.queue_id).first()
        )

        team0_ratings: list[Rating] = []
        team1_ratings: list[Rating] = []
        for igp in igps:
            player = session.query(Player).filter(Player.id == igp.player_id).first()
            if not player or queue is None:
                continue
            mu, sigma = _player_rating(session, db_config, player, queue, game.map_id)
            rating = Rating(mu=mu, sigma=sigma)
            if igp.team == 0:
                team0_ratings.append(rating)
            else:
                team1_ratings.append(rating)

        if team0_ratings and team1_ratings:
            game.win_probability = win_probability_matchmaking(
                team0_ratings, team1_ratings
            )

        # Create voice channels under the tribes voice category.
        category_channel: discord.abc.GuildChannel | None = guild.get_channel(
            config.TRIBES_VOICE_CATEGORY_CHANNEL_ID
        )
        be_voice_channel = None
        ds_voice_channel = None
        if isinstance(category_channel, CategoryChannel):
            be_voice_channel = await guild.create_voice_channel(
                f"🔴 {game.team0_name}", category=category_channel
            )
            ds_voice_channel = await guild.create_voice_channel(
                f"🔵 {game.team1_name}", category=category_channel
            )
            session.add(
                InProgressGameChannel(
                    in_progress_game_id=game.id, channel_id=be_voice_channel.id
                )
            )
            session.add(
                InProgressGameChannel(
                    in_progress_game_id=game.id, channel_id=ds_voice_channel.id
                )
            )

        game.is_drafting = False
        session.commit()

        # Post the standard in-progress game embed and view in the match channel.
        match_channel = guild.get_channel(game.channel_id) if game.channel_id else None
        in_progress_game_cog = bot.get_cog("InProgressGameCommands")
        if (
            match_channel
            and in_progress_game_cog is not None
            and isinstance(in_progress_game_cog, InProgressGameCommands)
        ):
            embed = await create_in_progress_game_embed(session, game, guild)
            embed.title = (
                f"🚩 Game '{queue.name if queue else ''}' "
                f"({short_uuid(game.id)}) has begun!"
            )
            view = InProgressGameView(game.id, in_progress_game_cog)
            message = await match_channel.send(embed=embed, view=view)
            # Replace the draft message_id with the post-finalize message.
            game.message_id = message.id
            session.commit()

            # DM each player a copy of the embed with their voice channel link.
            send_message_coroutines = []
            for igp in igps:
                voice = be_voice_channel if igp.team == 0 else ds_voice_channel
                if voice:
                    send_message_coroutines.append(
                        send_in_guild_message(
                            guild,
                            igp.player_id,
                            message_content=voice.jump_url,
                            embed=embed,
                        )
                    )
            if send_message_coroutines:
                await asyncio.gather(*send_message_coroutines, return_exceptions=True)

        # Optionally move players to voice channels.
        if (
            config.ENABLE_VOICE_MOVE
            and queue
            and queue.move_enabled
            and be_voice_channel
            and ds_voice_channel
        ):
            await move_game_players(short_uuid(game.id), None, guild)


# ─────────────────────────────────────────────────────────────────────────────
# Sub-during-draft restart
# ─────────────────────────────────────────────────────────────────────────────


async def restart_draft_after_sub(
    game_id: str,
    was_captain_subbed: bool,
) -> None:
    """
    Reset a drafting game's pick state and re-prompt for first-pick choice.

    Called by /sub when a substitution happens mid-draft. Per the design:
    - Always: clear DraftPick rows, reset non-captain teams to NULL.
    - If a captain was subbed out: also clear is_captain on all rows and
      re-run captain selection on the new player set. The replacement
      player isn't auto-promoted to captain — the algorithm decides who
      the top two are from the post-sub roster.
    - The old draft message has its view stripped (so stale buttons can't
      be clicked) and a fresh FirstPickChoiceView is posted in its place;
      InProgressGame.message_id is updated.
    """
    from discord_bots.views.draft import FirstPickChoiceView

    session: SQLAlchemySession
    with Session() as session:
        game = (
            session.query(InProgressGame).filter(InProgressGame.id == game_id).first()
        )
        if not game or not game.is_drafting:
            return

        # 1. Clear pick history.
        session.query(DraftPick).filter(
            DraftPick.in_progress_game_id == game_id
        ).delete()

        # 2. Reset team assignments (and captains, if needed).
        igps: list[InProgressGamePlayer] = (
            session.query(InProgressGamePlayer)
            .filter(InProgressGamePlayer.in_progress_game_id == game_id)
            .all()
        )
        if was_captain_subbed:
            for igp in igps:
                igp.team = None
                igp.is_captain = False
            queue: Queue | None = (
                session.query(Queue).filter(Queue.id == game.queue_id).first()
            )
            if not queue:
                return
            player_ids = [igp.player_id for igp in igps]
            players: list[Player] = (
                session.query(Player).filter(Player.id.in_(player_ids)).all()
            )
            captain_a, captain_b = select_captains(session, players, queue, game.map_id)
            for igp in igps:
                if igp.player_id == captain_a.id:
                    igp.team = 0
                    igp.is_captain = True
                elif igp.player_id == captain_b.id:
                    igp.team = 1
                    igp.is_captain = True
        else:
            for igp in igps:
                if not igp.is_captain:
                    igp.team = None

        session.commit()

        # 3. Strip the old draft message's view; post a fresh first-pick
        # choice view and update message_id.
        guild = bot.get_guild(_guild_id_for_game(game))
        match_channel = (
            guild.get_channel(game.channel_id) if guild and game.channel_id else None
        )
        if not isinstance(match_channel, TextChannel):
            return

        if game.message_id:
            try:
                old_message = await match_channel.fetch_message(game.message_id)
                await old_message.edit(view=None)
            except Exception as e:
                _log.warning(
                    f"[restart_draft_after_sub] couldn't disable old "
                    f"message {game.message_id}: {e}"
                )

        captain_b_igp = (
            session.query(InProgressGamePlayer)
            .filter(
                InProgressGamePlayer.in_progress_game_id == game_id,
                InProgressGamePlayer.is_captain == True,
                InProgressGamePlayer.team == 1,
            )
            .first()
        )
        if not captain_b_igp:
            return

        draft_cog = bot.get_cog("DraftCommands")
        embed = create_draft_embed(session, game)
        embed.description = (
            "🔁 Draft restarted due to a substitution. "
            f"<@{captain_b_igp.player_id}>, do you want to pick first or "
            f"second?"
        )
        view = FirstPickChoiceView(game_id, captain_b_igp.player_id, draft_cog)
        message = await match_channel.send(embed=embed, view=view)
        game.message_id = message.id
        session.commit()


def _guild_id_for_game(game: InProgressGame) -> int:
    """
    Derive the guild_id from the bot's guilds. We don't store guild_id on
    InProgressGame, but the bot is typically in a single guild and the
    match channel belongs to it.
    """
    if game.channel_id:
        channel = bot.get_channel(game.channel_id)
        if channel and channel.guild:
            return channel.guild.id
    # Fallback: first guild the bot is in.
    if bot.guilds:
        return bot.guilds[0].id
    return 0
