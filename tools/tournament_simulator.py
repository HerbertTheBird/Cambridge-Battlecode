#!/usr/bin/env python3
"""
Double-elimination BO9 tournament simulator with Monte Carlo.

Uses the cambc ladder API for ELO ratings and match history for head-to-head
win rates. Runs 100k simulations to estimate placement probabilities.

Usage:
    python tools/tournament_simulator.py
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

# ── Configuration ───────────────────────────────────────────────────────────

# 16 teams in the tournament — CHANGE THESE to match your bracket
TOURNAMENT_TEAMS = [
    "Oxford",
    "something else",
    "Kessoku Band",
    "bwaaa",
    "muteki",
    "MFF1",
    "Beehive",
    "Grandmaster Oogway",
    "test",
    "randomusergroup",
    "Tootill Labs",
    "The Cambridge Edge",
    "anime girls against period cramp",
    "Mr Worldwide",
    "Axionite Allergic Individuals",
    "Silver Street Capital"
]

NUM_SIMULATIONS = 1_000_000
BO_LENGTH = 9  # best-of-9, first to 5 wins
WINS_NEEDED = (BO_LENGTH + 1) // 2  # 5
MISSING_WINRATE = 0.95  # per-game win prob for higher-rated team when no h2h data

# ── API helpers ─────────────────────────────────────────────────────────────

CREDENTIALS_FILE = Path.home() / ".cambc" / "credentials.json"
DEFAULT_API_URL = "https://game.battlecode.cam"


def _get_api_url() -> str:
    return os.environ.get("CAMBC_API_URL", DEFAULT_API_URL)


def _get_token() -> str:
    if not CREDENTIALS_FILE.exists():
        print("Not logged in. Run: cambc login", file=sys.stderr)
        sys.exit(1)
    creds = json.loads(CREDENTIALS_FILE.read_text())
    if "token" not in creds:
        print("Not logged in. Run: cambc login", file=sys.stderr)
        sys.exit(1)
    return creds["token"]


def api_get(path: str, params: dict[str, str] | None = None) -> dict | list:
    token = _get_token()
    url = f"{_get_api_url()}{path}"
    if params:
        url += f"?{urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}"}, method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"API error {e.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)


# ── Data fetching ───────────────────────────────────────────────────────────


def fetch_ladder_ratings() -> dict[str, float]:
    """Fetch ELO ratings for all teams from the ladder."""
    data = api_get("/api/ladder", {"limit": "200"})
    entries = data if isinstance(data, list) else data.get("rankings", data.get("ladder", []))
    ratings: dict[str, float] = {}
    for entry in entries:
        name = entry.get("teamName", "")
        rating = entry.get("rating", 0)
        if name and rating > 0:
            ratings[name] = rating
    return ratings


def fetch_team_id_map() -> dict[str, str]:
    """Fetch team name -> team ID mapping from the ladder."""
    data = api_get("/api/ladder", {"limit": "200"})
    entries = data if isinstance(data, list) else data.get("rankings", data.get("ladder", []))
    mapping: dict[str, str] = {}
    for entry in entries:
        name = entry.get("teamName", "")
        tid = entry.get("teamId", "")
        if name and tid:
            mapping[name] = tid
    return mapping


def fetch_team_matches(team_id: str, limit: int = 100) -> list[dict]:
    """Fetch recent matches for a team."""
    params = {"limit": str(limit), "teamIds": team_id}
    data = api_get("/api/matches", params)
    return data.get("matches", []) if isinstance(data, dict) else []


# ── Win probability computation ─────────────────────────────────────────────


def bo_win_probability(p: float, wins_needed: int = WINS_NEEDED) -> float:
    """
    Probability of winning a best-of-(2*wins_needed - 1) series
    given per-game win probability p.
    """
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    q = 1.0 - p
    total = 0.0
    for losses in range(wins_needed):
        # C(wins_needed - 1 + losses, losses) * p^wins_needed * q^losses
        coeff = math.comb(wins_needed - 1 + losses, losses)
        total += coeff * (p ** wins_needed) * (q ** losses)
    return total


def elo_expected_score(rating_a: float, rating_b: float) -> float:
    """Standard logistic expected score from ELO ratings."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def build_h2h_matrix(
    team_names: list[str],
    team_ids: dict[str, str],
) -> dict[tuple[str, str], tuple[int, int]]:
    """
    Build head-to-head game wins matrix from recent match history.
    Returns {(teamA, teamB): (games_won_by_A, games_won_by_B)}.
    """
    # Fetch matches for each team
    all_matches: dict[str, list[dict]] = {}
    tournament_ids = {team_ids[name] for name in team_names if name in team_ids}
    id_to_name = {v: k for k, v in team_ids.items() if k in team_names}

    print("Fetching match history for each team...")
    for name in team_names:
        tid = team_ids.get(name)
        if not tid:
            print(f"  WARNING: Team '{name}' not found on ladder, skipping h2h")
            continue
        matches = fetch_team_matches(tid, limit=100)
        all_matches[name] = matches
        print(f"  {name}: {len(matches)} recent matches")

    # Cross-reference to find h2h results
    h2h: dict[tuple[str, str], tuple[int, int]] = {}
    seen_match_ids: set[str] = set()

    for name, matches in all_matches.items():
        tid = team_ids[name]
        for m in matches:
            match_id = m.get("id", m.get("matchId", ""))
            if match_id in seen_match_ids:
                continue

            team_a_id = m.get("teamAId", "")
            team_b_id = m.get("teamBId", "")

            # Only care about matches between two tournament teams
            if team_a_id not in tournament_ids or team_b_id not in tournament_ids:
                continue

            name_a = id_to_name.get(team_a_id)
            name_b = id_to_name.get(team_b_id)
            if not name_a or not name_b:
                continue

            seen_match_ids.add(match_id)

            score_a = m.get("scoreA", 0) or 0
            score_b = m.get("scoreB", 0) or 0

            # Accumulate per-game wins
            key = tuple(sorted([name_a, name_b]))
            if key not in h2h:
                h2h[key] = (0, 0)

            prev_a, prev_b = h2h[key]
            if key[0] == name_a:
                h2h[key] = (prev_a + score_a, prev_b + score_b)
            else:
                h2h[key] = (prev_a + score_b, prev_b + score_a)

    return h2h


def build_win_prob_matrix(
    team_names: list[str],
    ratings: dict[str, float],
    h2h: dict[tuple[str, str], tuple[int, int]],
) -> dict[tuple[str, str], float]:
    """
    Build BO9 win probability matrix.
    Uses h2h data if available, otherwise falls back to ELO.
    Returns {(teamA, teamB): P(A wins BO9)}.
    """
    win_probs: dict[tuple[str, str], float] = {}

    for i, a in enumerate(team_names):
        for j, b in enumerate(team_names):
            if i >= j:
                continue

            key = tuple(sorted([a, b]))
            h2h_data = h2h.get(key)

            if h2h_data and sum(h2h_data) >= 3:
                # Use h2h per-game win rate
                if key[0] == a:
                    games_a, games_b = h2h_data
                else:
                    games_b, games_a = h2h_data
                total = games_a + games_b
                p_game = min(max(games_a / total, 1.0 - MISSING_WINRATE), MISSING_WINRATE)
                p_bo9 = bo_win_probability(p_game)
                source = f"h2h ({games_a}-{games_b} games)"
            else:
                # No h2h data — use MISSING_WINRATE as per-game prob for higher-rated team
                rating_a = ratings.get(a, 1500)
                rating_b = ratings.get(b, 1500)
                if rating_a >= rating_b:
                    p_bo9 = bo_win_probability(MISSING_WINRATE)
                else:
                    p_bo9 = 1.0 - bo_win_probability(MISSING_WINRATE)

            win_probs[(a, b)] = p_bo9
            win_probs[(b, a)] = 1.0 - p_bo9

    return win_probs


# ── Double elimination tournament ───────────────────────────────────────────


def standard_seeding(n: int) -> list[tuple[int, int]]:
    """
    Standard tournament seeding for n teams (power of 2).
    Returns list of (seed_a, seed_b) matchups for round 1.
    Seeds are 0-indexed.
    """
    if n == 2:
        return [(0, 1)]
    half = standard_seeding(n // 2)
    return [(a, n - 1 - a) if i % 2 == 0 else (n - 1 - a, a)
            for i, (a, b) in enumerate(half)] + \
           [(b, n - 1 - b) if i % 2 == 0 else (n - 1 - b, b)
            for i, (a, b) in enumerate(half)]


def get_seeded_matchups(n: int) -> list[tuple[int, int]]:
    """Standard bracket seeding: 1v16, 8v9, 5v12, 4v13, 6v11, 3v14, 7v10, 2v15."""
    # Standard seeding order for 16 teams
    if n == 16:
        return [
            (0, 15), (7, 8), (4, 11), (3, 12),
            (5, 10), (2, 13), (6, 9), (1, 14),
        ]
    # Fallback: simple 1vN, 2v(N-1), etc.
    return [(i, n - 1 - i) for i in range(n // 2)]


def simulate_match(
    team_a: str,
    team_b: str,
    win_probs: dict[tuple[str, str], float],
    rng: random.Random,
) -> str:
    """Simulate a single BO9 match, return winner."""
    p = win_probs.get((team_a, team_b), 0.5)
    if rng.random() < p:
        return team_a
    return team_b


def _get_loser(team_a: str, team_b: str, winner: str) -> str:
    return team_b if winner == team_a else team_a


# Match labels for bracket tracking (in order)
MATCH_LABELS = [
    # Upper bracket R1 (8 matches)
    "UB-R1-1", "UB-R1-2", "UB-R1-3", "UB-R1-4",
    "UB-R1-5", "UB-R1-6", "UB-R1-7", "UB-R1-8",
    # Upper bracket QF (4)
    "UB-QF-1", "UB-QF-2", "UB-QF-3", "UB-QF-4",
    # Upper bracket SF (2)
    "UB-SF-1", "UB-SF-2",
    # Upper bracket Final (1)
    "UB-Final",
    # Lower bracket R1 (4)
    "LB-R1-1", "LB-R1-2", "LB-R1-3", "LB-R1-4",
    # Lower bracket R2 (4)
    "LB-R2-1", "LB-R2-2", "LB-R2-3", "LB-R2-4",
    # Lower bracket R3 (2)
    "LB-R3-1", "LB-R3-2",
    # Lower bracket R4 (2)
    "LB-R4-1", "LB-R4-2",
    # Lower bracket R5 (1)
    "LB-R5",
    # Lower bracket Final (1)
    "LB-Final",
    # Grand Final (1) + possible reset (1)
    "Grand-Final",
    "GF-Reset",
]


def simulate_double_elim(
    teams_by_seed: list[str],
    win_probs: dict[tuple[str, str], float],
    rng: random.Random,
) -> tuple[dict[str, int], tuple[tuple[str, str, str], ...]]:
    """
    Simulate a 16-team double elimination bracket.
    Returns (placements, bracket_trace).
    bracket_trace is a tuple of (team_a, team_b, winner) for each match played.
    """
    n = len(teams_by_seed)
    placements: dict[str, int] = {}
    trace: list[tuple[str, str, str]] = []

    def play(a: str, b: str) -> str:
        w = simulate_match(a, b, win_probs, rng)
        trace.append((a, b, w))
        return w

    # Upper bracket round 1
    ub_r1_matchups = get_seeded_matchups(n)
    ub_r1_winners = []
    lb_r1_entrants = []
    for a_idx, b_idx in ub_r1_matchups:
        a, b = teams_by_seed[a_idx], teams_by_seed[b_idx]
        winner = play(a, b)
        ub_r1_winners.append(winner)
        lb_r1_entrants.append(_get_loser(a, b, winner))

    # Upper bracket quarterfinals
    ub_qf_winners = []
    lb_r2_entrants = []
    for i in range(0, len(ub_r1_winners), 2):
        a, b = ub_r1_winners[i], ub_r1_winners[i + 1]
        winner = play(a, b)
        ub_qf_winners.append(winner)
        lb_r2_entrants.append(_get_loser(a, b, winner))

    # Upper bracket semifinals
    ub_sf_winners = []
    lb_r4_entrants = []
    for i in range(0, len(ub_qf_winners), 2):
        a, b = ub_qf_winners[i], ub_qf_winners[i + 1]
        winner = play(a, b)
        ub_sf_winners.append(winner)
        lb_r4_entrants.append(_get_loser(a, b, winner))

    # Upper bracket final
    a, b = ub_sf_winners[0], ub_sf_winners[1]
    ub_winner = play(a, b)
    ub_final_loser = _get_loser(a, b, ub_winner)

    # ── Lower bracket ──

    # LB Round 1
    lb_r1_winners = []
    for i in range(0, len(lb_r1_entrants), 2):
        a, b = lb_r1_entrants[i], lb_r1_entrants[i + 1]
        winner = play(a, b)
        placements[_get_loser(a, b, winner)] = 13
        lb_r1_winners.append(winner)

    # LB Round 2
    lb_r2_winners = []
    for i in range(4):
        a, b = lb_r1_winners[i], lb_r2_entrants[i]
        winner = play(a, b)
        placements[_get_loser(a, b, winner)] = 9
        lb_r2_winners.append(winner)

    # LB Round 3
    lb_r3_winners = []
    for i in range(0, len(lb_r2_winners), 2):
        a, b = lb_r2_winners[i], lb_r2_winners[i + 1]
        winner = play(a, b)
        placements[_get_loser(a, b, winner)] = 7
        lb_r3_winners.append(winner)

    # LB Round 4
    lb_r4_winners = []
    for i in range(2):
        a, b = lb_r3_winners[i], lb_r4_entrants[i]
        winner = play(a, b)
        placements[_get_loser(a, b, winner)] = 5
        lb_r4_winners.append(winner)

    # LB Round 5
    a, b = lb_r4_winners[0], lb_r4_winners[1]
    lb_r5_winner = play(a, b)
    placements[_get_loser(a, b, lb_r5_winner)] = 4

    # LB Final
    a, b = lb_r5_winner, ub_final_loser
    lb_finalist = play(a, b)
    placements[_get_loser(a, b, lb_finalist)] = 3

    # ── Grand Final ──
    a, b = ub_winner, lb_finalist
    gf_winner = play(a, b)
    if gf_winner == ub_winner:
        placements[ub_winner] = 1
        placements[lb_finalist] = 2
    else:
        # Bracket reset
        reset_winner = play(ub_winner, lb_finalist)
        if reset_winner == lb_finalist:
            placements[lb_finalist] = 1
            placements[ub_winner] = 2
        else:
            placements[ub_winner] = 1
            placements[lb_finalist] = 2

    return placements, tuple(trace)


# ── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    print("=" * 70)
    print("  DOUBLE ELIMINATION BO9 TOURNAMENT SIMULATOR")
    print("=" * 70)

    # Step 1: Fetch ratings and team IDs
    print("\nFetching ladder ratings...")
    ratings = fetch_ladder_ratings()
    team_ids = fetch_team_id_map()

    # Validate teams
    missing = [t for t in TOURNAMENT_TEAMS if t not in ratings]
    if missing:
        print(f"\nWARNING: Teams not found on ladder (will use default 1500 rating):")
        for t in missing:
            print(f"  - {t}")

    # Sort by rating for seeding
    teams_sorted = sorted(
        TOURNAMENT_TEAMS,
        key=lambda t: ratings.get(t, 1500),
        reverse=True,
    )

    print("\nSeeding (by ELO):")
    for i, team in enumerate(teams_sorted, 1):
        r = ratings.get(team, 1500)
        print(f"  #{i:>2}  {team:35s}  {r:>7.0f}")

    # Step 2: Build head-to-head matrix
    print()
    h2h = build_h2h_matrix(TOURNAMENT_TEAMS, team_ids)

    # Step 3: Build win probability matrix
    win_probs = build_win_prob_matrix(TOURNAMENT_TEAMS, ratings, h2h)

    # Print notable h2h matchups
    print("\nNotable head-to-head data found:")
    h2h_count = 0
    for (a, b), (ga, gb) in sorted(h2h.items(), key=lambda x: -(x[1][0] + x[1][1])):
        total = ga + gb
        if total >= 3:
            p = ga / total
            p_bo9 = bo_win_probability(p)
            print(f"  {a:25s} vs {b:25s}: {ga:>3}-{gb:<3} games  "
                  f"(BO9: {p_bo9:.1%} - {1 - p_bo9:.1%})")
            h2h_count += 1
    if h2h_count == 0:
        print("  (none with >= 3 games)")

    # Step 4: Run Monte Carlo simulations
    print(f"\nRunning {NUM_SIMULATIONS:,} simulations...")

    placement_counts: dict[str, dict[int, int]] = {
        team: defaultdict(int) for team in TOURNAMENT_TEAMS
    }
    win_counts: dict[str, int] = defaultdict(int)
    top3_counts: dict[str, int] = defaultdict(int)
    bracket_counts: dict[tuple[str, ...], int] = defaultdict(int)

    rng = random.Random(42)

    for sim in range(NUM_SIMULATIONS):
        if sim > 0 and sim % 250_000 == 0:
            print(f"  ... {sim:,} / {NUM_SIMULATIONS:,}")

        placements, trace = simulate_double_elim(teams_sorted, win_probs, rng)
        for team, place in placements.items():
            placement_counts[team][place] += 1
            if place == 1:
                win_counts[team] += 1
            if place <= 3:
                top3_counts[team] += 1

        # Track bracket by tuple of winners only
        bracket_key = tuple(w for _, _, w in trace)
        bracket_counts[bracket_key] += 1

    # Step 5: Display results
    print("\n" + "=" * 70)
    print("  RESULTS")
    print("=" * 70)

    # Sort by win probability
    sorted_teams = sorted(TOURNAMENT_TEAMS, key=lambda t: -win_counts.get(t, 0) * NUM_SIMULATIONS - ratings.get(t, 1500))

    print(f"\n{'Team':35s} {'Win%':>7} {'Top3':>7} {'1st':>7} {'2nd':>7} "
          f"{'3rd':>7} {'4th':>7} {'5-6th':>7} {'7-8th':>7} {'9-12th':>7} {'13-16th':>7}")
    print("-" * 120)

    for team in sorted_teams:
        counts = placement_counts[team]
        n = NUM_SIMULATIONS
        win_pct = win_counts.get(team, 0) / n * 100
        top3_pct = top3_counts.get(team, 0) / n * 100
        first = counts.get(1, 0) / n * 100
        second = counts.get(2, 0) / n * 100
        third = counts.get(3, 0) / n * 100
        fourth = counts.get(4, 0) / n * 100
        fifth = (counts.get(5, 0) + counts.get(6, 0)) / n * 100
        seventh = (counts.get(7, 0) + counts.get(8, 0)) / n * 100
        ninth = sum(counts.get(pg, 0) for pg in range(9, 13)) / n * 100
        thirteenth = sum(counts.get(pg, 0) for pg in range(13, 17)) / n * 100

        print(f"{team:35s} {win_pct:>6.1f}% {top3_pct:>6.1f}% {first:>6.1f}% {second:>6.1f}% "
              f"{third:>6.1f}% {fourth:>6.1f}% {fifth:>6.1f}% {seventh:>6.1f}% "
              f"{ninth:>6.1f}% {thirteenth:>6.1f}%")

    # Step 6: Find and draw the most common bracket
    print("\n" + "=" * 70)
    print("  MOST COMMON BRACKET")
    print("=" * 70)

    # Find the most common bracket
    best_bracket_key, best_bracket_count = max(bracket_counts.items(), key=lambda x: x[1])
    bracket_prob = best_bracket_count / NUM_SIMULATIONS * 100
    print(f"\n  Occurred {best_bracket_count:,} / {NUM_SIMULATIONS:,} times ({bracket_prob:.3f}%)")

    # Re-run the most common bracket deterministically to get full trace
    # We stored winners only, now replay to get matchups
    winner_iter = iter(best_bracket_key)
    bracket_matches: list[tuple[str, str, str, str, float]] = []  # (label, a, b, winner, prob)

    def replay_match(label: str, a: str, b: str) -> str:
        w = next(winner_iter)
        p = win_probs.get((w, b if w == a else a), 0.5)
        bracket_matches.append((label, a, b, w, p))
        return w

    # Replay upper bracket R1
    ub_r1_matchups = get_seeded_matchups(len(teams_sorted))
    ub_r1_winners = []
    lb_r1_entrants = []
    for idx, (a_idx, b_idx) in enumerate(ub_r1_matchups):
        a, b = teams_sorted[a_idx], teams_sorted[b_idx]
        w = replay_match(f"UB-R1-{idx+1}", a, b)
        ub_r1_winners.append(w)
        lb_r1_entrants.append(_get_loser(a, b, w))

    ub_qf_winners = []
    lb_r2_entrants = []
    for idx in range(4):
        a, b = ub_r1_winners[idx * 2], ub_r1_winners[idx * 2 + 1]
        w = replay_match(f"UB-QF-{idx+1}", a, b)
        ub_qf_winners.append(w)
        lb_r2_entrants.append(_get_loser(a, b, w))

    ub_sf_winners = []
    lb_r4_entrants = []
    for idx in range(2):
        a, b = ub_qf_winners[idx * 2], ub_qf_winners[idx * 2 + 1]
        w = replay_match(f"UB-SF-{idx+1}", a, b)
        ub_sf_winners.append(w)
        lb_r4_entrants.append(_get_loser(a, b, w))

    a, b = ub_sf_winners[0], ub_sf_winners[1]
    ub_winner = replay_match("UB-Final", a, b)
    ub_final_loser = _get_loser(a, b, ub_winner)

    lb_r1_winners = []
    for idx in range(4):
        a, b = lb_r1_entrants[idx * 2], lb_r1_entrants[idx * 2 + 1]
        w = replay_match(f"LB-R1-{idx+1}", a, b)
        lb_r1_winners.append(w)

    lb_r2_winners = []
    for idx in range(4):
        a, b = lb_r1_winners[idx], lb_r2_entrants[idx]
        w = replay_match(f"LB-R2-{idx+1}", a, b)
        lb_r2_winners.append(w)

    lb_r3_winners = []
    for idx in range(2):
        a, b = lb_r2_winners[idx * 2], lb_r2_winners[idx * 2 + 1]
        w = replay_match(f"LB-R3-{idx+1}", a, b)
        lb_r3_winners.append(w)

    lb_r4_winners = []
    for idx in range(2):
        a, b = lb_r3_winners[idx], lb_r4_entrants[idx]
        w = replay_match(f"LB-R4-{idx+1}", a, b)
        lb_r4_winners.append(w)

    a, b = lb_r4_winners[0], lb_r4_winners[1]
    lb_r5_winner = replay_match("LB-R5", a, b)

    a, b = lb_r5_winner, ub_final_loser
    lb_finalist = replay_match("LB-Final", a, b)

    a, b = ub_winner, lb_finalist
    gf_winner = replay_match("Grand-Final", a, b)
    has_reset = gf_winner != ub_winner
    if has_reset:
        replay_match("GF-Reset", ub_winner, lb_finalist)

    # Compute overall bracket probability (product of each match probability)
    total_bracket_prob = 1.0
    for _, _, _, _, p in bracket_matches:
        total_bracket_prob *= p

    # Draw the bracket
    W = 20  # column width for team names

    def short(name: str) -> str:
        return name[:W-1] if len(name) >= W else name

    def draw_section(title: str, matches: list[tuple[str, str, str, str, float]]) -> None:
        print(f"\n  ── {title} {'─' * (60 - len(title))}")
        for label, a, b, w, p in matches:
            marker_a = ">>>" if w == a else "   "
            marker_b = ">>>" if w == b else "   "
            p_a = p if w == a else 1 - p
            p_b = 1 - p_a
            print(f"  {label:14s}  {marker_a} {short(a):{W}s} ({p_a:5.1%})")
            print(f"  {'':<14s}  {marker_b} {short(b):{W}s} ({p_b:5.1%})")
            print()

    # Group matches by round
    ub_r1 = bracket_matches[0:8]
    ub_qf = bracket_matches[8:12]
    ub_sf = bracket_matches[12:14]
    ub_final = bracket_matches[14:15]
    lb_r1 = bracket_matches[15:19]
    lb_r2 = bracket_matches[19:23]
    lb_r3 = bracket_matches[23:25]
    lb_r4 = bracket_matches[25:27]
    lb_r5 = bracket_matches[27:28]
    lb_final = bracket_matches[28:29]
    gf = bracket_matches[29:]

    draw_section("UPPER BRACKET — Round 1", ub_r1)
    draw_section("UPPER BRACKET — Quarterfinals", ub_qf)
    draw_section("UPPER BRACKET — Semifinals", ub_sf)
    draw_section("UPPER BRACKET — Final", ub_final)
    draw_section("LOWER BRACKET — Round 1", lb_r1)
    draw_section("LOWER BRACKET — Round 2 (vs UB QF losers)", lb_r2)
    draw_section("LOWER BRACKET — Round 3", lb_r3)
    draw_section("LOWER BRACKET — Round 4 (vs UB SF losers)", lb_r4)
    draw_section("LOWER BRACKET — Round 5", lb_r5)
    draw_section("LOWER BRACKET — Final (vs UB Final loser)", lb_final)
    draw_section("GRAND FINAL" + (" + RESET" if has_reset else ""), gf)

    # Final champion
    champion = bracket_matches[-1][3]
    print(f"  {'=' * 60}")
    print(f"  CHAMPION: {champion}")
    print(f"  Overall bracket probability: {total_bracket_prob:.6e} ({total_bracket_prob*100:.6f}%)")


if __name__ == "__main__":
    main()