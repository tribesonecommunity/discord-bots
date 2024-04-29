import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy
from dateutil.parser import parse as parse_date
from sqlalchemy import or_
from table2ascii import Alignment, PresetStyle, table2ascii
from trueskill import Rating, rate
from typing_extensions import Literal

from discord_bots.models import (
    Category,
    FinishedGame,
    FinishedGamePlayer,
    Player,
    PlayerCategoryTrueskill,
    Session,
)

level = logging.INFO


def define_logger(name="app"):
    log = logging.getLogger(name)
    log.propagate = False
    log.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s:%(filename)s:%(lineno)s] %(message)s"
    )
    console_logger = logging.StreamHandler()
    console_logger.setLevel(level)
    console_logger.setFormatter(formatter)
    log.addHandler(console_logger)
    return log


log = define_logger("soft_reset")

OutcomeType = Literal["team1", "team2", "tie"]
default_rating = Rating()
DRAW = [0, 0]
TEAM1_WIN = [0, 1]
TEAM2_WIN = [1, 0]


@dataclass
class RawGame:
    team1: list[int]
    team2: list[int]
    outcome: OutcomeType
    rated: bool


@dataclass
class PlayerRating:
    id: int
    mu: float
    sigma: float


def parse_args() -> dict[str, any]:
    parser = argparse.ArgumentParser(
        description="Soft reset a category. Defaults to 'dry run' unless specifically told to overwrite data.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--store",
        default="False",
        help="If 'True' will store the recalculated ratings. "
        "Defaults to 'False', which is a 'Dry Run' mode.",
    )
    parser.add_argument(
        "--src-categories",
        nargs="*",
        help="Categories to source finished games from. "
        "At least one of --src-queues or --src-categories must be supplied",
    )
    parser.add_argument(
        "--src-queues",
        nargs="*",
        help="Queues to source finished games from, regardless of set category. "
        "At least one of --src-queues or --src-category must be supplied",
    )
    parser.add_argument(
        "--from",
        help="Date to start rating games. Leave empty to use all games, "
        "supply date in format YYYY-MM-DD or YYYY-MM-DDThh:mm:ss "
        "to start fetching games fom that date at UTC 00:00 AM",
    )
    parser.add_argument(
        "--target-category",
        help="Name of the category to calculate or recalculate",
        required=True,
    )
    arguments = parser.parse_args()
    return vars(arguments)


def map_raw_games(
    game_history: list[FinishedGame], game_players: list[FinishedGamePlayer]
) -> list[RawGame]:
    output: list[RawGame] = []

    log.info(f"Started mapping {len(game_history)} games")
    for idx, game in enumerate(game_history):
        players = list(
            filter(lambda fgp: fgp.finished_game_id == game.id, game_players)
        )
        players_team1 = list(filter(lambda fgp: fgp.team == 0, players))
        players_team2 = list(filter(lambda fgp: fgp.team == 1, players))
        num_players_team1 = len(players_team1)
        num_players_team2 = len(players_team2)
        if num_players_team1 == 0:
            log.warning(f"Ignoring game {game.game_id}. No players on team1")
            continue
        if num_players_team2 == 0:
            log.warning(f"Ignoring game {game.game_id}. No players on team2")
            continue
        if num_players_team1 != num_players_team2:
            log.warning(f"Ignoring game {game.game_id}. Player count not balanced.")
            continue
        # noinspection PyTypeChecker
        outcome: OutcomeType = (
            "team1"
            if game.winning_team == 0
            else ("team2" if game.winning_team == 1 else "tie")
        )
        output.append(
            RawGame(
                team1=list(map(lambda fgp: fgp.player_id, players_team1)),
                team2=list(map(lambda fgp: fgp.player_id, players_team2)),
                outcome=outcome,
                rated=game.is_rated,
            )
        )
        if idx % 1000 == 0 and idx > 0:
            log.info(f"Mapped {idx}/{len(game_history)} games")

    log.info(f"Finished mapping {len(game_history)} games")
    return output


def rate_games(games: list[RawGame]) -> dict[int, PlayerRating]:
    def get_player_or_default(
        players: dict[int, PlayerRating], player_id: int
    ) -> PlayerRating:
        player = players.get(player_id)
        if player is None:
            player = PlayerRating(
                id=player_id,
                mu=default_rating.mu,
                sigma=default_rating.sigma,
            )
            players.update({player_id: player})
        return player

    def player_to_rating(player: PlayerRating) -> Rating:
        return Rating(
            mu=player.mu,
            sigma=player.sigma,
        )

    def update_ratings(
        team: list[PlayerRating], new_ratings: list[Rating], rated: bool
    ):
        for update_idx, player in enumerate(team):
            if rated:
                player.mu = new_ratings[update_idx].mu
                player.sigma = new_ratings[update_idx].sigma

    result: dict[int, PlayerRating] = {}
    log.info(f"Started rating {len(games)} games")
    for idx, game in enumerate(games):
        team1 = list(map(lambda i: get_player_or_default(result, i), game.team1))
        team2 = list(map(lambda i: get_player_or_default(result, i), game.team2))
        team1_ratings_before = list(map(lambda p: player_to_rating(p), team1))
        team2_ratings_before = list(map(lambda p: player_to_rating(p), team2))
        game_result = (
            TEAM1_WIN
            if game.outcome == "team1"
            else (TEAM2_WIN if game.outcome == "team2" else DRAW)
        )
        team1_ratings_after, team2_ratings_after = rate(
            [team1_ratings_before, team2_ratings_before], game_result
        )
        update_ratings(team1, team1_ratings_after, game.rated)
        update_ratings(team2, team2_ratings_after, game.rated)

        if idx % 1000 == 0 and idx > 0:
            log.info(f"Rated {idx}/{len(games)} games")

    log.info(f"Finished rating {len(games)} games")
    return result


def map_ratings_to_entities(
    session: Session, ratings: dict[int, PlayerRating], target_category_id: str
) -> list[
    tuple[Player, PlayerCategoryTrueskill | None, PlayerCategoryTrueskill | None]
]:
    """
    map for update or display
    :param session: session object
    :param ratings: calculated ratings
    :param target_category_id: target category
    :return: player -> (old_rating, new_rating)
    """
    new_players: list[Player] = (
        session.query(Player).filter(Player.id.in_(list(ratings.keys()))).all()
    )
    old_ratings_result = (
        session.query(PlayerCategoryTrueskill, Player)
        .filter(
            PlayerCategoryTrueskill.player_id == Player.id,
            PlayerCategoryTrueskill.category_id == target_category_id,
        )
        .all()
    )
    old_pcts: list[PlayerCategoryTrueskill] = [x[0] for x in old_ratings_result]
    old_players: list[Player] = [x[1] for x in old_ratings_result]
    all_players = numpy.unique(old_players + new_players)

    result: list[
        tuple[Player, PlayerCategoryTrueskill | None, PlayerCategoryTrueskill | None]
    ] = []
    player: Player
    for player in all_players:
        old_pct = next(
            iter(filter(lambda pct: pct.player_id == player.id, old_pcts)), None
        )
        new_pct = None
        rating = ratings.get(player.id)
        if rating is not None:
            new_pct = PlayerCategoryTrueskill(
                player_id=rating.id,
                category_id=target_category_id,
                mu=rating.mu,
                sigma=rating.sigma,
                rank=rating.mu - (3 * rating.sigma),
                last_game_finished_at=(
                    old_pct.last_game_finished_at
                    if old_pct
                    else datetime.now(timezone.utc)
                ),
            )
        result.append((player, old_pct, new_pct))
    return result


def print_ratings_change(
    new_rating_entries: list[
        tuple[Player, PlayerCategoryTrueskill | None, PlayerCategoryTrueskill | None]
    ]
) -> None:
    # TODO: write to csv instead of printing as a table
    entries = new_rating_entries.copy()
    # sort by new rating, then old rating, then player-id
    entries.sort(
        key=lambda x: (
            x[2].rank if x[2] is not None else -1,
            x[1].rank if x[1] is not None else -1,
            x[0].id,
        ),
        reverse=True,
    )
    cols = []
    for entry in entries:
        col = []
        player = entry[0]
        old_rating = entry[1]
        new_rating = entry[2]
        escaped_name = player.name.replace('"', "\\")
        if old_rating is None:
            old_rating = default_rating
        if new_rating is None:
            new_rating = default_rating
        old_rank = old_rating.mu - (3 * old_rating.sigma)
        new_rank = new_rating.mu - (3 * new_rating.sigma)
        col.append(f'"{escaped_name}"')
        col.append(f"{round(new_rank, 2)} [{round(new_rank - old_rank, 2)}]")
        col.append(
            f"{round(new_rating.mu, 2)} [{round(new_rating.mu - old_rating.mu, 2)}]"
        )
        col.append(
            f"{round(new_rating.sigma, 2)} [{round(new_rating.sigma - old_rating.sigma, 2)}]"
        )
        col.append(f"{round(old_rank, 2)}")
        col.append(f"{round(old_rating.mu, 2)}")
        col.append(f"{round(old_rating.sigma, 2)}")
        cols.append(col)

    header = [
        "name",
        "new_rank",
        "new_mu",
        "new_sigma",
        "old_rank",
        "old_mu",
        "old_sigma",
    ]
    table = table2ascii(
        header=header,
        body=cols,
        first_col_heading=True,
        style=PresetStyle.plain,
        alignments=Alignment.LEFT,
    )
    log.info("### Changed Ratings:\n" + table)


def store_updated_ratings(
    session: Session,
    new_rating_entries: list[
        tuple[Player, PlayerCategoryTrueskill | None, PlayerCategoryTrueskill | None]
    ],
    target_category_id: str,
) -> None:
    session.query(PlayerCategoryTrueskill).filter(
        PlayerCategoryTrueskill.category_id == target_category_id
    ).delete()
    new_ratings = [x[2] for x in new_rating_entries if x[2] is not None]
    for new_rating in new_ratings:
        session.add(new_rating)


def do_soft_reset(
    target_category_name: str,
    src_categories: list[str],
    src_queues: list[str],
    from_date: datetime,
    dry_run: bool,
) -> None:
    log.info(
        f"Executing soft reset: target category {target_category_name}, source regions: {src_categories}, "
        f"source queues: {src_queues}, from: {from_date}, dry run: {dry_run}"
    )
    if dry_run:
        log.info(f"Executing dry run")
    else:
        log.warning(f"Real mode. Data will be overwritten!")

    with Session() as session:
        target_category: Category | None = (
            session.query(Category)
            .filter(target_category_name == Category.name)
            .first()
        )
        if target_category is None:
            raise ValueError(f"Category {target_category_name} does not exist")

        log.info("Loading game history")
        # noinspection PyUnresolvedReferences
        game_history: list[FinishedGame] = (
            session.query(FinishedGame)
            .filter(
                or_(
                    FinishedGame.queue_name.in_(src_queues),
                    FinishedGame.category_name.in_(src_categories),
                ),
                FinishedGame.finished_at >= from_date,
            )
            .order_by(FinishedGame.finished_at.asc())
            .all()
        )
        # noinspection PyUnresolvedReferences
        game_players: list[FinishedGamePlayer] = (
            session.query(FinishedGamePlayer)
            .filter(
                FinishedGamePlayer.finished_game_id.in_(
                    list(map(lambda x: x.id, game_history))
                )
            )
            .all()
        )
        log.info("Finished loading game history")

        games = map_raw_games(game_history, game_players)
        ratings = rate_games(games)
        new_rating_entries = map_ratings_to_entities(
            session, ratings, target_category.id
        )
        print_ratings_change(new_rating_entries)

        if not dry_run:
            store_updated_ratings(session, new_rating_entries, target_category.id)
            session.commit()


def main() -> None:
    input_args = parse_args()
    if input_args["src_queues"] is None and input_args["src_categories"] is None:
        log.error("At least one of --src-queues or --src-categories must be supplied")
        exit(1)

    target_category_name = input_args["target_category"]
    src_queues = (
        input_args["src_queues"] if input_args["src_queues"] is not None else []
    )
    src_categories = (
        input_args["src_categories"] if input_args["src_categories"] is not None else []
    )
    dry_run = False if input_args["store"].lower() == "true" else True
    from_date = (
        parse_date(input_args["from"])
        if input_args["from"] is not None
        else datetime(1990, 1, 1)
    )

    do_soft_reset(
        target_category_name=target_category_name,
        src_categories=src_categories,
        src_queues=src_queues,
        from_date=from_date,
        dry_run=dry_run,
    )


if __name__ == "__main__":
    main()
