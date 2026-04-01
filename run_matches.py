import os
import subprocess
import pandas as pd
import re
from time import sleep

TEAM = "Blue Dragon"

def run_cmd(command, env):
    """Run cambc safely with UTF-8 and no Rich issues"""
    cmd = "chcp 65001 >nul && " + subprocess.list2cmdline(command)
    result = subprocess.run(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        env=env
    )

    return result.stdout

import re
def parse_ascii_table(text):
    rows = []
    lines = text.splitlines()
    
    for line in lines:
        line = line.strip()
        
        # Only process lines that start with "| " and are not the header separator
        if line.startswith("|") and not set(line[1:-1]).issubset({"-", "+"}):
            # Split by | and strip spaces
            parts = [p.strip() for p in line.split("|")]
            
            # There will be empty strings at start/end due to leading/trailing |
            if len(parts) < 7:
                continue
            
            # Skip the header row
            if parts[1] == "#":
                continue
            
            # Parse row
            row = {
                "rank": int(parts[1]),
                "team": parts[2],
                "rating": int(parts[3]),
                "matches": int(parts[4]),
                "category": parts[5],
                "region": parts[6],
            }
            rows.append(row)
    
    return rows

def parse_ascii_tableid(text):
    rows = []

    for line in text.splitlines():
        line = line.strip()
        # Skip border lines and header row
        if line.startswith("+") or line.startswith("|-") or "Team ID" in line:
            continue

        if line.startswith("|"):
            # Split on | and strip spaces
            parts = [p.strip() for p in line.split("|")]

            # Remove empty strings from leading/trailing pipes
            parts = [p for p in parts if p]

            if len(parts) != 6:
                continue  # skip malformed rows

            row = {
                "team_id": parts[0],
                "name": parts[1],
                "category": parts[2],
                "rating": int(parts[3]),
                "matches": int(parts[4]),
                "region": parts[5],
            }
            rows.append(row)

    return rows



command = ["cambc", "ladder", "--limit", "10"]
env = os.environ.copy()
env["COLUMNS"] = "1000"
env["LINES"] = "100000"


output = run_cmd(command, env)
teas=parse_ascii_table(output)
teams=[]
ranks=[]
team_id=[]
for t in teas:
    teams.append(t["team"])
    ranks.append(t["rank"])
    cmds=["cambc","team","search",teams[-1]]
    output2 = run_cmd(cmds, env)
    team_id.append(parse_ascii_tableid(output2)[0]["team_id"])

for i in range(1000):
    
    for rank,team,tid in zip (ranks,teams,team_id):
        print(rank,team,tid)

        if((rank<5 and i%3==0) or
           (rank<10 and rank>=5 and i%3==1) or
            (rank<15 and rank>=10 and i%3==2)

           ):
            command = ["cambc", "match", "unrated", tid]
            output = run_cmd(command, env)
            print(output)
    sleep(60*8)