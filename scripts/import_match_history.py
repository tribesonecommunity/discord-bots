import json
from datetime import datetime
from uuid import uuid4

from trueskill import Rating, rate

from discord_bots.models import FinishedGame, FinishedGamePlayer, Player, Session
from discord_bots.utils import win_probability

DATA_FILE = "out.json"

data = json.load(open(DATA_FILE))

session = Session()

for i, match in enumerate(data):
    print(i, len(data), i / len(data))
    if match["queue"]["name"] == "bottest":
        continue

    team1_players = []
    team2_players = []
    for json_player in match["players"]:
        player: Player
        player_id = json_player["user"]["id"]
        player = (
            session.query(Player).filter(Player.id == json_player["user"]["id"]).first()
        )
        if not player:
            player = Player(id=player_id, name=json_player["user"]["name"])
            session.add(player)

        if json_player["team"] == 1:
            team1_players.append(player)
        else:
            team2_players.append(player)

    rated_team1_ratings = list(
        map(
            lambda x: Rating(x.rated_trueskill_mu, x.rated_trueskill_sigma),
            team1_players,
        )
    )
    rated_team2_ratings = list(
        map(
            lambda x: Rating(x.rated_trueskill_mu, x.rated_trueskill_sigma),
            team2_players,
        )
    )
    unrated_team1_ratings = list(
        map(
            lambda x: Rating(x.unrated_trueskill_mu, x.unrated_trueskill_sigma),
            team1_players,
        )
    )
    unrated_team2_ratings = list(
        map(
            lambda x: Rating(x.unrated_trueskill_mu, x.unrated_trueskill_sigma),
            team2_players,
        )
    )

    outcome = None
    if match["winningTeam"] == 0:
        outcome = [0, 0]
    elif match["winningTeam"] == 1:
        outcome = [0, 1]
    elif match["winningTeam"] == 2:
        outcome = [1, 0]
    is_rated = match["queue"]["name"] != "LTunrated"
    if is_rated:
        rated_team1_new_ratings, rated_team2_new_ratings = rate(
            [rated_team1_ratings, rated_team2_ratings], outcome
        )
    else:
        rated_team1_new_ratings, rated_team2_new_ratings = (
            rated_team1_ratings,
            rated_team2_ratings,
        )

    unrated_team1_new_ratings, unrated_team2_new_ratings = rate(
        [unrated_team1_ratings, unrated_team2_ratings], outcome
    )

    win_prob = win_probability(rated_team1_ratings, rated_team2_ratings)

    finished_game = FinishedGame(
        average_trueskill=0.0,
        game_id=str(uuid4()),
        finished_at=datetime.fromtimestamp(match["timestamp"] // 1000),
        is_rated=is_rated,
        queue_name=match["queue"]["name"],
        started_at=datetime.fromtimestamp(match["completionTimestamp"] // 1000),
        win_probability=win_prob,
        winning_team=match["winningTeam"] - 1,
    )
    session.add(finished_game)

    for i, player in enumerate(team1_players):
        finished_game_player = FinishedGamePlayer(
            finished_game_id=finished_game.id,
            player_id=player.id,
            player_name=player.name,
            team=0,
            rated_trueskill_mu_before=rated_team1_ratings[i].mu,
            rated_trueskill_sigma_before=rated_team1_ratings[i].sigma,
            rated_trueskill_mu_after=rated_team1_new_ratings[i].mu,
            rated_trueskill_sigma_after=rated_team1_new_ratings[i].sigma,
            unrated_trueskill_mu_before=unrated_team1_ratings[i].mu,
            unrated_trueskill_sigma_before=unrated_team1_ratings[i].sigma,
            unrated_trueskill_mu_after=unrated_team1_new_ratings[i].mu,
            unrated_trueskill_sigma_after=unrated_team1_new_ratings[i].sigma,
        )
        player.rated_trueskill_mu = rated_team1_new_ratings[i].mu
        player.rated_trueskill_sigma = rated_team1_new_ratings[i].sigma
        player.unrated_trueskill_mu = unrated_team1_new_ratings[i].mu
        player.unrated_trueskill_sigma = unrated_team1_new_ratings[i].sigma
        session.add(finished_game_player)

    for i, player in enumerate(team2_players):
        finished_game_player = FinishedGamePlayer(
            finished_game_id=finished_game.id,
            player_id=player.id,
            player_name=player.name,
            team=1,
            rated_trueskill_mu_before=rated_team2_ratings[i].mu,
            rated_trueskill_sigma_before=rated_team2_ratings[i].sigma,
            rated_trueskill_mu_after=rated_team2_new_ratings[i].mu,
            rated_trueskill_sigma_after=rated_team2_new_ratings[i].sigma,
            unrated_trueskill_mu_before=unrated_team2_ratings[i].mu,
            unrated_trueskill_sigma_before=unrated_team2_ratings[i].sigma,
            unrated_trueskill_mu_after=unrated_team2_new_ratings[i].mu,
            unrated_trueskill_sigma_after=unrated_team2_new_ratings[i].sigma,
        )
        player.rated_trueskill_mu = rated_team2_new_ratings[i].mu
        player.rated_trueskill_sigma = rated_team2_new_ratings[i].sigma
        player.unrated_trueskill_mu = unrated_team2_new_ratings[i].mu
        player.unrated_trueskill_sigma = unrated_team2_new_ratings[i].sigma
        session.add(finished_game_player)

    session.commit()
