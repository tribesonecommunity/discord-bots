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


    team1_ratings = list(
        map(lambda x: Rating(x.trueskill_mu, x.trueskill_sigma), team1_players)
    )
    team2_ratings = list(
        map(lambda x: Rating(x.trueskill_mu, x.trueskill_sigma), team2_players)
    )
    outcome = None
    if match["winningTeam"] == 0:
        outcome = [0, 0]
    elif match["winningTeam"] == 1:
        outcome = [0, 1]
    elif match["winningTeam"] == 2:
        outcome = [1, 0]
    team1_new_ratings, team2_new_ratings = rate([team1_ratings, team2_ratings], outcome)

    win_prob = win_probability(team1_ratings, team2_ratings)

    finished_game = FinishedGame(
        average_trueskill=0.0,
        game_id=str(uuid4()),
        finished_at=datetime.fromtimestamp(match["timestamp"] // 1000),
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
            trueskill_mu_before=team1_ratings[i].mu,
            trueskill_sigma_before=team1_ratings[i].sigma,
            trueskill_mu_after=team1_new_ratings[i].mu,
            trueskill_sigma_after=team1_new_ratings[i].sigma,
        )
        player.trueskill_mu = team1_new_ratings[i].mu
        player.trueskill_sigma = team1_new_ratings[i].sigma
        session.add(finished_game_player)

    for i, player in enumerate(team2_players):
        finished_game_player = FinishedGamePlayer(
            finished_game_id=finished_game.id,
            player_id=player.id,
            player_name=player.name,
            team=1,
            trueskill_mu_before=team2_ratings[i].mu,
            trueskill_sigma_before=team2_ratings[i].sigma,
            trueskill_mu_after=team2_new_ratings[i].mu,
            trueskill_sigma_after=team2_new_ratings[i].sigma,
        )
        player.trueskill_mu = team2_new_ratings[i].mu
        player.trueskill_sigma = team2_new_ratings[i].sigma
        session.add(finished_game_player)

    session.commit()
