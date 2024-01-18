from collections import defaultdict
from typing import List
import datetime
import pytz

from discord_bots.models import FinishedGame, FinishedGamePlayer, Player, Session

"""
Dump stats for the season
- Player progress throughout the season
- Most improved player
- Least improved player
- Players with most games
- Players with least games
- Least variance player
- Highest variance player
"""
cutoff_ts = "2023-10-21 20:17:05.681369"
eligible_player_ids = [
    508003755220926464,
    150452429610156033,
    335279105685585921,
    367866724550180867,
    209494363397423105,
    137361565522329601,
    115204465589616646,
    783502136049795072,
    161583081013116931,
    194675870248468481,
    219746505278488576,
    488884487552237579,
    267469062420955148,
    463173056869957644,
    370328859440054286,
    240609531636219906,
    299913378577645570,
    105789018876157952,
    457264042873192466,
    107263626468929536,
    341813225202909186,
    329844172410585091,
    451223075338453005,
    522296332526813207,
    297914408976515073,
    541726676963426336,
    280230988779356160,
    126900407975936001,
    461006508235292672,
    223646625480835073,
    317868909711720448,
    179402414082883584,
    463121657125666816,
    454811555331309568,
    320001580332220416,
    201422912094339072,
    192662572858605568,
    600137738007871522,
    268163084785287178,
    411820440072224768,
    939950815945314364,
    914896226707730494,
    1139739289182273728,
    688564219742126178,
    134333079622778880,
]


def main():
    session = Session()

    finished_games: List[FinishedGame] = list(
        session.query(FinishedGame)
        .filter(FinishedGame.started_at >= cutoff_ts)
        .order_by(FinishedGame.started_at.asc())
    )
    finished_game_ids = map(lambda x: x.id, finished_games)

    # Dump the number of games per day
    date_buckets = defaultdict(int)
    for finished_game in finished_games:
        date = finished_game.started_at.astimezone(pytz.timezone('America/Los_Angeles'))
        bucket = datetime.datetime(*date.timetuple()[:3])
        date_buckets[bucket] += 1
    for k, v in date_buckets.items():
        print(k, v)
    # print(date_buckets)

    # finished_game_players = session.query(FinishedGamePlayer).filter(
    #     FinishedGamePlayer.finished_game_id.in_(finished_game_ids)
    # )
    # player_ids = list(set(map(lambda x: x.player_id, finished_game_players)))
    # players = (
    #     session.query(Player)
    #     .filter(Player.id.in_(eligible_player_ids))
    #     .order_by(Player.rated_trueskill_mu.desc())
    # )
    # finished_game_players = session.query(FinishedGamePlayer).filter(
    #     FinishedGamePlayer.finished_game_id.in_(finished_game_ids)
    # )

    # Dump the number of games per player, the mu per player
    # for player in players:
    #     # print(f"{player.name},{player.rated_trueskill_mu}")
    #     finished_game_players_for_player = filter(
    #         lambda x: x.player_id == player.id, finished_game_players
    #     )
    #     print(len(list(finished_game_players_for_player)), player.name)

    # Dumping table of players and mu per game, to plot the full season graph
    # header = [""] + list(map(lambda x: x.name, players))
    # print(",".join(header))
    # for finished_game in finished_games:
    #     finished_game_players = session.query(FinishedGamePlayer).filter(
    #         FinishedGamePlayer.finished_game_id == finished_game.id
    #     )
    #     finished_game_players_by_player_id = {
    #         finished_game_player.player_id: finished_game_player
    #         for finished_game_player in finished_game_players
    #     }
    #     row = [finished_game.started_at.strftime('%s')]
    #     for player in players:
    #         finished_game_player = finished_game_players_by_player_id.get(player.id)
    #         if finished_game_player:
    #             row.append(str(finished_game_player.rated_trueskill_mu_before))
    #         else:
    #             row.append("")
    #     print(','.join(row))


if __name__ == "__main__":
    main()
