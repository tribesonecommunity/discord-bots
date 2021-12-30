from collections import defaultdict
from datetime import timedelta
import random

import matplotlib.pyplot as plt
from scipy.interpolate import interp1d

from models import FinishedGame, FinishedGamePlayer, Player, Session

cnames = {
    "aliceblue": "#F0F8FF",
    "antiquewhite": "#FAEBD7",
    "aqua": "#00FFFF",
    "aquamarine": "#7FFFD4",
    "azure": "#F0FFFF",
    "beige": "#F5F5DC",
    "bisque": "#FFE4C4",
    "black": "#000000",
    "blanchedalmond": "#FFEBCD",
    "blue": "#0000FF",
    "blueviolet": "#8A2BE2",
    "brown": "#A52A2A",
    "burlywood": "#DEB887",
    "cadetblue": "#5F9EA0",
    "chartreuse": "#7FFF00",
    "chocolate": "#D2691E",
    "coral": "#FF7F50",
    "cornflowerblue": "#6495ED",
    "cornsilk": "#FFF8DC",
    "crimson": "#DC143C",
    "cyan": "#00FFFF",
    "darkblue": "#00008B",
    "darkcyan": "#008B8B",
    "darkgoldenrod": "#B8860B",
    "darkgray": "#A9A9A9",
    "darkgreen": "#006400",
    "darkkhaki": "#BDB76B",
    "darkmagenta": "#8B008B",
    "darkolivegreen": "#556B2F",
    "darkorange": "#FF8C00",
    "darkorchid": "#9932CC",
    "darkred": "#8B0000",
    "darksalmon": "#E9967A",
    "darkseagreen": "#8FBC8F",
    "darkslateblue": "#483D8B",
    "darkslategray": "#2F4F4F",
    "darkturquoise": "#00CED1",
    "darkviolet": "#9400D3",
    "deeppink": "#FF1493",
    "deepskyblue": "#00BFFF",
    "dimgray": "#696969",
    "dodgerblue": "#1E90FF",
    "firebrick": "#B22222",
    "floralwhite": "#FFFAF0",
    "forestgreen": "#228B22",
    "fuchsia": "#FF00FF",
    "gainsboro": "#DCDCDC",
    "ghostwhite": "#F8F8FF",
    "gold": "#FFD700",
    "goldenrod": "#DAA520",
    "gray": "#808080",
    "green": "#008000",
    "greenyellow": "#ADFF2F",
    "honeydew": "#F0FFF0",
    "hotpink": "#FF69B4",
    "indianred": "#CD5C5C",
    "indigo": "#4B0082",
    "ivory": "#FFFFF0",
    "khaki": "#F0E68C",
    "lavender": "#E6E6FA",
    "lavenderblush": "#FFF0F5",
    "lawngreen": "#7CFC00",
    "lemonchiffon": "#FFFACD",
    "lightblue": "#ADD8E6",
    "lightcoral": "#F08080",
    "lightcyan": "#E0FFFF",
    "lightgoldenrodyellow": "#FAFAD2",
    "lightgreen": "#90EE90",
    "lightgray": "#D3D3D3",
    "lightpink": "#FFB6C1",
    "lightsalmon": "#FFA07A",
    "lightseagreen": "#20B2AA",
    "lightskyblue": "#87CEFA",
    "lightslategray": "#778899",
    "lightsteelblue": "#B0C4DE",
    "lightyellow": "#FFFFE0",
    "lime": "#00FF00",
    "limegreen": "#32CD32",
    "linen": "#FAF0E6",
    "magenta": "#FF00FF",
    "maroon": "#800000",
    "mediumaquamarine": "#66CDAA",
    "mediumblue": "#0000CD",
    "mediumorchid": "#BA55D3",
    "mediumpurple": "#9370DB",
    "mediumseagreen": "#3CB371",
    "mediumslateblue": "#7B68EE",
    "mediumspringgreen": "#00FA9A",
    "mediumturquoise": "#48D1CC",
    "mediumvioletred": "#C71585",
    "midnightblue": "#191970",
    "mintcream": "#F5FFFA",
    "mistyrose": "#FFE4E1",
    "moccasin": "#FFE4B5",
    "navajowhite": "#FFDEAD",
    "navy": "#000080",
    "oldlace": "#FDF5E6",
    "olive": "#808000",
    "olivedrab": "#6B8E23",
    "orange": "#FFA500",
    "orangered": "#FF4500",
    "orchid": "#DA70D6",
    "palegoldenrod": "#EEE8AA",
    "palegreen": "#98FB98",
    "paleturquoise": "#AFEEEE",
    "palevioletred": "#DB7093",
    "papayawhip": "#FFEFD5",
    "peachpuff": "#FFDAB9",
    "peru": "#CD853F",
    "pink": "#FFC0CB",
    "plum": "#DDA0DD",
    "powderblue": "#B0E0E6",
    "purple": "#800080",
    "red": "#FF0000",
    "rosybrown": "#BC8F8F",
    "royalblue": "#4169E1",
    "saddlebrown": "#8B4513",
    "salmon": "#FA8072",
    "sandybrown": "#FAA460",
    "seagreen": "#2E8B57",
    "seashell": "#FFF5EE",
    "sienna": "#A0522D",
    "silver": "#C0C0C0",
    "skyblue": "#87CEEB",
    "slateblue": "#6A5ACD",
    "slategray": "#708090",
    "snow": "#FFFAFA",
    "springgreen": "#00FF7F",
    "steelblue": "#4682B4",
    "tan": "#D2B48C",
    "teal": "#008080",
    "thistle": "#D8BFD8",
    "tomato": "#FF6347",
    "turquoise": "#40E0D0",
    "violet": "#EE82EE",
    "wheat": "#F5DEB3",
    "white": "#FFFFFF",
    "whitesmoke": "#F5F5F5",
    "yellow": "#FFFF00",
    "yellowgreen": "#9ACD32",
}
cname_keys = list(cnames.keys())

session = Session()
oldest_game: FinishedGame = (
    session.query(FinishedGame).order_by(FinishedGame.finished_at).first()
)
newest_game: FinishedGame = (
    session.query(FinishedGame)
    .order_by(FinishedGame.finished_at.desc())  # type: ignore
    .first()
)

duration_days = (newest_game.finished_at - oldest_game.finished_at).days
print(duration_days)

# x_axis = [i for i in range(duration_days)]
x_axis = [0]
sigma_y_axes = defaultdict(list)
mu_y_axes = defaultdict(list)

players = list(session.query(Player))
for player in players:
    sigma_y_axes[player.id].append(0)
    mu_y_axes[player.id].append(0)

current_date = oldest_game.finished_at
end_date = newest_game.finished_at
# end_date = oldest_game.finished_at + timedelta(days=30)

while current_date < end_date:
    x_axis.append(x_axis[-1] + 1)
    window_start = current_date
    window_end = current_date + timedelta(days=1)
    for player in players:
        last_game_of_day: FinishedGamePlayer = (
            session.query(FinishedGamePlayer)
            .join(FinishedGame)
            .filter(
                FinishedGamePlayer.player_id == player.id,
                FinishedGame.finished_at > window_start,
                FinishedGame.finished_at <= window_end,
            )
            .order_by(FinishedGame.finished_at.desc())  # type: ignore
            .first()
        )
        # print(last_game_of_day)
        if last_game_of_day:
            mu_y_axes[player.id].append(last_game_of_day.trueskill_mu_after)
            sigma_y_axes[player.id].append(last_game_of_day.trueskill_sigma_after)
        else:
            mu_y_axes[player.id].append(mu_y_axes[player.id][-1])
            sigma_y_axes[player.id].append(sigma_y_axes[player.id][-1])

    current_date += timedelta(days=2)
    print(current_date, newest_game.finished_at)


highest_rated_players = (
    session.query(Player).order_by(Player.trueskill_mu.desc()).limit(15).all()  # type: ignore
)

# print(sigma_y_axes)
for player in highest_rated_players:
    y = mu_y_axes[player.id]
    # for y in mu_y_axes.values():
    interpolate = interp1d(x_axis, y)
    plt.plot(x_axis, interpolate(x_axis), color=random.choice(cname_keys))

plt.show()
