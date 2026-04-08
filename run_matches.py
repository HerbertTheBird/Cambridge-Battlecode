import os
import subprocess
import pandas as pd
import re
from time import sleep

TEAM = "Blue Dragon"


# -------------------------
# SAFE COMMAND RUNNER (macOS)
# -------------------------
def run_cmd(command, env):
    if isinstance(command, str):
        command = command.split()

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env
    )

    return result.stdout


# -------------------------
# PARSERS
# -------------------------
def parse_ascii_table(text):
    rows = []
    lines = text.splitlines()

    for line in lines:
        line = line.strip()

        if not line.startswith("|"):
            continue

        # skip borders
        if set(line.replace("|", "").strip()) <= {"-", "+"}:
            continue

        parts = [p.strip() for p in line.split("|")]

        if len(parts) < 7:
            continue

        if parts[1] == "#":
            continue

        try:
            row = {
                "rank": int(parts[1]),
                "team": parts[2],
                "rating": int(parts[3]),
                "matches": int(parts[4]),
                "category": parts[5],
                "region": parts[6],
            }
            rows.append(row)
        except:
            continue

    return rows


def parse_ascii_tableid(text):
    rows = []

    for line in text.splitlines():
        line = line.strip()

        if line.startswith("+") or line.startswith("|-") or "Team ID" in line:
            continue

        if not line.startswith("|"):
            continue

        parts = [p.strip() for p in line.split("|")]

        # remove empty edges
        parts = [p for p in parts if p]

        if len(parts) != 6:
            continue

        try:
            rows.append({
                "team_id": parts[0],
                "name": parts[1],
                "category": parts[2],
                "rating": int(parts[3]),
                "matches": int(parts[4]),
                "region": parts[5],
            })
        except:
            continue

    return rows


# -------------------------
# ENV (macOS-safe)
# -------------------------
env = os.environ.copy()
env["COLUMNS"] = "1000"
env["LINES"] = "100000"


# -------------------------
# LOAD LADDER
# -------------------------
command = ["cambc", "ladder", "--limit", "10"]
output = run_cmd(command, env)

teas = parse_ascii_table(output)

teams = []
ranks = []
team_id = []

for t in teas:
    teams.append(t["team"])
    ranks.append(t["rank"])

    cmds = ["cambc", "team", "search", teams[-1]]
    output2 = run_cmd(cmds, env)

    parsed = parse_ascii_tableid(output2)
    if parsed:
        team_id.append(parsed[0]["team_id"])
    else:
        team_id.append(None)


# -------------------------
# MAIN LOOP
# -------------------------
for i in range(1000):

    for rank, team, tid in zip(ranks, teams, team_id):
        print(rank, team, tid)

        if tid is None:
            continue

        if (
            (rank < 5 and i % 3 == 0) or
            (rank < 10 and 5 <= rank < 10 and i % 3 == 1) or
            (rank < 15 and 10 <= rank < 15 and i % 3 == 2)
        ):
            command = ["cambc", "match", "unrated", tid]
            output = run_cmd(command, env)
            print(output)
