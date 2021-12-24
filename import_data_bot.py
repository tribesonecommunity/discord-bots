# Relevant libraries
import heapq
import pandas as pd
import numpy as np
import math
import trueskill as ts
import itertools
import urllib.request, json

# This is the server that implosions hosts the data on
with urllib.request.urlopen("http://50.116.36.119/api/server/127155819698454529/games/1546300801000") as url: #1546300801000") as url:
    df = json.loads(url.read().decode())
    #df= pd.read_json(url.read(), orient = 'records')
    
from pandas.io.json import json_normalize #package for flattening json in pandas df

# Creates long form dataframe
df = json_normalize(data = df, record_path='players', meta = ['completionTimestamp', 'winningTeam', ['queue', 'name']])
df = pd.concat([df, df['user'].apply(pd.Series)], axis = 1) #switched user to id
df = df[['captain', 'pickOrder', 'team', 'completionTimestamp', 'winningTeam', 'name', 'queue.name']] #added id
#df.id.astype(str)

def win(series):
    if series['winningTeam'] == 0:
        return 'Tie'
    if series['team'] == series['winningTeam']:
        return 'Win'
    return 'Loss'

#print(df['queue'])#['name'])
#print(type(df['queue']))

df['Win'] = df.apply(win, axis = 1)
df['MatchID'] = df['completionTimestamp']
df['QueueData'] = df['queue.name']
df.rename(columns = {'name':'Player'}, inplace = True) #changed 'name':'Player' to 'id':'Player'

df = df[df.QueueData != 'LTunrated']
df = df[df.QueueData != 'LTgold']
df = df[df.QueueData != 'LTpug']
df = df[df.QueueData != 'LTsilver']
df = df[df.QueueData != 'bottest']
df = df[df.QueueData != 'LTeuro']
df = df[df.QueueData != 'LTpug+']
df = df[df.QueueData != 'TR']
df = df[df.QueueData != 'TD']
#df = df[df.QueueData != 'LTbot']

'''df = json_normalize(data = df, record_path='players', meta = ['completionTimestamp', 'winningTeam'])
df = pd.concat([df, df['user'].apply(pd.Series)], axis = 1)
df = df[['captain', 'pickOrder', 'team', 'completionTimestamp', 'winningTeam', 'name']]

def win(series):
    if series['winningTeam'] == 0:
        return 'Tie'
    if series['team'] == series['winningTeam']:
        return 'Win'
    return 'Loss'
df['Win'] = df.apply(win, axis = 1)
df['MatchID'] = df['completionTimestamp']
df.rename(columns = {'name':'Player'}, inplace = True)
'''
# Create wide format dataframe
temp1 = df.groupby(['MatchID', 'Win'])['Player'].apply(list)
temp1 = temp1.unstack('Win')

if 'Tie' in temp1.columns:
    temp1 = temp1[['Win', 'Loss']].fillna(temp1['Tie'])
temp1.dropna(inplace = True)

'''
for item in temp1.Loss.values.tolist():
    if len(item) > 5:
        print(temp1.index)
        print(item)
        
for item in temp1.Win.values.tolist():
    if len(item) > 5:
        print(item)              
'''
winner_cols = ['WPlayer1', 'WPlayer2', 'WPlayer3', 'WPlayer4', 'WPlayer5']#, 'WPlayer6']
loser_cols = ['LPlayer1', 'LPlayer2', 'LPlayer3', 'LPlayer4', 'LPlayer5']#, 'LPlayer6']

match_df = pd.merge(pd.DataFrame(temp1.Win.values.tolist(), index= temp1.index, columns = winner_cols).reset_index(), \
         pd.DataFrame(temp1.Loss.values.tolist(), index= temp1.index, columns = loser_cols).reset_index(), \
         on = 'MatchID')

#match_df.drop(['WPlayer6', 'LPlayer6'], axis=1, inplace = True)

match_df['winningSide'] = match_df['MatchID'].map(dict(zip(df['MatchID'], df['winningTeam'])))

def WpickOrder(series):
    return 'First Pick' if series['winningSide'] == 1 else 'Second Pick'

def LpickOrder(series):
    return 'Second Pick' if series['winningSide'] == 1 else 'First Pick'

match_df['WpickOrder'] = match_df.apply(WpickOrder, axis = 1)
match_df['LpickOrder'] = match_df.apply(LpickOrder, axis = 1)


# Creates records of player ratings over time
df = match_df.copy()
df.replace({"[KoV]LordKermit": 'LordKermit'}, regex = True, inplace = True)

player_cols = ['WPlayer1', 'WPlayer2', 'WPlayer3', 'WPlayer4', 'WPlayer5', 'LPlayer1', 'LPlayer2', 'LPlayer3', 'LPlayer4', 'LPlayer5']
players = list(set([item for sublist in df[player_cols].values.tolist() for item in sublist])) + ['First Pick', 'Second Pick']
players_ts = dict(zip(players, [ts.Rating() for i in players]))
players_ts_time = {player: [] for player in players}



# Creates a dataframe of rankings
def rankings(df, num_games, ties):
    winner_cols = ['WPlayer1', 'WPlayer2', 'WPlayer3', 'WPlayer4', 'WPlayer5', 'WpickOrder']
    loser_cols = ['LPlayer1', 'LPlayer2', 'LPlayer3', 'LPlayer4', 'LPlayer5', 'LpickOrder']

    #players_ts['Pikaboo'] = ts.Rating(mu = 18.5, sigma = 3.0)
    #players_ts['humps'] = ts.Rating(mu = 21.0, sigma = 2.5)
    #players_ts['_Moose'] = ts.Rating(mu = 26.5, sigma = 2.0)

    for i, row in df.iterrows():
    # Find ratings in dictionary
        winner_list = list(filter(None, list(row[winner_cols])))
        loser_list = list(filter(None, list(row[loser_cols])))
        t1 = [players_ts[player] for player in winner_list]
        t2 = [players_ts[player] for player in loser_list]
    # Get ratings after match
        a, b = (ts.rate([t1, t2], ranks=[0, 1]))
    #print (a,b)
        if ties == True:
            if row['Winner'] == 'Tie':
                a, b = (ts.rate([t1, t2], ranks=[0, 0]))
        # Update ratings in dictionary (not necessary to split winners and losers, but easier to read and debug)
        for i, player in list(enumerate(winner_list)):
            players_ts[player] = a[i]
            players_ts_time[player].append(a[i].mu)
        for i, player in list(enumerate(loser_list)):
            players_ts[player] = b[i]
            players_ts_time[player].append(b[i].mu)
    
    sorted_rating_list = sorted(((value.mu, key) for (key,value) in players_ts.items()), reverse = True)
    rating_sigma_list = sorted(((round(value.mu, 1), round(value.sigma, 1), key) for (key,value) in players_ts.items()), reverse = True)
    adj_rating_list = sorted([(round((item[0] - 2*item[1]),2), item[0], item[1], item[2]) for item in rating_sigma_list], reverse = True)

    tuple_df = pd.DataFrame(adj_rating_list, columns=['adj_rating', 'rating', 'uncertainty', 'name'])
    record_df = pd.DataFrame([(item[1], df[winner_cols].isin([item[1]]).any(axis = 1).sum(), df[loser_cols].isin([item[1]]).any(axis = 1).sum(), round(df[winner_cols].isin([item[1]]).any(axis = 1).sum()/df[loser_cols].isin([item[1]]).any(axis = 1).sum(), 1)) for item in sorted_rating_list], columns = ['name', 'wins', 'losses', 'ratio'])


    overall_rating_df = pd.merge(tuple_df, record_df, on = 'name', how = 'left')[['name', 'wins', 'losses', 'ratio', 'rating', 'uncertainty', 'adj_rating']]
    overall_rating_df['ratio'] = overall_rating_df['ratio'].apply(lambda x: round(x, 1))
    overall_rating_df = overall_rating_df.sort_values(by = 'adj_rating', ascending = False).reindex()
    return overall_rating_df[(overall_rating_df['wins'] + overall_rating_df['losses']) >= num_games]

ranking_df = rankings(df, 5, ties = False)

pd.set_option('display.expand_frame_repr', False)
with pd.option_context('display.max_rows', None, 'display.max_columns', None):
    print(ranking_df)

#import seaborn as sns
#more_games_df = ranking_df[(ranking_df['wins'] + ranking_df['losses']) >= 25].reset_index(drop = True)
#sns.lmplot(data=more_games_df, x="adj_rating",  y="rating", hue="name", fit_reg=False) 

#ranking_df.to_csv()

# Helper function for make_teams
def win_probability(team1, team2):
    BETA = 4.1666
    delta_mu = sum(r.mu for r in team1) - sum(r.mu for r in team2)
    sum_sigma = sum(r.sigma ** 2 for r in itertools.chain(team1, team2))
    size = len(team1) + len(team2)
    denom = math.sqrt(size * (BETA * BETA) + sum_sigma)
    trueskill = ts.global_env()
    return round(trueskill.cdf(delta_mu / denom), 2)

# Given a list of 10 players:
# 1) Returns a dataframe of all combinations with win_probability between 45-55% ("fair")
# 2) prints a random 5v5 combination that has win_probability between 45-55%

def make_teams(player_list):
    BETA = 4.1666
    matches = []
    for team1 in list(itertools.combinations(player_list, r= 5)):
        team2 = set(player_list) - set(team1)
        win_prob = win_probability(
            [players_ts[player] for player in team1],
            [players_ts[player] for player in team2]
        )
        matches.append((
            abs(0.50 - win_prob),
            win_prob,
            team1,
            team2
        ))

    #print (random.choice([teams for teams in matches if ((teams[0] > 0.45) & (teams[0] < 0.55))]))
    even_games = [teams for teams in matches if ((teams[1] > 0.45) & (teams[1] < 0.55))]
    if not even_games:
        return (0, ['No Even Teams'], ['Try New Captains'])
    elif len(even_games) == 1:
        return even_games[0]
    else:
        heapq.heapify(even_games)
        return heapq.heappop(even_games)[1:4]
    #return pd.DataFrame([teams for teams in matches if ((teams[0] > 0.45) & (teams[0] < 0.55))], columns = ['Win Probability', 'Team 1', 'Team 2']).sort_values(by = 'Win Probability', ascending = True)

#player_list = ['sharp|laptop', 'Stork', 'rtcll', 'lel', 'Lyon', 'Fooshiez', 'Noosh', 'MaL', 'Navox', 'hojo420']
player_list = ['humps', 'cl0wn', 'r😬ven', 'a z r a e l 💸', 'Krayvok', 'hyperlite', 'lel', 'lotus 💥', 'bgbggr9']#, '[KoV]LordKermit']
#make_teams(player_list)

