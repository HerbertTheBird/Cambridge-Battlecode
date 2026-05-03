#!/usr/bin/env python3
"""
Match Tracker — pull match history & ladder data from the cambc API.

Provides: win rate analysis, opponent scouting, rating trends,
and match history search. Uses the same auth as `cambc` CLI.

Usage:
    python match_tracker.py status              # show team status + rating
    python match_tracker.py history              # recent match history
    python match_tracker.py history --limit 50   # more matches
    python match_tracker.py winrate              # win rate breakdown
    python match_tracker.py scout TEAM_NAME      # scout an opponent
    python match_tracker.py ladder               # show ladder around you
    python match_tracker.py ladder --top 20      # show top 20
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


# ── API helpers (standalone, no cambc import needed) ─────────────────────────

CREDENTIALS_FILE = Path.home() / ".cambc" / "credentials.json"
DEFAULT_API_URL = "https://game.battlecode.cam"


def _get_api_url() -> str:
    return os.environ.get("CAMBC_API_URL", DEFAULT_API_URL)


def _get_credentials() -> dict | None:
    if not CREDENTIALS_FILE.exists():
        return None
    try:
        return json.loads(CREDENTIALS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _get_token() -> str:
    creds = _get_credentials()
    if not creds or "token" not in creds:
        print("Not logged in. Run: cambc login", file=sys.stderr)
        sys.exit(1)
    return creds["token"]


def _get_team_id() -> str | None:
    creds = _get_credentials()
    if creds and creds.get("team"):
        return creds["team"].get("id")
    return None


def _get_team_name() -> str | None:
    creds = _get_credentials()
    if creds and creds.get("team"):
        return creds["team"].get("name")
    return None


def api_get(path: str, params: dict[str, str] | None = None) -> dict | list:
    from urllib.parse import urlencode
    token = _get_token()
    url = f"{_get_api_url()}{path}"
    if params:
        url += f"?{urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print("Session expired. Run: cambc login", file=sys.stderr)
        else:
            body = e.read().decode(errors="replace")
            print(f"API error {e.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)


# ── Data fetching ────────────────────────────────────────────────────────────

def fetch_matches(limit: int = 20, match_type: str | None = None) -> list[dict]:
    """Fetch recent matches for the current team."""
    team_id = _get_team_id()
    if not team_id:
        print("No team configured. Run: cambc login", file=sys.stderr)
        sys.exit(1)

    params: dict[str, str] = {"limit": str(limit), "teamIds": team_id}
    if match_type:
        params["type"] = match_type

    data = api_get("/api/matches", params)
    return data.get("matches", []) if isinstance(data, dict) else []


def fetch_match_detail(match_id: str) -> dict:
    return api_get(f"/api/matches/{match_id}")


def fetch_ladder(limit: int = 20, around: bool = False) -> list[dict]:
    params: dict[str, str] = {"limit": str(limit)}
    if around:
        team_id = _get_team_id()
        if team_id:
            params["around"] = team_id
    data = api_get("/api/ladder", params)
    if isinstance(data, list):
        return data
    return data.get("rankings", data.get("ladder", []))


def search_team(query: str) -> list[dict]:
    data = api_get("/api/teams/search", {"q": query})
    return data.get("teams", []) if isinstance(data, dict) else []


def fetch_team_info(team_id: str) -> dict:
    return api_get(f"/api/teams/{team_id}")


def fetch_team_matches(team_id: str, limit: int = 20) -> list[dict]:
    params = {"limit": str(limit), "teamIds": team_id}
    data = api_get("/api/matches", params)
    return data.get("matches", []) if isinstance(data, dict) else []


# ── Display helpers ──────────────────────────────────────────────────────────

def _time_ago(iso_str: str | None) -> str:
    if not iso_str:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except (ValueError, TypeError):
        return "?"


def _result_for_team(match: dict, team_id: str) -> str:
    winner = match.get("winnerId")
    if not winner:
        status = match.get("status", "")
        if status == "error":
            return "ERR"
        return "---"
    if winner == team_id:
        return "WIN"
    return "LOSS"


def _opponent_name(match: dict, team_id: str) -> str:
    if match.get("teamAId") == team_id:
        return match.get("teamBName", "?")
    return match.get("teamAName", "?")


def _elo_delta(match: dict, team_id: str) -> str:
    if match.get("teamAId") == team_id:
        delta = match.get("eloDeltaA")
    else:
        delta = match.get("eloDeltaB")
    if delta is None:
        return ""
    return f"+{delta}" if delta >= 0 else str(delta)


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_status() -> None:
    team_id = _get_team_id()
    team_name = _get_team_name()
    if not team_id:
        print("No team configured.")
        return

    print(f"\n  Team: {team_name} (id: {team_id})")

    try:
        info = fetch_team_info(team_id)
        team = info.get("team", {})
        rating = info.get("rating", {})
        members = info.get("members", [])

        print(f"  Category: {team.get('category', '?')}")
        print(f"  Region: {info.get('region', '?')}")
        print(f"  Rating: {rating.get('rating', '?')}")
        print(f"  Matches: {rating.get('matchesPlayed', '?')}")
        if members:
            names = ", ".join(m.get("userName", "?") for m in members)
            print(f"  Members: {names}")
    except Exception:
        print("  (Could not fetch team details)")

    # Get recent ladder position
    try:
        ladder = fetch_ladder(limit=5, around=True)
        for entry in ladder:
            marker = " <--" if str(entry.get("teamId")) == team_id else ""
            print(f"  #{entry.get('_rank', '?'):>4}  {entry.get('teamName', '?'):20s}  "
                  f"rating={entry.get('rating', '?')}{marker}")
    except Exception:
        pass


def cmd_history(limit: int = 20, match_type: str | None = None) -> None:
    team_id = _get_team_id()
    if not team_id:
        print("No team configured.")
        return

    matches = fetch_matches(limit=limit, match_type=match_type)
    if not matches:
        print("No matches found.")
        return

    print(f"\n  --- Match History (last {len(matches)}) ---")
    print(f"  {'Result':>6}  {'ELO':>6}  {'Score':>5}  {'Opponent':20s}  {'When':>8}  {'Type':>8}")
    print(f"  {'------':>6}  {'---':>6}  {'-----':>5}  {'--------':20s}  {'----':>8}  {'----':>8}")

    for m in matches:
        result = _result_for_team(m, team_id)
        elo = _elo_delta(m, team_id)
        opp = _opponent_name(m, team_id)
        score = f"{m.get('scoreA', '?')}-{m.get('scoreB', '?')}"
        when = _time_ago(m.get("completedAt") or m.get("createdAt"))
        mtype = "rated" if m.get("rated") else "unrated"

        print(f"  {result:>6}  {elo:>6}  {score:>5}  {opp:20s}  {when:>8}  {mtype:>8}")


def cmd_winrate(limit: int = 50) -> None:
    team_id = _get_team_id()
    if not team_id:
        print("No team configured.")
        return

    matches = fetch_matches(limit=limit)
    if not matches:
        print("No matches found.")
        return

    wins = losses = draws = errors = 0
    opponent_stats: dict[str, dict[str, int]] = {}
    elo_history: list[tuple[str, int | None]] = []

    for m in matches:
        result = _result_for_team(m, team_id)
        opp = _opponent_name(m, team_id)

        if opp not in opponent_stats:
            opponent_stats[opp] = {"wins": 0, "losses": 0}

        if result == "WIN":
            wins += 1
            opponent_stats[opp]["wins"] += 1
        elif result == "LOSS":
            losses += 1
            opponent_stats[opp]["losses"] += 1
        elif result == "ERR":
            errors += 1
        else:
            draws += 1

        if m.get("teamAId") == team_id:
            elo_history.append((m.get("completedAt", ""), m.get("eloDeltaA")))
        else:
            elo_history.append((m.get("completedAt", ""), m.get("eloDeltaB")))

    total = wins + losses + draws
    wr = wins / total * 100 if total else 0

    print(f"\n  --- Win Rate (last {len(matches)} matches) ---")
    print(f"  Wins: {wins}  Losses: {losses}  Draws: {draws}  Errors: {errors}")
    print(f"  Win Rate: {wr:.1f}%")

    # ELO trend
    elo_deltas = [d for _, d in elo_history if d is not None]
    if elo_deltas:
        net = sum(elo_deltas)
        print(f"  Net ELO change: {'+' if net >= 0 else ''}{net}")

    # Opponent breakdown (sorted by most games)
    sorted_opps = sorted(opponent_stats.items(), key=lambda x: -(x[1]["wins"] + x[1]["losses"]))
    print(f"\n  --- Opponent Breakdown ---")
    print(f"  {'Opponent':25s}  {'W':>3}  {'L':>3}  {'WR':>5}")
    print(f"  {'-' * 25}  ---  ---  -----")
    for opp, s in sorted_opps[:15]:
        opp_total = s["wins"] + s["losses"]
        opp_wr = s["wins"] / opp_total * 100 if opp_total else 0
        print(f"  {opp:25s}  {s['wins']:>3}  {s['losses']:>3}  {opp_wr:>4.0f}%")


def cmd_scout(query: str) -> None:
    """Scout an opponent team."""
    teams = search_team(query)
    if not teams:
        print(f"No teams found matching '{query}'.")
        return

    # Pick best match
    target = teams[0]
    target_id = str(target.get("teamId", target.get("id", "")))
    target_name = target.get("teamName", target.get("name", "?"))

    print(f"\n  --- Scouting: {target_name} ---")
    print(f"  Team ID: {target_id}")
    print(f"  Category: {target.get('category', '?')}")
    print(f"  Rating: {target.get('rating', '?')}")
    print(f"  Matches: {target.get('matchesPlayed', '?')}")

    # Get their recent matches
    try:
        matches = fetch_team_matches(target_id, limit=20)
        if matches:
            wins = sum(1 for m in matches if m.get("winnerId") == target_id)
            losses = len(matches) - wins
            wr = wins / len(matches) * 100 if matches else 0
            print(f"\n  Recent form ({len(matches)} matches): {wins}W {losses}L ({wr:.0f}%)")

            # Show results
            print(f"\n  {'Result':>6}  {'Score':>5}  {'Opponent':25s}  {'When':>8}")
            print(f"  {'------':>6}  {'-----':>5}  {'--------':25s}  {'----':>8}")
            for m in matches[:10]:
                result = "WIN" if m.get("winnerId") == target_id else "LOSS"
                score = f"{m.get('scoreA', '?')}-{m.get('scoreB', '?')}"
                if m.get("teamAId") == target_id:
                    opp = m.get("teamBName", "?")
                else:
                    opp = m.get("teamAName", "?")
                when = _time_ago(m.get("completedAt") or m.get("createdAt"))
                print(f"  {result:>6}  {score:>5}  {opp:25s}  {when:>8}")
    except Exception as e:
        print(f"  Could not fetch match history: {e}")


def cmd_ladder(top: int | None = None, around: bool = True) -> None:
    if top:
        ladder = fetch_ladder(limit=top, around=False)
    else:
        ladder = fetch_ladder(limit=10, around=around)

    if not ladder:
        print("No ladder data.")
        return

    team_id = _get_team_id()

    # Filter to teams with rating > 0 and matches > 0 if no explicit top
    if not top:
        ladder = [e for e in ladder if e.get("rating", 0) > 0 and e.get("matchesPlayed", 0) > 0]

    # Sort by rating descending
    ladder.sort(key=lambda e: -(e.get("rating") or 0))

    # Limit output
    limit = top or 15
    ladder = ladder[:limit]

    print(f"\n  --- Ladder ---")
    print(f"  {'#':>4}  {'Team':25s}  {'Rating':>8}  {'Matches':>8}")
    print(f"  {'--':>4}  {'----':25s}  {'------':>8}  {'-------':>8}")
    for i, entry in enumerate(ladder, 1):
        rank = entry.get("_rank", i)
        marker = " <--" if str(entry.get("teamId")) == team_id else ""
        rating = entry.get("rating", 0)
        print(f"  {rank:>4}  {entry.get('teamName', '?'):25s}  "
              f"{rating:>8.0f}  {entry.get('matchesPlayed', '?'):>8}{marker}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Track matches and ladder for Cambridge Battlecode.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="Show team status and rating.")

    hist_parser = subparsers.add_parser("history", help="Show recent match history.")
    hist_parser.add_argument("--limit", type=int, default=20, help="Number of matches to show.")
    hist_parser.add_argument("--type", choices=["ladder", "unrated"], dest="match_type", help="Filter by match type.")

    wr_parser = subparsers.add_parser("winrate", help="Win rate analysis.")
    wr_parser.add_argument("--limit", type=int, default=100, help="Number of matches to analyze.")

    scout_parser = subparsers.add_parser("scout", help="Scout an opponent team.")
    scout_parser.add_argument("team", help="Team name to search for.")

    ladder_parser = subparsers.add_parser("ladder", help="Show ladder standings.")
    ladder_parser.add_argument("--top", type=int, help="Show top N teams.")

    args = parser.parse_args()

    if args.command == "status":
        cmd_status()
    elif args.command == "history":
        cmd_history(limit=args.limit, match_type=args.match_type)
    elif args.command == "winrate":
        cmd_winrate(limit=args.limit)
    elif args.command == "scout":
        cmd_scout(args.team)
    elif args.command == "ladder":
        cmd_ladder(top=args.top)
    else:
        parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
