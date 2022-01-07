from discord_bots.models import FinishedGame, FinishedGamePlayer, Player, Session

session = Session()
total_games = 0
print("id,name,games,rated_ts_mu,rated_ts_sigma,unrated_ts_mu,unrated_ts_sigma,diff")
players = session.query(Player).order_by(Player.rated_trueskill_mu.desc()).all()
for i, player in enumerate(players):
    total_games = 0
    wins = 0
    losses = 0
    ties = 0
    finished_game: FinishedGame
    finished_games = (
        session.query(FinishedGame)
        .join(
            FinishedGamePlayer, FinishedGame.id == FinishedGamePlayer.finished_game_id
        )
        .filter(FinishedGamePlayer.player_id == player.id)
        .all()
    )
    # fgp = session.query(FinishedGamePlayer).filter(FinishedGamePlayer.player_id == player.id).all()
    # print(player.name, len(finished_games), len(fgp))
    total_games += len(finished_games)
    # for finished_game in finished_games:
    #     team = (
    #         session.query(FinishedGamePlayer)
    #         .filter(
    #             FinishedGamePlayer.player_id == player.id,
    #             FinishedGamePlayer.finished_game_id == finished_game.id,
    #         )
    #         .first()
    #         .team
    #     )
    #     if finished_game.winning_team == team:
    #         wins += 1
    #     elif finished_game.winning_team == -1:
    #         ties += 1
    #     else:
    #         losses += 1
    print(
        f"{player.id},{player.name},{total_games},{player.rated_trueskill_mu},{player.rated_trueskill_sigma},{player.unrated_trueskill_mu},{player.rated_trueskill_sigma},{player.rated_trueskill_mu - player.unrated_trueskill_mu}"
    )
# print("total games:", total_games)
finished_games = session.query(FinishedGame).all()
# print("finished games:", len(finished_games))
