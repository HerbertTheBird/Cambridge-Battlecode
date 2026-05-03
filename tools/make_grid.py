import pandas as pd
import time 

df=pd.read_csv("CitadelGames.csv")

df["date"] = pd.to_datetime(df["date"]).dt.tz_localize("UTC")

now = pd.Timestamp.now(tz="UTC")
df["hours_ago"] = (now - df["date"]).dt.total_seconds() / 3600

df=df[df["hours_ago"]<15]
print(len(df["hours_ago"]))
maps=set(df["maps"])
teams=set(df["enemyTeam"])
sides=["A","B"]
print(len("teams"))


# Dictionary to hold the per-team-side series
team_side_winfrac = {}
team_games={}
"""
for team in teams:
    for side in sides:
        # Filter for this team and side
        sub = df[(df["enemyTeam"] == team) & (df["OurTeamsSide"] == side)]
        if not sub.empty:
            win_frac = sub.groupby("maps")["Victory"].mean()
        else:
            win_frac = pd.Series(dtype=float)
        win_frac = win_frac.reindex(maps)
        team_side_winfrac[f"{team}_{side}"] = win_frac
"""
for team in teams:
    sub = df[(df["enemyTeam"] == team) ]
    if not sub.empty:
        win_frac = sub.groupby("maps")["Victory"].mean()
    else:
        win_frac = pd.Series(dtype=float)
    win_frac = win_frac.reindex(maps)
    team_side_winfrac[f"{team}"] = win_frac
# Combine all series into one dataframe
summary_df = pd.DataFrame(team_side_winfrac)

for team in teams:
    sub = df[(df["enemyTeam"] == team) ]
    if not sub.empty:
        win_frac = sub.groupby("maps")["Victory"].sum()
    else:
        win_frac = pd.Series(dtype=float)
    win_frac = win_frac.reindex(maps)
    team_games[f"{team}"] = win_frac
# Combine all series into one dataframe
summary_df_games = pd.DataFrame(team_games)


print(summary_df)
summary_df.to_csv("Win.csv")
summary_df_games.to_csv("Games.csv")