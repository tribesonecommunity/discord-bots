# Misc helper functions
import itertools
import math
from datetime import datetime, timezone, tzinfo

from trueskill import Rating, global_env

from discord_bots.models import CurrentMap, Player, RotationMap, Session


def pretty_format_team(
    team_name: str, win_probability: float, players: list[Player]
) -> str:
    player_names = ", ".join(sorted([player.name for player in players]))
    return f"**{team_name}** ({round(100 * win_probability, 1)}%): {player_names}\n"


def short_uuid(uuid: str) -> str:
    return uuid.split("-")[0]


def update_current_map_to_next_map_in_rotation():
    session = Session()
    current_map: CurrentMap = session.query(CurrentMap).first()
    rotation_maps: list[RotationMap] = session.query(RotationMap).order_by(RotationMap.created_at.asc()).all()  # type: ignore
    if len(rotation_maps) > 0:
        if current_map:
            next_rotation_map_index = (current_map.map_rotation_index + 1) % len(
                rotation_maps
            )
            next_map = rotation_maps[next_rotation_map_index]
            current_map.map_rotation_index = next_rotation_map_index
            current_map.full_name = next_map.full_name
            current_map.short_name = next_map.short_name
            current_map.updated_at = datetime.now(timezone.utc)
        else:
            next_map = rotation_maps[0]
            session.add(CurrentMap(0, next_map.full_name, next_map.short_name))
        session.commit()


def win_probability(team0: list[Rating], team1: list[Rating]) -> float:
    """
    Calculate the probability that team0 beats team1
    Taken from https://trueskill.org/#win-probability
    """
    BETA = 4.1666
    delta_mu = sum(r.mu for r in team0) - sum(r.mu for r in team1)
    sum_sigma = sum(r.sigma ** 2 for r in itertools.chain(team0, team1))
    size = len(team0) + len(team1)
    denom = math.sqrt(size * (BETA * BETA) + sum_sigma)
    trueskill = global_env()

    return trueskill.cdf(delta_mu / denom)
