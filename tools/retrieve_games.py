import os
import subprocess
import pandas as pd
import re

TEAM = "Citadel"


# -------------------------
# COMMAND RUNNER (macOS-safe)
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

    if "list" in command:
        print("Err:", result.stderr)

    return result.stdout


# -------------------------
# PARSERS
# -------------------------
def parse_matches(text):
    matches = []

    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        if set(line.strip()) <= set("|-+"):
            continue

        parts = [p.strip() for p in line.strip("|").split("|")]

        if len(parts) < 7 or parts[0] == "Match ID":
            continue

        score_a, score_b = -1, -1
        if "-" in parts[3]:
            try:
                score_a, score_b = map(int, parts[3].split("-"))
            except:
                pass

        matches.append({
            "match_id": parts[0],
            "team_a": parts[2],
            "team_b": parts[4],
            "score_a": score_a,
            "score_b": score_b,
            "date": parts[6],
        })

    return matches


def parse_game_results(text):
    results = []

    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        if set(line.strip()) <= set("|-+"):
            continue

        parts = [p.strip() for p in line.strip("|").split("|")]

        if len(parts) < 5 or parts[0] == "#":
            continue

        results.append({
            "map": parts[1],
            "winner": parts[2],
            "outcome": parts[3],
            "turns": parts[4],
        })

    return results


# -------------------------
# ENV (macOS-safe)
# -------------------------
env = os.environ.copy()
env["COLUMNS"] = "1000"
env["LINES"] = "100000"
env["RICH_NO_COLOR"] = "1"
env["PYTHONIOENCODING"] = "utf-8"


# -------------------------
# MAIN LOOP
# -------------------------
Games = []
Pagination = None

for _ in range(2):
    command = ["cambc", "match", "list", "--team", TEAM, "--limit", "100"]

    if Pagination:
        command += ["--cursor", Pagination]

    print("Running:", command)

    output = run_cmd(command, env)

    # macOS-safe cursor parsing
    match = re.search(r"--cursor\s+([^\s]+)", output)
    Pagination = match.group(1) if match else None

    print("Next cursor:", Pagination)

    matches = parse_matches(output)
    print("Matches found:", len(matches))

    for m in matches:
        if TEAM not in (m["team_a"], m["team_b"]):
            continue

        result2 = run_cmd(["cambc", "match", "info", m["match_id"]], env)

        for o in parse_game_results(result2):
            print(o)

            weWon = TEAM in o["winner"]
            otherTeam = m["team_b"] if m["team_a"] == TEAM else m["team_a"]

            Games.append((
                o["map"],
                "A" if m["team_a"] == TEAM else "B",
                1 if weWon else 0,
                otherTeam,
                m["date"],
                o["outcome"],
                o["turns"]
            ))

    if not Pagination:
        break


# -------------------------
# DATAFRAME OUTPUT
# -------------------------
if not Games:
    print("No games found")
    exit()

maps, sides, wins, enemies, dates, outcome, turns = zip(*Games)

df = pd.DataFrame({
    "maps": maps,
    "OurTeamsSide": sides,
    "Victory": wins,
    "enemyTeam": enemies,
    "date": dates,
    "outcome": outcome,
    "turns": turns
})

df.to_csv("CitadelGames.csv", index=False)
print("Saved to CitadelGames.csv")