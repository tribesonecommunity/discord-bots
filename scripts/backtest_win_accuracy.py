from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.models import FinishedGame, Session

"""
Measures the prediction accuracy of the last 1000 (default) matches.
Each finished game contains a win probability for team0 winning, so
we check this win probability against the match result to determine the overall accuracy.
"""
session: SQLAlchemySession = Session()
finished_games: list[FinishedGame] = (
    session.query(FinishedGame)
    .order_by(FinishedGame.finished_at.desc())
    .limit(1000)  # Increase or decrease based on preference
    .all()
)

total_matches = len(finished_games)
correct_predictions = 0
incorrect_predictions = 0
for game in finished_games:
    result = game.winning_team
    team0_win = True if result == 0 else False
    team0_win_probability = game.win_probability
    team1_win_probability = abs(1 - team0_win_probability)
    if (team0_win_probability > team1_win_probability) == team0_win:
        # team0 was predicted to win and they did actually win
        correct_predictions += 1
    else:
        incorrect_predictions += 1

accuracy = round(
    correct_predictions / (correct_predictions + incorrect_predictions) * 100, 2
)
print("Total Matches:", total_matches)
print(f"Accuracy: {correct_predictions}/{incorrect_predictions} [{accuracy:.2f}%]")
