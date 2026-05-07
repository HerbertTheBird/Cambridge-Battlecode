#!/usr/bin/env python3
"""
Double-elimination BO9 tournament simulator with Monte Carlo.

Uses the cambc ladder API for ELO ratings and match history for head-to-head
win rates. Runs 100k simulations to estimate placement probabilities.

Usage:
    python tools/tournament_simulator.py

Optional publishing:
    TOURNAMENT_PUBLISH_REPO=/path/to/netlify/repo python tools/tournament_simulator.py

The publisher copies the generated bracket to index.html, copies the match DB
to matches.json, commits those two files, and pushes the Netlify-connected repo.
"""

from __future__ import annotations

import html
import json
import math
import os
import random
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

# ── Configuration ───────────────────────────────────────────────────────────

# 16 teams in the tournament — CHANGE THESE to match your bracket
# 16 teams in forced seed order
TOURNAMENT_TEAMS = [
    "Oxford (aka Pantheon)",              # 1
    "something else",                     # 2
    "Kessoku Band",                       # 3
    "bwaaa",                              # 4
    "MFF1",                               # 5
    "muteki",                             # 6
    "randomusergroup",                    # 7
    "test",                               # 8
    "Beehive",                            # 9
    "Grandmaster Oogway",                 # 10
    "Silver Street Capital",              # 11
    "anime girls against period cramp",   # 12
    "The Cambridge Edge",                 # 13
    "Tootill Labs",                       # 14
    "Axionite Allergic Individuals",      # 15
    "Mr Worldwide",                       # 16
]

NUM_SIMULATIONS = 1_000_000
BO_LENGTH = 9  # best-of-9, first to 5 wins
WINS_NEEDED = (BO_LENGTH + 1) // 2  # 5
MISSING_WINRATE = 0.99  # clamp for empirical per-game H2H win rates
RECENT_MATCH_LIMIT = int(os.environ.get("TOURNAMENT_RECENT_MATCH_LIMIT", "100"))
BAYESIAN_PRIOR_GAMES = float(os.environ.get("TOURNAMENT_BAYESIAN_PRIOR_GAMES", "12"))

SCRIPT_DIR = Path(__file__).resolve().parent
MATCH_DB_PATH = Path(os.environ.get("TOURNAMENT_MATCH_DB", SCRIPT_DIR / "tournament_matches.json"))
HTML_OUT_PATH = Path(os.environ.get("TOURNAMENT_HTML_OUT", "double_elim_bracket.html"))

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


# ── Match database ──────────────────────────────────────────────────────────


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_text(value: object) -> str:
    return "" if value is None else str(value)


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_match(raw: dict) -> dict | None:
    """Keep the stable fields needed for repeatable H2H predictions."""
    match_id = _as_text(raw.get("id") or raw.get("matchId"))
    if not match_id:
        return None

    return {
        "id": match_id,
        "teamAId": _as_text(raw.get("teamAId")),
        "teamBId": _as_text(raw.get("teamBId")),
        "teamAName": _as_text(raw.get("teamAName")),
        "teamBName": _as_text(raw.get("teamBName")),
        "scoreA": _as_int(raw.get("scoreA")),
        "scoreB": _as_int(raw.get("scoreB")),
        "winnerId": _as_text(raw.get("winnerId")),
        "rated": bool(raw.get("rated")),
        "type": _as_text(raw.get("type")),
        "status": _as_text(raw.get("status")),
        "createdAt": _as_text(raw.get("createdAt")),
        "completedAt": _as_text(raw.get("completedAt")),
    }


def load_match_db(path: Path) -> dict:
    if not path.exists():
        return {"schemaVersion": 1, "createdAt": utc_now_iso(), "updatedAt": "", "matches": {}}

    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        matches = {}
        for match in data:
            match_id = _as_text(match.get("id") or match.get("matchId")) if isinstance(match, dict) else ""
            if match_id:
                matches[match_id] = match
        return {"schemaVersion": 1, "createdAt": utc_now_iso(), "updatedAt": "", "matches": matches}

    if not isinstance(data, dict):
        raise ValueError(f"Unsupported match DB format in {path}")

    data.setdefault("schemaVersion", 1)
    data.setdefault("createdAt", utc_now_iso())
    data.setdefault("updatedAt", "")
    data.setdefault("matches", {})
    if isinstance(data["matches"], list):
        data["matches"] = {
            _as_text(m.get("id") or m.get("matchId")): m
            for m in data["matches"]
            if isinstance(m, dict) and _as_text(m.get("id") or m.get("matchId"))
        }
    return data


def save_match_db(path: Path, db: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(db, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def comparable_match_payload(match: dict) -> dict:
    return {
        key: value
        for key, value in match.items()
        if key not in {"firstSeenAt", "lastSeenAt"}
    }


def update_match_db(
    path: Path,
    team_names: list[str],
    team_ids: dict[str, str],
    recent_limit: int = RECENT_MATCH_LIMIT,
) -> tuple[list[dict], int, int]:
    """
    Add new matches from each team's most recent match feed to the persistent DB.
    Returns (all_stored_matches, new_match_count, total_match_count).
    """
    db = load_match_db(path)
    matches_by_id: dict[str, dict] = db["matches"]
    before_ids = set(matches_by_id)
    seen_at = utc_now_iso()
    changed_count = 0

    print(f"Fetching {recent_limit} recent matches per team...")
    for name in team_names:
        tid = team_ids.get(name)
        if not tid:
            print(f"  WARNING: Team '{name}' not found on ladder, skipping match fetch")
            continue

        recent_matches = fetch_team_matches(tid, limit=recent_limit)
        print(f"  {name}: {len(recent_matches)} recent matches")
        for raw in recent_matches:
            match = normalize_match(raw)
            if not match:
                continue
            old = matches_by_id.get(match["id"], {})
            match["firstSeenAt"] = old.get("firstSeenAt") or seen_at
            match["lastSeenAt"] = old.get("lastSeenAt") or old.get("firstSeenAt") or seen_at

            if not old:
                matches_by_id[match["id"]] = match
                continue

            if comparable_match_payload(old) != comparable_match_payload(match):
                match["lastSeenAt"] = seen_at
                matches_by_id[match["id"]] = match
                changed_count += 1

    new_count = len(set(matches_by_id) - before_ids)
    if new_count or changed_count or not path.exists():
        db["updatedAt"] = seen_at
        db["matches"] = matches_by_id
        save_match_db(path, db)

    total_count = len(matches_by_id)
    print(f"Match DB updated: +{new_count} new, {changed_count} changed, {total_count} total ({path})")
    return list(matches_by_id.values()), new_count, total_count


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
    matches: list[dict],
) -> dict[tuple[str, str], tuple[int, int]]:
    """
    Build head-to-head game wins matrix from stored match history.
    Returns {(teamA, teamB): (games_won_by_A, games_won_by_B)}.
    """
    tournament_ids = {team_ids[name] for name in team_names if name in team_ids}
    id_to_name = {v: k for k, v in team_ids.items() if k in team_names}

    # Cross-reference to find h2h results
    h2h: dict[tuple[str, str], tuple[int, int]] = {}
    seen_match_ids: set[str] = set()

    print(f"Building head-to-head matrix from {len(matches)} stored matches...")
    for m in matches:
        match_id = _as_text(m.get("id") or m.get("matchId"))
        if not match_id or match_id in seen_match_ids:
            continue

        team_a_id = _as_text(m.get("teamAId"))
        team_b_id = _as_text(m.get("teamBId"))

        # Only care about matches between two tournament teams.
        if team_a_id not in tournament_ids or team_b_id not in tournament_ids:
            continue

        name_a = id_to_name.get(team_a_id)
        name_b = id_to_name.get(team_b_id)
        if not name_a or not name_b:
            continue

        score_a = _as_int(m.get("scoreA"))
        score_b = _as_int(m.get("scoreB"))
        if score_a + score_b <= 0:
            continue

        seen_match_ids.add(match_id)

        # Accumulate per-game wins.
        key = tuple(sorted([name_a, name_b]))
        if key not in h2h:
            h2h[key] = (0, 0)

        prev_a, prev_b = h2h[key]
        if key[0] == name_a:
            h2h[key] = (prev_a + score_a, prev_b + score_b)
        else:
            h2h[key] = (prev_a + score_b, prev_b + score_a)

    return h2h


def build_game_win_prob_matrix(
    team_names: list[str],
    ratings: dict[str, float],
    h2h: dict[tuple[str, str], tuple[int, int]],
) -> dict[tuple[str, str], float]:
    """
    Build per-game win probability matrix.
    Uses h2h data if available, otherwise falls back to ELO.
    Returns {(teamA, teamB): P(A wins one game/map)}.
    """
    game_win_probs: dict[tuple[str, str], float] = {}

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
            else:
                # No H2H data: use Elo expected score instead of a fixed near-certain fallback.
                rating_a = ratings.get(a, 1500)
                rating_b = ratings.get(b, 1500)
                p_game = min(
                    max(elo_expected_score(rating_a, rating_b), 1.0 - MISSING_WINRATE),
                    MISSING_WINRATE,
                )

            game_win_probs[(a, b)] = p_game
            game_win_probs[(b, a)] = 1.0 - p_game

    return game_win_probs


def build_win_prob_matrix(
    team_names: list[str],
    ratings: dict[str, float],
    h2h: dict[tuple[str, str], tuple[int, int]],
) -> dict[tuple[str, str], float]:
    """
    Build BO9 win probability matrix.
    Uses per-game probabilities, then converts them to BO9 match probabilities.
    Returns {(teamA, teamB): P(A wins BO9)}.
    """
    game_win_probs = build_game_win_prob_matrix(team_names, ratings, h2h)
    win_probs: dict[tuple[str, str], float] = {}

    for (a, b), p_game in game_win_probs.items():
        win_probs[(a, b)] = bo_win_probability(p_game)

    return win_probs


def build_bo_win_prob_matrix_from_games(
    game_win_probs: dict[tuple[str, str], float],
) -> dict[tuple[str, str], float]:
    """Convert a per-game win matrix into a BO9 win matrix."""
    return {
        matchup: bo_win_probability(p_game)
        for matchup, p_game in game_win_probs.items()
    }


def build_bayesian_game_win_prob_matrix(
    team_names: list[str],
    ratings: dict[str, float],
    h2h: dict[tuple[str, str], tuple[int, int]],
    prior_games: float = BAYESIAN_PRIOR_GAMES,
) -> dict[tuple[str, str], float]:
    """
    Build per-game probabilities using a Beta posterior.

    The prior mean comes from Elo, and prior_games controls how many virtual
    games that Elo prior contributes before observed H2H games are added.
    """
    prior_games = max(0.0, float(prior_games))
    game_win_probs: dict[tuple[str, str], float] = {}

    for i, a in enumerate(team_names):
        for j, b in enumerate(team_names):
            if i >= j:
                continue

            key = tuple(sorted([a, b]))
            h2h_data = h2h.get(key, (0, 0))
            if key[0] == a:
                games_a, games_b = h2h_data
            else:
                games_b, games_a = h2h_data

            prior_mean_a = elo_expected_score(ratings.get(a, 1500), ratings.get(b, 1500))
            alpha_a = prior_mean_a * prior_games + games_a
            beta_a = (1.0 - prior_mean_a) * prior_games + games_b
            denominator = alpha_a + beta_a
            p_game = alpha_a / denominator if denominator > 0 else prior_mean_a
            p_game = min(max(p_game, 1.0 - MISSING_WINRATE), MISSING_WINRATE)

            game_win_probs[(a, b)] = p_game
            game_win_probs[(b, a)] = 1.0 - p_game

    return game_win_probs


def build_bayesian_matchup_rows(
    team_names: list[str],
    ratings: dict[str, float],
    h2h: dict[tuple[str, str], tuple[int, int]],
    bayesian_game_win_probs: dict[tuple[str, str], float],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for a in team_names:
        for b in team_names:
            if a == b:
                continue
            key = tuple(sorted([a, b]))
            h2h_data = h2h.get(key, (0, 0))
            if key[0] == a:
                games_a, games_b = h2h_data
            else:
                games_b, games_a = h2h_data
            prior_game = elo_expected_score(ratings.get(a, 1500), ratings.get(b, 1500))
            bayes_game = bayesian_game_win_probs.get((a, b), prior_game)
            rows.append({
                "team_a": a,
                "team_b": b,
                "games_a": games_a,
                "games_b": games_b,
                "prior_game_pct_a": prior_game * 100,
                "bayes_game_pct_a": bayes_game * 100,
                "bayes_bo9_pct_a": bo_win_probability(bayes_game) * 100,
            })
    return rows


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


def bracket_champion_key(bracket_key: tuple[str, ...]) -> str:
    return bracket_key[-1] if bracket_key else ""


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
    # Cross-feed WB quarterfinal losers into the opposite side of the lower
    # bracket to avoid immediate local-branch rematches.
    lb_r2_drop_ins = list(reversed(lb_r2_entrants))
    for i in range(4):
        a, b = lb_r1_winners[i], lb_r2_drop_ins[i]
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


def replay_bracket_key(
    bracket_key: tuple[str, ...],
    teams_by_seed: list[str],
    win_probs: dict[tuple[str, str], float],
) -> list[tuple[str, str, str, str, float]]:
    """Replay a winner-only bracket key into labeled matchups."""
    winner_iter = iter(bracket_key)
    bracket_matches: list[tuple[str, str, str, str, float]] = []

    def replay_match(label: str, a: str, b: str) -> str:
        w = next(winner_iter)
        p = win_probs.get((w, b if w == a else a), 0.5)
        bracket_matches.append((label, a, b, w, p))
        return w

    ub_r1_matchups = get_seeded_matchups(len(teams_by_seed))
    ub_r1_winners = []
    lb_r1_entrants = []
    for idx, (a_idx, b_idx) in enumerate(ub_r1_matchups):
        a, b = teams_by_seed[a_idx], teams_by_seed[b_idx]
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
    lb_r2_drop_ins = list(reversed(lb_r2_entrants))
    for idx in range(4):
        a, b = lb_r1_winners[idx], lb_r2_drop_ins[idx]
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
    if gf_winner != ub_winner:
        replay_match("GF-Reset", ub_winner, lb_finalist)

    return bracket_matches


def calculated_most_likely_brackets_by_champion(
    teams_by_seed: list[str],
    win_probs: dict[tuple[str, str], float],
) -> dict[str, dict[str, object]]:
    """
    Calculate the highest-probability full bracket for each possible champion.

    This is deterministic: it maximizes the product of BO9 match probabilities
    over the fixed double-elimination structure, including a possible GF reset.
    """
    Match = tuple[str, str, str, str, float]
    Outcome = tuple[float, list[Match]]
    LeafMap = dict[str, Outcome]
    best_by_champion: dict[str, Outcome] = {}

    def winner_prob(a: str, b: str, winner: str) -> float:
        if winner == a:
            return win_probs.get((a, b), 0.5)
        return win_probs.get((b, a), 0.5)

    def add_best(target: dict[str, Outcome], winner: str, probability: float, matches: list[Match]) -> None:
        previous = target.get(winner)
        if previous is None or probability > previous[0]:
            target[winner] = (probability, matches)

    def leaf(team: str) -> LeafMap:
        return {team: (1.0, [])}

    def combine(label: str, left: LeafMap, right: LeafMap) -> LeafMap:
        result: LeafMap = {}
        for a, (prob_a, matches_a) in left.items():
            for b, (prob_b, matches_b) in right.items():
                if a == b:
                    continue
                base_prob = prob_a * prob_b
                base_matches = [*matches_a, *matches_b]
                for winner in (a, b):
                    p = winner_prob(a, b, winner)
                    add_best(
                        result,
                        winner,
                        base_prob * p,
                        [*base_matches, (label, a, b, winner, p)],
                    )
        return result

    def lower_bracket_outcomes(
        lb_r1_entrants: list[str],
        lb_r2_entrants: list[str],
        lb_r4_entrants: list[str],
        ub_final_loser: str,
    ) -> LeafMap:
        lb_r1 = [
            combine(
                f"LB-R1-{idx+1}",
                leaf(lb_r1_entrants[idx * 2]),
                leaf(lb_r1_entrants[idx * 2 + 1]),
            )
            for idx in range(4)
        ]
        lb_r2_drop_ins = list(reversed(lb_r2_entrants))
        lb_r2 = [
            combine(f"LB-R2-{idx+1}", lb_r1[idx], leaf(lb_r2_drop_ins[idx]))
            for idx in range(4)
        ]
        lb_r3 = [
            combine("LB-R3-1", lb_r2[0], lb_r2[1]),
            combine("LB-R3-2", lb_r2[2], lb_r2[3]),
        ]
        lb_r4 = [
            combine("LB-R4-1", lb_r3[0], leaf(lb_r4_entrants[0])),
            combine("LB-R4-2", lb_r3[1], leaf(lb_r4_entrants[1])),
        ]
        lb_r5 = combine("LB-R5", lb_r4[0], lb_r4[1])
        return combine("LB-Final", lb_r5, leaf(ub_final_loser))

    def finish_bracket(
        ub_winner: str,
        ub_prob: float,
        ub_matches: list[Match],
        lb_outcomes: LeafMap,
    ) -> None:
        for lb_winner, (lb_prob, lb_matches) in lb_outcomes.items():
            p_ub = winner_prob(ub_winner, lb_winner, ub_winner)
            p_lb = 1.0 - p_ub
            base_prob = ub_prob * lb_prob
            base_matches = [*ub_matches, *lb_matches]

            add_best(
                best_by_champion,
                ub_winner,
                base_prob * p_ub,
                [*base_matches, ("Grand-Final", ub_winner, lb_winner, ub_winner, p_ub)],
            )
            add_best(
                best_by_champion,
                lb_winner,
                base_prob * p_lb * p_lb,
                [
                    *base_matches,
                    ("Grand-Final", ub_winner, lb_winner, lb_winner, p_lb),
                    ("GF-Reset", ub_winner, lb_winner, lb_winner, p_lb),
                ],
            )

    def advance_states(
        states: list[tuple[float, list[Match], list[str], list[str], list[str], list[str]]],
        labels: list[str],
        pair_source: list[tuple[str, str]],
        winner_bucket: str,
        loser_bucket: str,
    ) -> list[tuple[float, list[Match], list[str], list[str], list[str], list[str]]]:
        advanced = []
        for prob, matches, ub_winners, lb_r1_entrants, lb_r2_entrants, lb_r4_entrants in states:
            stage_states = [(prob, matches, [], lb_r1_entrants, lb_r2_entrants, lb_r4_entrants)]
            for label, (a, b) in zip(labels, pair_source):
                next_stage = []
                for stage_prob, stage_matches, stage_winners, r1, r2, r4 in stage_states:
                    for winner in (a, b):
                        p = winner_prob(a, b, winner)
                        loser = _get_loser(a, b, winner)
                        next_winners = [*stage_winners, winner]
                        next_r1 = [*r1, loser] if loser_bucket == "r1" else r1
                        next_r2 = [*r2, loser] if loser_bucket == "r2" else r2
                        next_r4 = [*r4, loser] if loser_bucket == "r4" else r4
                        next_stage.append((
                            stage_prob * p,
                            [*stage_matches, (label, a, b, winner, p)],
                            next_winners,
                            next_r1,
                            next_r2,
                            next_r4,
                        ))
                stage_states = next_stage
            for stage_prob, stage_matches, stage_winners, r1, r2, r4 in stage_states:
                if winner_bucket == "ub":
                    advanced.append((stage_prob, stage_matches, stage_winners, r1, r2, r4))
        return advanced

    ub_r1_matchups = [
        (teams_by_seed[a_idx], teams_by_seed[b_idx])
        for a_idx, b_idx in get_seeded_matchups(len(teams_by_seed))
    ]
    states = [(1.0, [], [], [], [], [])]
    states = advance_states(
        states,
        [f"UB-R1-{idx+1}" for idx in range(8)],
        ub_r1_matchups,
        "ub",
        "r1",
    )

    ub_qf_pairs_by_state = []
    for state in states:
        ub_winners = state[2]
        ub_qf_pairs_by_state.append([
            (ub_winners[idx * 2], ub_winners[idx * 2 + 1])
            for idx in range(4)
        ])
    qf_states = []
    for state, pairs in zip(states, ub_qf_pairs_by_state):
        qf_states.extend(advance_states(
            [state[:2] + ([],) + state[3:]],
            [f"UB-QF-{idx+1}" for idx in range(4)],
            pairs,
            "ub",
            "r2",
        ))
    states = qf_states

    ub_sf_pairs_by_state = []
    for state in states:
        ub_winners = state[2]
        ub_sf_pairs_by_state.append([
            (ub_winners[idx * 2], ub_winners[idx * 2 + 1])
            for idx in range(2)
        ])
    sf_states = []
    for state, pairs in zip(states, ub_sf_pairs_by_state):
        sf_states.extend(advance_states(
            [state[:2] + ([],) + state[3:]],
            [f"UB-SF-{idx+1}" for idx in range(2)],
            pairs,
            "ub",
            "r4",
        ))
    states = sf_states

    for prob, matches, ub_sf_winners, lb_r1_entrants, lb_r2_entrants, lb_r4_entrants in states:
        a, b = ub_sf_winners[0], ub_sf_winners[1]
        for ub_winner in (a, b):
            p = winner_prob(a, b, ub_winner)
            ub_final_loser = _get_loser(a, b, ub_winner)
            ub_matches = [*matches, ("UB-Final", a, b, ub_winner, p)]
            lb_outcomes = lower_bracket_outcomes(
                lb_r1_entrants,
                lb_r2_entrants,
                lb_r4_entrants,
                ub_final_loser,
            )
            finish_bracket(ub_winner, prob * p, ub_matches, lb_outcomes)

    return {
        champion: {"probability": probability, "matches": matches}
        for champion, (probability, matches) in best_by_champion.items()
    }


def write_bracket_html(
    bracket_matches: list[tuple[str, str, str, str, float]],
    out_path: str | Path,
    best_bracket_count: int,
    num_simulations: int,
    bracket_prob_percent: float,
    total_bracket_prob: float,
    match_db_count: int = 0,
    new_match_count: int = 0,
    generated_at: str | None = None,
    ratings: dict[str, float] | None = None,
    win_probs: dict[tuple[str, str], float] | None = None,
    game_win_probs: dict[tuple[str, str], float] | None = None,
    seed_map: dict[str, int] | None = None,
    alternate_bracket_matches: list[tuple[str, str, str, str, float]] | None = None,
    alternate_bracket_count: int = 0,
    champion_brackets: list[dict[str, object]] | None = None,
    placement_rows: list[dict[str, object]] | None = None,
    h2h_rows: list[dict[str, object]] | None = None,
    results_model: dict[str, object] | None = None,
    bayesian_matchup_rows: list[dict[str, object]] | None = None,
) -> None:
    """
    Writes a Battlecode-style visual double-elimination bracket.

    Uses:
      - absolute-positioned match cards
      - SVG connector lines
      - solid lines for winners advancing
      - dashed red lines for loser drops into lower bracket
      - clickable round headers to collapse/expand rounds

    bracket_matches entries:
      (label, team_a, team_b, winner, winner_probability)
    """
    out_path = Path(out_path)
    ratings = ratings or {}
    win_probs = win_probs or {}
    game_win_probs = game_win_probs or {}
    seed_map = seed_map or {}
    alternate_bracket_matches = alternate_bracket_matches or []
    champion_brackets = champion_brackets or []
    placement_rows = placement_rows or []
    h2h_rows = h2h_rows or []
    results_model = results_model or {}
    bayesian_matchup_rows = bayesian_matchup_rows or []
    generated_at = generated_at or utc_now_iso()

    DISPLAY_NAME_OVERRIDES = {
        "Oxford": "Pantheon",
        "Oxford (aka Pantheon)": "Pantheon",
    }

    def esc(s: object) -> str:
        return html.escape(str(s))

    def display_name(name: str) -> str:
        return DISPLAY_NAME_OVERRIDES.get(name, name)

    def short_name(name: str, limit: int = 26) -> str:
        name = display_name(name)
        return name if len(name) <= limit else name[: limit - 1] + "…"

    def get_match(label: str):
        for m in [*bracket_matches, *alternate_bracket_matches]:
            if m[0] == label:
                return m
        return None

    def round_key_for_label(label: str) -> str:
        if label.startswith("UB-R1-"):
            return "UB-R1"
        if label.startswith("UB-QF-"):
            return "UB-QF"
        if label.startswith("UB-SF-"):
            return "UB-SF"
        if label == "UB-Final":
            return "UB-Final"
        if label.startswith("LB-R1-"):
            return "LB-R1"
        if label.startswith("LB-R2-"):
            return "LB-R2"
        if label.startswith("LB-R3-"):
            return "LB-R3"
        if label.startswith("LB-R4-"):
            return "LB-R4"
        if label == "LB-R5":
            return "LB-R5"
        if label == "LB-Final":
            return "LB-Final"
        if label == "Grand-Final":
            return "Grand-Final"
        if label == "GF-Reset":
            return "GF-Reset"
        return label

    def predicted_series_scores(a: str, b: str, winner: str) -> tuple[int, int]:
        """
        Predict displayed BO9 score by doing winrate * 9 and rounding.

        Example:
            p = 0.62 -> round(0.62 * 9) = 6
            opponent score = round(0.38 * 9) = 3

        This is only a display prediction. The simulator itself sampled the
        whole BO9 winner, not the individual game score.
        """
        p_a = game_win_probs.get((a, b), 0.5)
        p_b = 1.0 - p_a

        a_score = round(p_a * 9)
        b_score = round(p_b * 9)

        # Keep total at exactly 9 after rounding.
        total = a_score + b_score
        if total != 9:
            if a_score >= b_score:
                b_score = 9 - a_score
            else:
                a_score = 9 - b_score

        # Clamp just in case.
        a_score = max(0, min(9, a_score))
        b_score = max(0, min(9, b_score))

        # Make sure the displayed winner actually has the higher score.
        # Since this is BO9, a winner should have at least 5.
        if winner == a and a_score <= b_score:
            a_score, b_score = 5, 4
        elif winner == b and b_score <= a_score:
            b_score, a_score = 5, 4

        return a_score, b_score

    # ── Layout constants ───────────────────────────────────────────────

    CARD_W = 198
    CARD_H = 48
    HEADER_H = 32
    ROW_GAP = 20
    COL_GAP = 48

    X0 = 20
    Y0 = HEADER_H + 20

    # Upper bracket columns
    X_UB_R1 = X0
    X_UB_QF = X_UB_R1 + CARD_W + COL_GAP
    X_UB_SF = X_UB_QF + CARD_W + COL_GAP
    X_UB_F = X_UB_SF + CARD_W + COL_GAP

    # Lower bracket columns
    X_LB_R1 = X0
    X_LB_R2 = X_LB_R1 + CARD_W + COL_GAP
    X_LB_R3 = X_LB_R2 + CARD_W + COL_GAP
    X_LB_R4 = X_LB_R3 + CARD_W + COL_GAP
    X_LB_R5 = X_LB_R4 + CARD_W + COL_GAP
    X_LB_F = X_LB_R5 + CARD_W + COL_GAP

    # Grand finals should be to the right of LB Final,
    # but still aligned vertically with the winners bracket.
    X_GF = X_LB_F
    X_RESET = X_GF + CARD_W + COL_GAP

    Y_UB = Y0
    Y_LB = Y_UB + 8 * (CARD_H + ROW_GAP) + 66

    Y_UB_HEADERS = 0
    Y_LB_HEADERS = Y_LB - HEADER_H - 8

    W = max(X_RESET + CARD_W + 28, X_LB_F + CARD_W + 28)
    H = Y_LB + 4 * (CARD_H + ROW_GAP) + 112

    # ── Position map ───────────────────────────────────────────────────

    pos: dict[str, tuple[int, int]] = {}

    # Upper R1: 8 stacked cards
    for i in range(1, 9):
        pos[f"UB-R1-{i}"] = (X_UB_R1, Y_UB + (i - 1) * (CARD_H + ROW_GAP))

    # Upper QF: centered between pairs
    for i in range(1, 5):
        y_a = pos[f"UB-R1-{2 * i - 1}"][1]
        y_b = pos[f"UB-R1-{2 * i}"][1]
        pos[f"UB-QF-{i}"] = (X_UB_QF, (y_a + y_b) // 2)

    # Upper SF
    for i in range(1, 3):
        y_a = pos[f"UB-QF-{2 * i - 1}"][1]
        y_b = pos[f"UB-QF-{2 * i}"][1]
        pos[f"UB-SF-{i}"] = (X_UB_SF, (y_a + y_b) // 2)

    # Upper Final
    pos["UB-Final"] = (
        X_UB_F,
        (pos["UB-SF-1"][1] + pos["UB-SF-2"][1]) // 2,
    )

    # Lower R1
    for i in range(1, 5):
        pos[f"LB-R1-{i}"] = (X_LB_R1, Y_LB + (i - 1) * (CARD_H + ROW_GAP))

    # Lower R2
    for i in range(1, 5):
        pos[f"LB-R2-{i}"] = (X_LB_R2, Y_LB + (i - 1) * (CARD_H + ROW_GAP))

    # Lower R3
    for i in range(1, 3):
        y_a = pos[f"LB-R2-{2 * i - 1}"][1]
        y_b = pos[f"LB-R2-{2 * i}"][1]
        pos[f"LB-R3-{i}"] = (X_LB_R3, (y_a + y_b) // 2)

    # Lower R4
    for i in range(1, 3):
        pos[f"LB-R4-{i}"] = (
            X_LB_R4,
            pos[f"LB-R3-{i}"][1],
        )

    # Lower R5 + Lower Final
    pos["LB-R5"] = (
        X_LB_R5,
        (pos["LB-R4-1"][1] + pos["LB-R4-2"][1]) // 2,
    )
    pos["LB-Final"] = (
        X_LB_F,
        pos["LB-R5"][1],
    )
    pos["Grand-Final"] = (X_GF, pos["UB-Final"][1])

    if get_match("GF-Reset"):
        pos["GF-Reset"] = (X_RESET, pos["Grand-Final"][1])

    # ── SVG connector helpers ──────────────────────────────────────────

    ROW_TOP_MID = 11.5
    ROW_BOTTOM_MID = 35.5

    def render_lines(matches: list[tuple[str, str, str, str, float]]) -> str:
        match_by_label = {label: (a, b, w) for label, a, b, w, _ in matches}
        lines: list[str] = []

        def match_team_y(label: str, team: str) -> float:
            x, y = pos[label]
            match = match_by_label.get(label)
            if not match:
                return y + CARD_H / 2
            a, b, _ = match
            if team == a:
                return y + ROW_TOP_MID
            if team == b:
                return y + ROW_BOTTOM_MID
            return y + CARD_H / 2

        def right_team(label: str, team: str) -> tuple[float, float]:
            x, _ = pos[label]
            return x + CARD_W, match_team_y(label, team)

        def left_team(label: str, team: str) -> tuple[float, float]:
            x, _ = pos[label]
            return x, match_team_y(label, team)

        def right_mid(label: str, team: str) -> tuple[float, float]:
            x, _ = pos[label]
            return x + CARD_W, match_team_y(label, team)

        def winner_of(label: str) -> str | None:
            match = match_by_label.get(label)
            return match[2] if match else None

        def loser_of(label: str) -> str | None:
            match = match_by_label.get(label)
            if not match:
                return None
            a, b, w = match
            return b if w == a else a

        def add_path(
            cls: str,
            src: str,
            dst: str,
            points: list[tuple[float, float]],
            dashed: bool = False,
        ) -> None:
            if not points:
                return
            src_round = round_key_for_label(src)
            dst_round = round_key_for_label(dst)
            dash = ' stroke-dasharray="4 4"' if dashed else ""
            d = " ".join(
                f"{'M' if idx == 0 else 'L'} {x:.1f} {y:.1f}"
                for idx, (x, y) in enumerate(points)
            )
            lines.append(
                f'<path class="{cls} round-line" '
                f'data-src-round="{esc(src_round)}" '
                f'data-dst-round="{esc(dst_round)}" '
                f'd="{d}"{dash}/>'
            )

        def draw_advance(src: str, dst: str, lane_offset: float = 0) -> None:
            team = winner_of(src)
            if not team or src not in pos or dst not in pos:
                return
            x1, y1 = right_team(src, team)
            x2, y2 = left_team(dst, team)
            mid_x = x1 + max(22 + lane_offset, (x2 - x1) * 0.45)
            add_path("advance-line", src, dst, [(x1, y1), (mid_x, y1), (mid_x, y2), (x2, y2)])

        def draw_advance_to_right(src: str, dst: str) -> None:
            team = winner_of(src)
            if not team or src not in pos or dst not in pos:
                return
            x1, y1 = right_team(src, team)
            x2, y2 = right_mid(dst, team)
            mid_x = max(x1, x2) + 28
            add_path("advance-line", src, dst, [(x1, y1), (mid_x, y1), (mid_x, y2), (x2, y2)])

        def draw_drop(
            src: str,
            dst: str,
            lane_offset: float = 0,
            mid_x_override: float | None = None,
        ) -> None:
            team = loser_of(src)
            if not team or src not in pos or dst not in pos:
                return
            x1, y1 = right_team(src, team)
            x2, y2 = left_team(dst, team)
            mid_x = (
                mid_x_override
                if mid_x_override is not None
                else x1 + max(22 + lane_offset, (x2 - x1) * 0.45)
            )
            add_path(
                "drop-line",
                src,
                dst,
                [(x1, y1), (mid_x, y1), (mid_x, y2), (x2, y2)],
                dashed=True,
            )

        # Upper winner advancement
        for i in range(1, 9):
            draw_advance(f"UB-R1-{i}", f"UB-QF-{(i + 1) // 2}")

        for i in range(1, 5):
            draw_advance(f"UB-QF-{i}", f"UB-SF-{(i + 1) // 2}")

        draw_advance("UB-SF-1", "UB-Final")
        draw_advance("UB-SF-2", "UB-Final")
        draw_advance("UB-Final", "Grand-Final")

        # Lower winner advancement
        for i in range(1, 5):
            draw_advance(f"LB-R1-{i}", f"LB-R2-{i}")

        draw_advance("LB-R2-1", "LB-R3-1")
        draw_advance("LB-R2-2", "LB-R3-1")
        draw_advance("LB-R2-3", "LB-R3-2")
        draw_advance("LB-R2-4", "LB-R3-2")

        draw_advance("LB-R3-1", "LB-R4-1")
        draw_advance("LB-R3-2", "LB-R4-2")

        draw_advance("LB-R4-1", "LB-R5")
        draw_advance("LB-R4-2", "LB-R5")
        draw_advance("LB-R5", "LB-Final")
        draw_advance_to_right("LB-Final", "Grand-Final")

        if "GF-Reset" in match_by_label:
            draw_advance("Grand-Final", "GF-Reset")

        # Loser drops: route from the losing row, with separated lanes.
        for i in range(1, 9):
            draw_drop(f"UB-R1-{i}", f"LB-R1-{(i + 1) // 2}", lane_offset=(i - 1) * 3)

        for i in range(1, 5):
            draw_drop(f"UB-QF-{i}", f"LB-R2-{5 - i}", lane_offset=(i - 1) * 4)

        draw_drop("UB-SF-1", "LB-R4-1", lane_offset=0)
        draw_drop("UB-SF-2", "LB-R4-2", lane_offset=10)
        draw_drop(
            "UB-Final",
            "LB-Final",
            mid_x_override=X_LB_R5 + CARD_W + COL_GAP / 2,
        )

        return "".join(lines)

    # ── Render match cards ─────────────────────────────────────────────

    def render_team(team: str, opponent: str, score: int, is_winner: bool) -> str:
        rating = ratings.get(team)
        rating_text = f"{rating:.0f}" if rating is not None else "—"
        seed_text = f"#{seed_map[team]}" if team in seed_map else ""
        p_bo9 = win_probs.get((team, opponent), 0.5)
        row_cls = "team-row winner-row" if is_winner else "team-row"

        return f"""
        <div class="{row_cls}">
            <span class="seed">{esc(seed_text)}</span>
            <span class="rating">{esc(rating_text)}</span>
            <span class="name" title="{esc(display_name(team))}">{esc(short_name(team))}</span>
            <span class="score">{score}</span>
            <span class="team-prob">{p_bo9:.0%}</span>
        </div>
        """

    def render_card(match: tuple[str, str, str, str, float]) -> str:
        label, a, b, w, p = match
        if label not in pos:
            return ""

        x, y = pos[label]
        loser = b if w == a else a
        a_score, b_score = predicted_series_scores(a, b, w)
        round_key = round_key_for_label(label)

        return f"""
        <div
            class="match-card"
            data-round="{esc(round_key)}"
            data-market-id="{esc(label)}::{esc(a)}::{esc(b)}"
            data-market-label="{esc(label)}"
            data-team-a="{esc(a)}"
            data-team-b="{esc(b)}"
            data-pred-a="{a_score}"
            data-pred-b="{b_score}"
            style="left:{x}px; top:{y}px; width:{CARD_W}px; height:{CARD_H}px;"
            title="{esc(label)} | Winner: {esc(display_name(w))} | Loser: {esc(display_name(loser))}"
        >
            {render_team(a, b, a_score, w == a)}
            <div class="divider"></div>
            {render_team(b, a, b_score, w == b)}
        </div>
        """

    def render_cards(matches: list[tuple[str, str, str, str, float]]) -> str:
        return "".join(render_card(m) for m in matches)

    def bracket_match_probability(matches: list[tuple[str, str, str, str, float]]) -> float:
        probability = 1.0
        for _, _, _, _, p in matches:
            probability *= p
        return probability

    # ── Round headers ──────────────────────────────────────────────────

    headers = [
        # Winners bracket row
        ("UB R1", X_UB_R1, Y_UB_HEADERS, 8, "UB-R1"),
        ("UB QF", X_UB_QF, Y_UB_HEADERS, 4, "UB-QF"),
        ("UB SF", X_UB_SF, Y_UB_HEADERS, 2, "UB-SF"),
        ("UB Final", X_UB_F, Y_UB_HEADERS, 1, "UB-Final"),
        ("GF", X_GF, Y_UB_HEADERS, 1, "Grand-Final"),

        # Losers bracket row
        ("LB R1", X_LB_R1, Y_LB_HEADERS, 4, "LB-R1"),
        ("LB R2", X_LB_R2, Y_LB_HEADERS, 4, "LB-R2"),
        ("LB R3", X_LB_R3, Y_LB_HEADERS, 2, "LB-R3"),
        ("LB R4", X_LB_R4, Y_LB_HEADERS, 2, "LB-R4"),
        ("LB R5", X_LB_R5, Y_LB_HEADERS, 1, "LB-R5"),
        ("LB Final", X_LB_F, Y_LB_HEADERS, 1, "LB-Final"),
    ]

    def render_header(name: str, x: int, y: int, count: int, round_key: str) -> str:
        return f"""
        <button
            type="button"
            class="round-header round-toggle"
            data-round="{esc(round_key)}"
            style="left:{x}px; top:{y}px; width:{CARD_W}px;"
            title="Click to collapse/expand {esc(name)}"
        >
            <span>{esc(name)}</span>
            <span class="count">({count})</span>
        </button>
        """

    def render_headers(matches: list[tuple[str, str, str, str, float]]) -> str:
        panel_headers = [*headers]
        if any(label == "GF-Reset" for label, *_ in matches):
            panel_headers.append(("Reset", X_RESET, Y_UB_HEADERS, 1, "GF-Reset"))
        return "\n".join(render_header(*h) for h in panel_headers)

    champion = bracket_matches[-1][3] if bracket_matches else "Unknown"
    alternate_champion = (
        alternate_bracket_matches[-1][3] if alternate_bracket_matches else "Unavailable"
    )

    def render_bracket_panel(
        matches: list[tuple[str, str, str, str, float]],
        empty_message: str,
    ) -> str:
        if not matches:
            return f'<div class="empty-state">{esc(empty_message)}</div>'
        return f"""
        <div class="legend">
            <div class="legend-item"><span class="legend-line"></span> winner advances</div>
            <div class="legend-item"><span class="legend-line drop"></span> loser drops to lower bracket</div>
        </div>
        <div class="viewport">
            <div class="canvas">
                {render_headers(matches)}

                <svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">
                    {render_lines(matches)}
                </svg>

                {render_cards(matches)}
            </div>
        </div>
        <div class="note">
            Displayed scores are predicted from per-game winrate x 9. Gray row percentages are BO9 win probabilities.
        </div>
        """

    if not champion_brackets and alternate_bracket_matches:
        champion_brackets = [{
            "champion": alternate_champion,
            "count": alternate_bracket_count,
            "matches": alternate_bracket_matches,
        }]

    def champion_entry_percent(entry: dict[str, object]) -> float:
        if "observed_pct" in entry:
            try:
                return float(entry["observed_pct"])
            except (TypeError, ValueError):
                return 0.0
        count = int(entry.get("count") or 0)
        return count / num_simulations * 100 if num_simulations else 0.0

    def champion_entry_source(entry: dict[str, object]) -> str:
        if entry.get("source") == "calculated":
            return "[CALCULATED]"
        return f'{int(entry.get("count") or 0):,} / {num_simulations:,}'

    def champion_path_probability(entry: dict[str, object]) -> str:
        try:
            return f'{float(entry.get("path_prob_pct") or 0.0):.6f}%'
        except (TypeError, ValueError):
            return "0.000000%"

    def render_champion_option(idx: int, entry: dict[str, object]) -> str:
        source_badge = " [CALCULATED]" if entry.get("source") == "calculated" else ""
        return (
            f'<option value="{idx}">{esc(display_name(str(entry["champion"])))}'
            f' ({champion_entry_percent(entry):.3f}%){source_badge}</option>'
        )

    champion_options = "\n".join(
        render_champion_option(idx, entry)
        for idx, entry in enumerate(champion_brackets)
    )
    if not champion_options:
        champion_options = '<option value="">No alternate champions</option>'

    champion_select_disabled = " disabled" if not champion_brackets else ""

    champion_panels = "\n".join(
        f"""
        <div class="champion-bracket-panel{' active' if idx == 0 else ''}" data-champion-panel="{idx}">
            <div class="stats">
                <div class="pill">
                    <div class="k">Champion</div>
                    <div class="v">{esc(display_name(str(entry["champion"])))}</div>
                </div>
                <div class="pill">
                    <div class="k">Bracket source</div>
                    <div class="v">{esc(champion_entry_source(entry))}</div>
                </div>
                <div class="pill">
                    <div class="k">Observed freq</div>
                    <div class="v">{champion_entry_percent(entry):.3f}%</div>
                </div>
                <div class="pill">
                    <div class="k">Path prob</div>
                    <div class="v">{champion_path_probability(entry)}</div>
                </div>
            </div>
            {render_bracket_panel(
                entry.get("matches") if isinstance(entry.get("matches"), list) else [],
                "No bracket for this champion appeared in this simulation run.",
            )}
        </div>
        """
        for idx, entry in enumerate(champion_brackets)
    )
    if not champion_panels:
        champion_panels = '<div class="empty-state">No different champion appeared in this simulation run.</div>'

    def fmt_pct(value: object, digits: int = 1) -> str:
        try:
            return f"{float(value):.{digits}f}%"
        except (TypeError, ValueError):
            return "0.0%"

    placement_bucket_keys = [
        ("1st", "first"),
        ("2nd", "second"),
        ("3rd", "third"),
        ("4th", "fourth"),
        ("5-6", "fifth_sixth"),
        ("7-8", "seventh_eighth"),
        ("9-12", "ninth_twelfth"),
        ("13-16", "thirteenth_sixteenth"),
    ]

    def pct_value(value: object) -> float:
        try:
            return max(0.0, min(100.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    def prob_cell(value: object) -> str:
        pct = pct_value(value)
        heat = (pct / 100.0) * 0.30
        return f'<td class="prob-cell" style="--heat:{heat:.3f};">{fmt_pct(pct)}</td>'

    def render_probability_curve(row: dict[str, object]) -> str:
        values = [pct_value(row[key]) for _, key in placement_bucket_keys]
        width = 280
        height = 88
        pad_x = 10
        pad_top = 8
        pad_bottom = 18
        baseline = height - pad_bottom
        graph_h = baseline - pad_top
        step = (width - pad_x * 2) / max(1, len(values) - 1)
        points = []
        for idx, value in enumerate(values):
            x = pad_x + idx * step
            y = baseline - (value / 100.0) * graph_h
            points.append((x, y))
        if points:
            line_path = " ".join(
                f"{'M' if idx == 0 else 'L'} {x:.1f} {y:.1f}"
                for idx, (x, y) in enumerate(points)
            )
            area_path = f"{line_path} L {points[-1][0]:.1f} {baseline:.1f} L {points[0][0]:.1f} {baseline:.1f} Z"
        else:
            line_path = ""
            area_path = ""
        label_cells = "".join(f"<span>{esc(label)}</span>" for label, _ in placement_bucket_keys)
        return f"""
        <div class="curve-card">
            <div class="curve-title">
                <span>{esc(display_name(str(row["team"])))}</span>
                <span>{fmt_pct(row["win_pct"])}</span>
            </div>
            <svg class="curve-svg" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(display_name(str(row["team"])))} placement probability curve">
                <path class="curve-baseline" d="M {pad_x} {baseline:.1f} L {width - pad_x} {baseline:.1f}"></path>
                <path class="curve-area" d="{area_path}"></path>
                <path class="curve-line" d="{line_path}"></path>
            </svg>
            <div class="curve-labels">{label_cells}</div>
        </div>
        """

    placement_table_rows = "\n".join(
        f"""
        <tr>
            <th class="text-col">{esc(display_name(str(row["team"])))}</th>
            {prob_cell(row["win_pct"])}
            {prob_cell(row["top3_pct"])}
            {prob_cell(row["first"])}
            {prob_cell(row["second"])}
            {prob_cell(row["third"])}
            {prob_cell(row["fourth"])}
            {prob_cell(row["fifth_sixth"])}
            {prob_cell(row["seventh_eighth"])}
            {prob_cell(row["ninth_twelfth"])}
            {prob_cell(row["thirteenth_sixteenth"])}
        </tr>
        """
        for row in placement_rows
    )
    if not placement_table_rows:
        placement_table_rows = '<tr><td colspan="11" class="empty-cell">No placement data was generated.</td></tr>'

    placement_curve_cards = "\n".join(render_probability_curve(row) for row in placement_rows)
    if not placement_curve_cards:
        placement_curve_cards = '<div class="empty-state">No placement curves were generated.</div>'

    h2h_table_rows = "\n".join(
        f"""
        <tr>
            <th class="text-col">{esc(display_name(str(row["team_a"])))}</th>
            <th class="text-col">{esc(display_name(str(row["team_b"])))}</th>
            <td>{esc(row["games_a"])}-{esc(row["games_b"])}</td>
            <td>{fmt_pct(row["game_pct_a"])}</td>
            <td>{fmt_pct(row["bo9_pct_a"])}</td>
        </tr>
        """
        for row in h2h_rows
    )
    if not h2h_table_rows:
        h2h_table_rows = '<tr><td colspan="5" class="empty-cell">No stored head-to-head data yet.</td></tr>'

    def model_display_value(key: str, fallback: str = "") -> str:
        value = results_model.get(key, fallback)
        if isinstance(value, float):
            return f"{value:g}"
        return str(value)

    calculator_team_options = "\n".join(
        f'<option value="{esc(team)}">{esc(display_name(team))}</option>'
        for team in TOURNAMENT_TEAMS
    )
    affiliation_options = "\n".join(
        ['<option value="Other">Other</option>'] +
        [
            f'<option value="{esc(team)}">{esc(display_name(team))}</option>'
            for team in TOURNAMENT_TEAMS
        ]
    )
    market_seed_json = (
        json.dumps(
            [{"id": team, "name": display_name(team)} for team in TOURNAMENT_TEAMS],
            separators=(",", ":"),
        )
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    bayesian_matchup_json = (
        json.dumps(bayesian_matchup_rows, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )

    primary_bracket_panel = render_bracket_panel(
        bracket_matches,
        "No most-common bracket data was generated.",
    )
    html_text = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Predicted Cambridge BC Final Bracket</title>
<style>
    :root {{
        --background: #09090b;
        --foreground: #f4f4f5;
        --muted: #18181b;
        --muted-foreground: #a1a1aa;
        --border: #27272a;
        --card: #0f0f12;
        --card-hover: #17171b;
        --winner-bg: rgba(34, 197, 94, 0.10);
        --winner-fg: #86efac;
        --line: rgba(244, 244, 245, 0.38);
        --drop: rgba(239, 68, 68, 0.34);
        --accent: #60a5fa;
    }}

    * {{
        box-sizing: border-box;
    }}

    body {{
        margin: 0;
        min-height: 100vh;
        overflow: auto;
        scrollbar-width: none;
        background:
            radial-gradient(circle at 20% 0%, rgba(96, 165, 250, 0.10), transparent 28rem),
            radial-gradient(circle at 90% 20%, rgba(34, 197, 94, 0.08), transparent 26rem),
            var(--background);
        color: var(--foreground);
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
        font-size: 13px;
    }}

    body::-webkit-scrollbar {{
        display: none;
    }}

    .page {{
        padding: 14px;
    }}

    .topbar {{
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        gap: 20px;
        margin-bottom: 10px;
    }}

    .title-row {{
        display: flex;
        align-items: center;
        gap: 12px;
        min-width: 0;
    }}

    .title h1 {{
        margin: 0;
        font-size: 19px;
        line-height: 1.1;
        letter-spacing: -0.03em;
    }}

    .market-button,
    .market-secondary,
    .market-primary {{
        border: 1px solid rgba(96, 165, 250, 0.48);
        background: rgba(96, 165, 250, 0.14);
        color: var(--foreground);
        border-radius: 8px;
        cursor: pointer;
        font: inherit;
        font-size: 11px;
        font-weight: 800;
        padding: 8px 10px;
        white-space: nowrap;
    }}

    .market-button:hover,
    .market-secondary:hover,
    .market-primary:hover {{
        background: rgba(96, 165, 250, 0.22);
    }}

    .market-primary {{
        background: rgba(34, 197, 94, 0.16);
        border-color: rgba(34, 197, 94, 0.48);
    }}

    .market-secondary {{
        border-color: var(--border);
        background: rgba(15, 15, 18, 0.82);
    }}

    .market-status {{
        color: var(--muted-foreground);
        font-size: 11px;
        margin-top: 6px;
    }}

    .title p {{
        margin: 4px 0 0;
        color: var(--muted-foreground);
    }}

    .stats {{
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        justify-content: flex-end;
    }}

    .pill {{
        border: 1px solid var(--border);
        background: rgba(15, 15, 18, 0.82);
        border-radius: 10px;
        padding: 5px 8px;
        min-width: 132px;
    }}

    .pill .k {{
        color: var(--muted-foreground);
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }}

    .pill .v {{
        font-weight: 700;
        margin-top: 2px;
        white-space: nowrap;
    }}

    .tabbar {{
        display: flex;
        align-items: center;
        gap: 6px;
        margin: 10px 0;
        overflow-x: auto;
        scrollbar-width: none;
    }}

    .tabbar::-webkit-scrollbar {{
        display: none;
    }}

    .tab-button {{
        border: 1px solid var(--border);
        background: rgba(15, 15, 18, 0.72);
        color: var(--muted-foreground);
        border-radius: 8px;
        padding: 7px 10px;
        font: inherit;
        font-size: 11px;
        cursor: pointer;
        white-space: nowrap;
    }}

    .tab-button:hover {{
        color: var(--foreground);
        border-color: rgba(96, 165, 250, 0.45);
    }}

    .tab-button.active {{
        color: var(--foreground);
        background: rgba(96, 165, 250, 0.16);
        border-color: rgba(96, 165, 250, 0.58);
    }}

    .tab-spacer {{
        flex: 1 0 12px;
    }}

    .champion-picker {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        color: var(--muted-foreground);
        font-size: 11px;
        white-space: nowrap;
    }}

    .champion-select {{
        min-width: 210px;
        border: 1px solid var(--border);
        background: rgba(15, 15, 18, 0.82);
        color: var(--foreground);
        border-radius: 8px;
        padding: 7px 10px;
        font: inherit;
        font-size: 11px;
    }}

    .tab-panel {{
        display: none;
    }}

    .tab-panel.active {{
        display: block;
    }}

    .champion-bracket-panel {{
        display: none;
    }}

    .champion-bracket-panel.active {{
        display: block;
    }}

    .legend {{
        display: flex;
        gap: 14px;
        color: var(--muted-foreground);
        margin: 0 0 8px;
        font-size: 12px;
    }}

    .legend-item {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
    }}

    .legend-line {{
        width: 28px;
        height: 0;
        border-top: 2px solid var(--line);
    }}

    .legend-line.drop {{
        border-top: 2px dashed var(--drop);
    }}

    .viewport {{
        border: 1px solid var(--border);
        background: rgba(9, 9, 11, 0.72);
        border-radius: 14px;
        overflow: auto;
        scrollbar-width: none;
        box-shadow: 0 20px 60px rgba(0, 0, 0, 0.25);
    }}

    .viewport::-webkit-scrollbar {{
        display: none;
    }}

    .canvas {{
        position: relative;
        width: {W}px;
        height: {H}px;
        overflow: visible;
        transition: width 160ms ease;
    }}

    .round-header {{
        position: absolute;
        height: 28px;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 5px;
        border-bottom: 1px solid var(--border);
        color: var(--muted-foreground);
        font-size: 10px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        user-select: none;
        cursor: pointer;
        background: transparent;
        font-family: inherit;
        overflow: hidden;
        transition: color 120ms ease, border-color 120ms ease;
    }}

    .round-header:hover {{
        color: var(--foreground);
        background: rgba(255, 255, 255, 0.025);
    }}

    .round-header .count {{
        opacity: 0.55;
        font-weight: 500;
    }}

    .round-toggle.collapsed {{
        color: #fca5a5;
        border-bottom-color: rgba(239, 68, 68, 0.5);
    }}

    .canvas > svg {{
        position: absolute;
        inset: 0;
        pointer-events: none;
        overflow: visible;
    }}

    .advance-line {{
        fill: none;
        stroke: var(--line);
        stroke-width: 1.6;
    }}

    .drop-line {{
        fill: none;
        stroke: var(--drop);
        stroke-width: 1.4;
    }}

    .round-line.is-collapsed {{
        display: none;
    }}

    .match-card {{
        position: absolute;
        border: 1px solid var(--border);
        background: rgba(15, 15, 18, 0.96);
        border-radius: 7px;
        overflow: hidden;
        transition: background 120ms ease, border-color 120ms ease, transform 120ms ease, opacity 120ms ease;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.22);
    }}

    .match-card:hover {{
        background: var(--card-hover);
        border-color: rgba(96, 165, 250, 0.55);
        transform: translateY(-1px);
        z-index: 10;
        cursor: pointer;
    }}

    .match-card.is-collapsed {{
        display: none;
    }}

    .team-row {{
        height: 23px;
        display: grid;
        grid-template-columns: 14px 24px minmax(0, 1fr) 18px 24px;
        align-items: center;
        gap: 4px;
        padding: 0 6px;
        color: var(--foreground);
    }}

    .winner-row {{
        background: var(--winner-bg);
        color: var(--winner-fg);
        font-weight: 700;
    }}

    .divider {{
        height: 1px;
        background: var(--border);
    }}

    .seed {{
        color: var(--muted-foreground);
        font-size: 9px;
        opacity: 0.8;
    }}

    .name {{
        overflow: hidden;
        white-space: nowrap;
        text-overflow: ellipsis;
        font-size: 10px;
    }}

    .score {{
        text-align: right;
        font-size: 10px;
        font-weight: 700;
    }}
    
    .rating {{
        text-align: right;
        color: var(--muted-foreground);
        font-size: 8px;
        opacity: 0.78;
    }}


    .team-prob {{
        display: inline-flex;
        justify-content: flex-end;
        align-items: center;
        min-width: 18px;
        color: var(--muted-foreground);
        font-size: 9px;
        font-weight: 600;
        opacity: 0.74;
    }}

    .note {{
        margin-top: 10px;
        color: var(--muted-foreground);
        font-size: 12px;
    }}

    .data-panel {{
        border: 1px solid var(--border);
        background: rgba(9, 9, 11, 0.72);
        border-radius: 14px;
        overflow: auto;
        scrollbar-width: none;
        box-shadow: 0 20px 60px rgba(0, 0, 0, 0.25);
    }}

    .data-panel::-webkit-scrollbar {{
        display: none;
    }}

    .data-table {{
        width: 100%;
        min-width: 900px;
        border-collapse: collapse;
    }}

    .data-table th,
    .data-table td {{
        border-bottom: 1px solid var(--border);
        padding: 8px 9px;
        text-align: right;
        white-space: nowrap;
    }}

    .data-table thead th {{
        position: sticky;
        top: 0;
        z-index: 1;
        background: #111114;
        color: var(--muted-foreground);
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }}

    .data-table tbody th {{
        text-align: left;
        font-weight: 700;
        color: var(--foreground);
    }}

    .data-table .text-col {{
        text-align: left;
    }}

    .data-table tbody tr:hover {{
        background: rgba(255, 255, 255, 0.025);
    }}

    .data-table .prob-cell {{
        background: rgba(22, 101, 52, var(--heat));
    }}

    .results-model {{
        margin-bottom: 10px;
    }}

    .bayes-panel {{
        border: 1px solid var(--border);
        background: rgba(9, 9, 11, 0.72);
        border-radius: 14px;
        margin-top: 12px;
        padding: 12px;
        box-shadow: 0 20px 60px rgba(0, 0, 0, 0.25);
    }}

    .bayes-title {{
        color: var(--muted-foreground);
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.08em;
        margin-bottom: 10px;
        text-transform: uppercase;
    }}

    .bayes-controls {{
        align-items: end;
        display: grid;
        gap: 10px;
        grid-template-columns: minmax(170px, 1fr) minmax(170px, 1fr) repeat(4, minmax(92px, auto));
    }}

    .bayes-field {{
        display: grid;
        gap: 5px;
        min-width: 0;
    }}

    .bayes-field label,
    .bayes-output .k {{
        color: var(--muted-foreground);
        font-size: 9px;
        font-weight: 800;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }}

    .bayes-field select {{
        border: 1px solid var(--border);
        background: rgba(15, 15, 18, 0.82);
        color: var(--foreground);
        border-radius: 8px;
        min-width: 0;
        padding: 8px 10px;
        font: inherit;
        font-size: 11px;
    }}

    .bayes-output {{
        border-left: 1px solid var(--border);
        display: grid;
        gap: 4px;
        min-height: 42px;
        padding-left: 10px;
    }}

    .bayes-output .v {{
        color: var(--foreground);
        font-size: 13px;
        font-weight: 800;
        font-variant-numeric: tabular-nums;
    }}

    .market-modal {{
        position: fixed;
        inset: 0;
        z-index: 50;
        display: grid;
        place-items: center;
        padding: 18px;
        background: rgba(0, 0, 0, 0.62);
    }}

    .market-modal.hidden {{
        display: none;
    }}

    .market-dialog {{
        width: min(560px, 100%);
        max-height: calc(100vh - 36px);
        overflow: auto;
        border: 1px solid var(--border);
        border-radius: 12px;
        background: #0d0d10;
        box-shadow: 0 24px 90px rgba(0, 0, 0, 0.55);
        padding: 14px;
    }}

    .market-dialog-head {{
        display: flex;
        align-items: start;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 12px;
    }}

    .market-dialog h2 {{
        font-size: 15px;
        margin: 0;
    }}

    .market-dialog p {{
        color: var(--muted-foreground);
        line-height: 1.45;
        margin: 6px 0 0;
    }}

    .market-close {{
        border: 1px solid var(--border);
        background: rgba(15, 15, 18, 0.82);
        border-radius: 7px;
        color: var(--muted-foreground);
        cursor: pointer;
        font: inherit;
        padding: 5px 8px;
    }}

    .market-form {{
        display: grid;
        gap: 10px;
    }}

    .market-field {{
        display: grid;
        gap: 5px;
    }}

    .market-field label,
    .market-choice-title {{
        color: var(--muted-foreground);
        font-size: 9px;
        font-weight: 800;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }}

    .market-field input,
    .market-field select {{
        border: 1px solid var(--border);
        background: rgba(15, 15, 18, 0.82);
        border-radius: 8px;
        color: var(--foreground);
        font: inherit;
        padding: 9px 10px;
    }}

    .market-choice-grid {{
        display: grid;
        gap: 8px;
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }}

    .market-choice {{
        border: 1px solid var(--border);
        background: rgba(15, 15, 18, 0.82);
        border-radius: 8px;
        cursor: pointer;
        display: grid;
        gap: 4px;
        padding: 9px;
    }}

    .market-choice input {{
        margin: 0;
    }}

    .market-choice span {{
        font-weight: 800;
    }}

    .market-choice small {{
        color: var(--muted-foreground);
        line-height: 1.3;
    }}

    .market-actions {{
        align-items: center;
        display: flex;
        justify-content: flex-end;
        gap: 8px;
        margin-top: 4px;
    }}

    .market-error {{
        color: #fca5a5;
        font-size: 11px;
        min-height: 16px;
    }}

    .market-lock {{
        border: 1px solid rgba(250, 204, 21, 0.28);
        background: rgba(250, 204, 21, 0.08);
        border-radius: 8px;
        color: #fde68a;
        padding: 8px;
        line-height: 1.45;
    }}

    @media (max-width: 980px) {{
        .bayes-controls {{
            grid-template-columns: 1fr 1fr;
        }}

        .market-choice-grid {{
            grid-template-columns: 1fr;
        }}

        .bayes-output {{
            border-left: 0;
            border-top: 1px solid var(--border);
            padding-left: 0;
            padding-top: 8px;
        }}
    }}

    .curve-section {{
        margin-top: 12px;
    }}

    .curve-section-title {{
        color: var(--muted-foreground);
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.08em;
        margin: 0 0 8px;
        text-transform: uppercase;
    }}

    .curve-grid {{
        display: grid;
        gap: 8px;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }}

    .curve-card {{
        border: 1px solid var(--border);
        border-radius: 8px;
        background: rgba(9, 9, 11, 0.64);
        padding: 8px;
        min-width: 0;
    }}

    .curve-title {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        color: var(--foreground);
        font-size: 10px;
        font-weight: 800;
        margin-bottom: 4px;
    }}

    .curve-title span:first-child {{
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }}

    .curve-title span:last-child {{
        color: var(--muted-foreground);
        flex: 0 0 auto;
        font-variant-numeric: tabular-nums;
    }}

    .curve-svg {{
        display: block;
        height: 70px;
        overflow: visible;
        width: 100%;
    }}

    .curve-baseline {{
        fill: none;
        stroke: rgba(255, 255, 255, 0.12);
        stroke-width: 1;
    }}

    .curve-area {{
        fill: rgba(22, 101, 52, 0.34);
    }}

    .curve-line {{
        fill: none;
        stroke: #74d99f;
        stroke-linecap: round;
        stroke-linejoin: round;
        stroke-width: 2;
    }}

    .curve-labels {{
        display: grid;
        grid-template-columns: repeat(8, minmax(0, 1fr));
        color: var(--muted-foreground);
        font-size: 8px;
        gap: 2px;
        line-height: 1;
        margin-top: 2px;
        text-align: center;
    }}

    .empty-state,
    .empty-cell {{
        padding: 28px;
        color: var(--muted-foreground);
        text-align: left;
    }}
</style>
</head>
<body>
<div class="page">
    <div class="topbar">
        <div class="title">
            <div class="title-row">
                <h1>Predicted Cambridge BC Final Bracket</h1>
                <button class="market-button" type="button" data-market-auth>Sign in to Polymarket</button>
            </div>
            <div class="market-status" data-market-status>Prediction market loading...</div>
        </div>

        <div class="stats">
            <div class="pill">
                <div class="k">Champion</div>
                <div class="v">{esc(display_name(champion))}</div>
            </div>
            <div class="pill">
                <div class="k">Common bracket</div>
                <div class="v">{best_bracket_count:,} / {num_simulations:,}</div>
            </div>
            <div class="pill">
                <div class="k">Observed freq</div>
                <div class="v">{bracket_prob_percent:.3f}%</div>
            </div>
            <div class="pill">
                <div class="k">Stored matches</div>
                <div class="v">{match_db_count:,} (+{new_match_count:,})</div>
            </div>
            <div class="pill">
                <div class="k">Updated</div>
                <div class="v">{esc(generated_at)}</div>
            </div>
        </div>
    </div>

    <div class="tabbar" role="tablist" aria-label="Tournament views">
        <button class="tab-button active" type="button" data-tab="bracket">Bracket</button>
        <button class="tab-button" type="button" data-tab="alt-bracket">Different Champion</button>
        <button class="tab-button" type="button" data-tab="results">Results</button>
        <button class="tab-button" type="button" data-tab="h2h">Head To Head</button>
        <span class="tab-spacer"></span>
        <label class="champion-picker">
            Champion
            <select class="champion-select" data-champion-select{champion_select_disabled}>
                {champion_options}
            </select>
        </label>
    </div>

    <section class="tab-panel active" data-panel="bracket">
        <div class="stats">
            <div class="pill">
                <div class="k">Champion</div>
                <div class="v">{esc(display_name(champion))}</div>
            </div>
            <div class="pill">
                <div class="k">Observed freq</div>
                <div class="v">{bracket_prob_percent:.3f}%</div>
            </div>
        </div>
        {primary_bracket_panel}
    </section>

    <section class="tab-panel" data-panel="alt-bracket">
        {champion_panels}
    </section>

    <section class="tab-panel" data-panel="results">
        <div class="stats results-model">
            <div class="pill">
                <div class="k">Results model</div>
                <div class="v">{esc(model_display_value("name", "Bayesian"))}</div>
            </div>
            <div class="pill">
                <div class="k">Prior</div>
                <div class="v">{esc(model_display_value("prior", "Elo"))}</div>
            </div>
            <div class="pill">
                <div class="k">Virtual games</div>
                <div class="v">{esc(model_display_value("prior_games", "0"))}</div>
            </div>
        </div>
        <div class="data-panel">
            <table class="data-table">
                <thead>
                    <tr>
                        <th class="text-col">Team</th>
                        <th>Win%</th>
                        <th>Top3</th>
                        <th>1st</th>
                        <th>2nd</th>
                        <th>3rd</th>
                        <th>4th</th>
                        <th>5-6th</th>
                        <th>7-8th</th>
                        <th>9-12th</th>
                        <th>13-16th</th>
                    </tr>
                </thead>
                <tbody>
                    {placement_table_rows}
                </tbody>
            </table>
        </div>
        <div class="bayes-panel">
            <div class="bayes-title">Bayesian Probability Calculator</div>
            <div class="bayes-controls">
                <div class="bayes-field">
                    <label for="bayes-team-a">Team A</label>
                    <select id="bayes-team-a" data-bayes-team-a>
                        {calculator_team_options}
                    </select>
                </div>
                <div class="bayes-field">
                    <label for="bayes-team-b">Team B</label>
                    <select id="bayes-team-b" data-bayes-team-b>
                        {calculator_team_options}
                    </select>
                </div>
                <div class="bayes-output">
                    <div class="k">Observed games</div>
                    <div class="v" data-bayes-games>--</div>
                </div>
                <div class="bayes-output">
                    <div class="k">Elo prior</div>
                    <div class="v" data-bayes-prior>--</div>
                </div>
                <div class="bayes-output">
                    <div class="k">Game win%</div>
                    <div class="v" data-bayes-game>--</div>
                </div>
                <div class="bayes-output">
                    <div class="k">BO9 win%</div>
                    <div class="v" data-bayes-bo9>--</div>
                </div>
            </div>
        </div>
        <div class="curve-section">
            <div class="curve-section-title">Placement Probability Curves</div>
            <div class="curve-grid">
                {placement_curve_cards}
            </div>
        </div>
    </section>

    <section class="tab-panel" data-panel="h2h">
        <div class="data-panel">
            <table class="data-table">
                <thead>
                    <tr>
                        <th class="text-col">Team A</th>
                        <th class="text-col">Team B</th>
                        <th>Games</th>
                        <th>Game Win% A</th>
                        <th>BO9 Win% A</th>
                    </tr>
                </thead>
                <tbody>
                    {h2h_table_rows}
                </tbody>
            </table>
        </div>
    </section>
</div>

<div class="market-modal hidden" data-account-modal>
    <div class="market-dialog">
        <div class="market-dialog-head">
            <div>
                <h2>Create Axionite Account</h2>
                <p>New users receive 100 Axionite now and unlock the remaining 900 after placing the assigned team bet.</p>
            </div>
            <button class="market-close" type="button" data-close-account>Close</button>
        </div>
        <form class="market-form" data-account-form>
            <div class="market-field">
                <label for="market-display-name">Display name</label>
                <input id="market-display-name" name="displayName" maxlength="32" required>
            </div>
            <div class="market-field">
                <label for="market-affiliation">Team affiliation</label>
                <select id="market-affiliation" name="affiliation">
                    {affiliation_options}
                </select>
            </div>
            <div class="market-error" data-account-error></div>
            <div class="market-actions">
                <button class="market-primary" type="submit">Create account</button>
            </div>
        </form>
    </div>
</div>

<div class="market-modal hidden" data-bet-modal>
    <div class="market-dialog">
        <div class="market-dialog-head">
            <div>
                <h2 data-bet-title>Place Axionite Bet</h2>
                <p data-bet-subtitle></p>
            </div>
            <button class="market-close" type="button" data-close-bet>Close</button>
        </div>
        <form class="market-form" data-bet-form>
            <div class="market-lock hidden" data-bet-lock></div>
            <div class="market-field">
                <label for="bet-team">Team</label>
                <select id="bet-team" name="selectedTeam" data-bet-team></select>
            </div>
            <div>
                <div class="market-choice-title">Outcome</div>
                <div class="market-choice-grid">
                    <label class="market-choice">
                        <input type="radio" name="outcome" value="above" required>
                        <span>Above predicted</span>
                        <small data-choice-above></small>
                    </label>
                    <label class="market-choice">
                        <input type="radio" name="outcome" value="below" required>
                        <span>Below predicted</span>
                        <small data-choice-below></small>
                    </label>
                    <label class="market-choice">
                        <input type="radio" name="outcome" value="equal" required>
                        <span>Equal to predicted</span>
                        <small data-choice-equal></small>
                    </label>
                    <label class="market-choice">
                        <input type="radio" name="outcome" value="no_match" required>
                        <span>Match does not occur</span>
                        <small>This node is not part of the final realized bracket.</small>
                    </label>
                </div>
            </div>
            <div class="market-field">
                <label for="bet-amount">Amount</label>
                <input id="bet-amount" name="amount" type="number" min="1" step="1" value="100" data-bet-amount required>
            </div>
            <div class="market-error" data-bet-error></div>
            <div class="market-actions">
                <button class="market-secondary" type="button" data-close-bet-secondary>Cancel</button>
                <button class="market-primary" type="submit">Place bet</button>
            </div>
        </form>
    </div>
</div>

<script>
(() => {{
    const tabButtons = document.querySelectorAll('.tab-button');
    const tabPanels = document.querySelectorAll('.tab-panel');
    const championSelect = document.querySelector('[data-champion-select]');
    const championPanels = document.querySelectorAll('.champion-bracket-panel');
    const toggles = document.querySelectorAll('.round-toggle');
    const bayesianMatchups = {bayesian_matchup_json};
    const bayesTeamA = document.querySelector('[data-bayes-team-a]');
    const bayesTeamB = document.querySelector('[data-bayes-team-b]');
    const bayesGames = document.querySelector('[data-bayes-games]');
    const bayesPrior = document.querySelector('[data-bayes-prior]');
    const bayesGame = document.querySelector('[data-bayes-game]');
    const bayesBo9 = document.querySelector('[data-bayes-bo9]');
    const seededTeams = {market_seed_json};
    const marketAuthButton = document.querySelector('[data-market-auth]');
    const marketStatus = document.querySelector('[data-market-status]');
    const accountModal = document.querySelector('[data-account-modal]');
    const accountForm = document.querySelector('[data-account-form]');
    const accountError = document.querySelector('[data-account-error]');
    const betModal = document.querySelector('[data-bet-modal]');
    const betForm = document.querySelector('[data-bet-form]');
    const betTitle = document.querySelector('[data-bet-title]');
    const betSubtitle = document.querySelector('[data-bet-subtitle]');
    const betTeam = document.querySelector('[data-bet-team]');
    const betAmount = document.querySelector('[data-bet-amount]');
    const betError = document.querySelector('[data-bet-error]');
    const betLock = document.querySelector('[data-bet-lock]');
    const choiceAbove = document.querySelector('[data-choice-above]');
    const choiceBelow = document.querySelector('[data-choice-below]');
    const choiceEqual = document.querySelector('[data-choice-equal]');
    const marketError = new URLSearchParams(window.location.search).get('market_error');
    let marketSession = null;
    let activeMarket = null;

    function setTab(tab) {{
        tabButtons.forEach(item => item.classList.toggle('active', item.dataset.tab === tab));
        tabPanels.forEach(panel => {{
            panel.classList.toggle('active', panel.dataset.panel === tab);
        }});
    }}

    tabButtons.forEach(btn => {{
        btn.addEventListener('click', () => {{
            setTab(btn.dataset.tab);
        }});
    }});

    if (championSelect) {{
        championSelect.addEventListener('change', () => {{
            const selected = championSelect.value;
            championPanels.forEach(panel => {{
                panel.classList.toggle('active', panel.dataset.championPanel === selected);
            }});
            setTab('alt-bracket');
        }});
    }}

    function fmtPct(value) {{
        return `${{Number(value || 0).toFixed(1)}}%`;
    }}

    function updateBayesianCalculator() {{
        if (!bayesTeamA || !bayesTeamB) {{
            return;
        }}
        if (bayesTeamA.value === bayesTeamB.value) {{
            const option = Array.from(bayesTeamB.options).find(item => item.value !== bayesTeamA.value);
            if (option) {{
                bayesTeamB.value = option.value;
            }}
        }}
        const row = bayesianMatchups.find(item => (
            item.team_a === bayesTeamA.value && item.team_b === bayesTeamB.value
        ));
        if (!row) {{
            return;
        }}
        bayesGames.textContent = `${{row.games_a}}-${{row.games_b}}`;
        bayesPrior.textContent = fmtPct(row.prior_game_pct_a);
        bayesGame.textContent = fmtPct(row.bayes_game_pct_a);
        bayesBo9.textContent = fmtPct(row.bayes_bo9_pct_a);
    }}

    if (bayesTeamA && bayesTeamB) {{
        if (bayesTeamB.options.length > 1) {{
            bayesTeamB.selectedIndex = 1;
        }}
        bayesTeamA.addEventListener('change', updateBayesianCalculator);
        bayesTeamB.addEventListener('change', updateBayesianCalculator);
        updateBayesianCalculator();
    }}

    function showModal(modal) {{
        modal?.classList.remove('hidden');
    }}

    function hideModal(modal) {{
        modal?.classList.add('hidden');
    }}

    function teamDisplay(teamId) {{
        return seededTeams.find(team => team.id === teamId)?.name || teamId;
    }}

    function accountNeedsUnlock(account) {{
        return Boolean(account && !account.requiredBetPlaced);
    }}

    function renderMarketStatus() {{
        if (marketError === 'discord_client_missing' || marketError === 'discord_env_missing') {{
            marketAuthButton.textContent = 'Discord auth not configured';
            marketStatus.textContent = 'Set DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET in Netlify environment variables.';
            marketAuthButton.disabled = true;
            return;
        }}
        const account = marketSession?.account;
        if (!marketSession?.authenticated) {{
            if (marketSession?.discordConfigured === false) {{
                marketAuthButton.textContent = 'Discord auth not configured';
                marketStatus.textContent = 'Set DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET in Netlify environment variables.';
                marketAuthButton.disabled = true;
                return;
            }}
            marketAuthButton.disabled = false;
            marketAuthButton.textContent = 'Sign in to Polymarket';
            marketStatus.textContent = 'Sign in with Discord to trade with Axionite.';
            return;
        }}
        marketAuthButton.disabled = false;
        if (marketSession.storageConfigured === false) {{
            marketAuthButton.textContent = 'Market storage unavailable';
            marketStatus.textContent = marketSession.storageError || 'Netlify Blobs is not available to the session function.';
            marketAuthButton.disabled = true;
            return;
        }}
        if (!account) {{
            marketAuthButton.textContent = 'Create Axionite account';
            marketStatus.textContent = `Signed in as ${{marketSession.user?.username || 'Discord user'}}. Create an account to trade.`;
            return;
        }}
        marketAuthButton.textContent = `${{account.displayName}} · ${{account.availableBalance}} AX`;
        if (accountNeedsUnlock(account)) {{
            marketStatus.textContent = `Place a 100 AX bet on ${{teamDisplay(account.assignedTeam)}} to unlock 900 additional Axionite.`;
        }} else {{
            marketStatus.textContent = `${{account.availableBalance}} AX available · ${{account.lockedBalance || 0}} AX locked`;
        }}
    }}

    async function fetchJSON(url, options = {{}}) {{
        const response = await fetch(url, {{
            headers: {{ 'content-type': 'application/json', ...(options.headers || {{}}) }},
            ...options,
        }});
        const data = await response.json().catch(() => ({{}}));
        if (!response.ok) {{
            throw new Error(data.error || `Request failed (${{response.status}})`);
        }}
        return data;
    }}

    async function refreshMarketSession() {{
        try {{
            marketSession = await fetchJSON('/api/session');
        }} catch (error) {{
            marketSession = {{ authenticated: false, backendUnavailable: true }};
            marketStatus.textContent = 'Prediction market backend is not configured yet.';
        }}
        renderMarketStatus();
        if (marketSession?.authenticated && !marketSession.account && !marketSession.backendUnavailable) {{
            showModal(accountModal);
        }}
    }}

    function requireMarketAccount() {{
        if (!marketSession?.authenticated) {{
            window.location.href = '/api/auth-start';
            return false;
        }}
        if (!marketSession.account) {{
            showModal(accountModal);
            return false;
        }}
        return true;
    }}

    function updateBetChoiceText() {{
        if (!activeMarket || !betTeam) {{
            return;
        }}
        const selected = betTeam.value;
        const predicted = selected === activeMarket.teamA ? activeMarket.predA : activeMarket.predB;
        choiceAbove.textContent = `${{teamDisplay(selected)}} wins more than ${{predicted}} games.`;
        choiceBelow.textContent = `${{teamDisplay(selected)}} wins fewer than ${{predicted}} games.`;
        choiceEqual.textContent = `${{teamDisplay(selected)}} wins exactly ${{predicted}} games.`;
    }}

    function openBetForCard(card) {{
        if (!requireMarketAccount()) {{
            return;
        }}
        const account = marketSession.account;
        activeMarket = {{
            marketId: card.dataset.marketId,
            label: card.dataset.marketLabel,
            teamA: card.dataset.teamA,
            teamB: card.dataset.teamB,
            predA: Number(card.dataset.predA || 0),
            predB: Number(card.dataset.predB || 0),
        }};
        betTitle.textContent = `${{activeMarket.label}} · ${{teamDisplay(activeMarket.teamA)}} vs ${{teamDisplay(activeMarket.teamB)}}`;
        betSubtitle.textContent = `Predicted score: ${{activeMarket.predA}}-${{activeMarket.predB}}. Markets settle against the selected team's final game wins if this match occurs.`;
        betTeam.innerHTML = [activeMarket.teamA, activeMarket.teamB]
            .map(team => `<option value="${{team}}">${{teamDisplay(team)}}</option>`)
            .join('');
        const locked = accountNeedsUnlock(account);
        const assignedInMatch = [activeMarket.teamA, activeMarket.teamB].includes(account.assignedTeam);
        betLock.classList.toggle('hidden', !locked);
        betAmount.value = locked ? '100' : '25';
        betAmount.disabled = locked;
        if (locked) {{
            betLock.textContent = assignedInMatch
                ? `Required unlock bet: place 100 AX on ${{teamDisplay(account.assignedTeam)}} in this match.`
                : `Locked: choose a match involving ${{teamDisplay(account.assignedTeam)}} to unlock your remaining 900 AX.`;
            betTeam.value = account.assignedTeam;
            betTeam.disabled = true;
        }} else {{
            betTeam.disabled = false;
        }}
        betForm.querySelectorAll('input[name="outcome"]').forEach(input => {{
            input.checked = false;
            input.disabled = locked && !assignedInMatch;
        }});
        betForm.querySelector('button[type="submit"]').disabled = locked && !assignedInMatch;
        betError.textContent = '';
        updateBetChoiceText();
        showModal(betModal);
    }}

    marketAuthButton?.addEventListener('click', () => {{
        if (!marketSession?.authenticated) {{
            window.location.href = '/api/auth-start';
            return;
        }}
        if (!marketSession.account) {{
            showModal(accountModal);
            return;
        }}
        renderMarketStatus();
    }});

    document.querySelectorAll('[data-close-account]').forEach(button => {{
        button.addEventListener('click', () => hideModal(accountModal));
    }});
    document.querySelectorAll('[data-close-bet], [data-close-bet-secondary]').forEach(button => {{
        button.addEventListener('click', () => hideModal(betModal));
    }});

    accountForm?.addEventListener('submit', async (event) => {{
        event.preventDefault();
        accountError.textContent = '';
        const form = new FormData(accountForm);
        try {{
            marketSession = await fetchJSON('/api/account', {{
                method: 'POST',
                body: JSON.stringify({{
                    displayName: form.get('displayName'),
                    affiliation: form.get('affiliation'),
                }}),
            }});
            hideModal(accountModal);
            renderMarketStatus();
        }} catch (error) {{
            accountError.textContent = error.message;
        }}
    }});

    betTeam?.addEventListener('change', updateBetChoiceText);

    betForm?.addEventListener('submit', async (event) => {{
        event.preventDefault();
        betError.textContent = '';
        const form = new FormData(betForm);
        try {{
            marketSession = await fetchJSON('/api/bet', {{
                method: 'POST',
                body: JSON.stringify({{
                    ...activeMarket,
                    selectedTeam: form.get('selectedTeam'),
                    outcome: form.get('outcome'),
                    amount: Number(betAmount.value),
                }}),
            }});
            hideModal(betModal);
            renderMarketStatus();
        }} catch (error) {{
            betError.textContent = error.message;
        }}
    }});

    document.addEventListener('click', event => {{
        const card = event.target.closest('.match-card');
        if (card) {{
            openBetForCard(card);
        }}
    }});

    refreshMarketSession();

    function updateRound(scope, roundKey, collapsed) {{
        scope.querySelectorAll(`.match-card[data-round="${{roundKey}}"]`)
            .forEach(el => el.classList.toggle('is-collapsed', collapsed));

        scope.querySelectorAll('.round-line')
            .forEach(el => {{
                const src = el.dataset.srcRound;
                const dst = el.dataset.dstRound;
                const hide = src === roundKey || dst === roundKey;
                if (hide) {{
                    el.classList.toggle('is-collapsed', collapsed);
                }}
            }});

        scope.querySelectorAll(`.round-toggle[data-round="${{roundKey}}"]`)
            .forEach(el => el.classList.toggle('collapsed', collapsed));
    }}

    toggles.forEach(btn => {{
        btn.addEventListener('click', () => {{
            const scope = btn.closest('.canvas');
            const roundKey = btn.dataset.round;
            const collapsed = !btn.classList.contains('collapsed');
            updateRound(scope, roundKey, collapsed);
        }});
    }});
}})();
</script>
</body>
</html>
"""
    out_path.write_text(html_text, encoding="utf-8")


# ── Netlify publishing ──────────────────────────────────────────────────────


def _git(repo_path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        text=True,
        capture_output=True,
        check=False,
    )


def _git_check(repo_path: Path, args: list[str]) -> str:
    result = _git(repo_path, args)
    if result.returncode != 0:
        command = "git " + " ".join(args)
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{command} failed in {repo_path}: {detail}")
    return result.stdout.strip()


def publish_to_netlify_repo(
    html_path: Path,
    match_db_path: Path,
    generated_at: str,
) -> None:
    """
    Copy generated artifacts into a Netlify-connected repo and push.

    Configure with:
      TOURNAMENT_PUBLISH_REPO=/path/to/static-site-repo
      TOURNAMENT_PUBLISH_REMOTE=origin
      TOURNAMENT_PUBLISH_BRANCH=main
    """
    repo_value = os.environ.get("TOURNAMENT_PUBLISH_REPO", "").strip()
    if not repo_value:
        print("\nNetlify publish skipped (set TOURNAMENT_PUBLISH_REPO to enable).")
        return

    repo_path = Path(repo_value).expanduser().resolve()
    if not (repo_path / ".git").exists():
        raise RuntimeError(f"TOURNAMENT_PUBLISH_REPO is not a git checkout: {repo_path}")

    html_dest = repo_path / os.environ.get("TOURNAMENT_PUBLISH_HTML", "index.html")
    db_dest = repo_path / os.environ.get("TOURNAMENT_PUBLISH_MATCH_DB", "matches.json")

    html_dest.parent.mkdir(parents=True, exist_ok=True)
    db_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(html_path, html_dest)
    shutil.copy2(match_db_path, db_dest)

    tracked_paths = [
        str(html_dest.relative_to(repo_path)),
        str(db_dest.relative_to(repo_path)),
    ]
    _git_check(repo_path, ["add", *tracked_paths])

    status = _git_check(repo_path, ["status", "--porcelain", "--", *tracked_paths])
    if not status:
        print("\nNetlify publish skipped (generated artifacts unchanged).")
        return

    commit_message = os.environ.get(
        "TOURNAMENT_PUBLISH_COMMIT_MESSAGE",
        f"Update tournament projection {generated_at}",
    )
    _git_check(repo_path, ["commit", "-m", commit_message, "--", *tracked_paths])

    remote = os.environ.get("TOURNAMENT_PUBLISH_REMOTE", "origin")
    branch = os.environ.get("TOURNAMENT_PUBLISH_BRANCH", "").strip()
    if not branch:
        branch = _git_check(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if not branch or branch == "HEAD":
        branch = "main"

    _git_check(repo_path, ["push", remote, branch])
    print(f"\nPublished Netlify artifacts to {repo_path} and pushed {remote}/{branch}.")


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

    # Force seeds exactly as listed in TOURNAMENT_TEAMS.
    # Do NOT sort by ELO.
    teams_sorted = list(TOURNAMENT_TEAMS)

    print("\nSeeding (forced):")
    for i, team in enumerate(teams_sorted, 1):
        r = ratings.get(team, 1500)
        print(f"  #{i:>2}  {team:35s}  {r:>7.0f}")

    # Step 2: Update persistent match DB from the latest recent-match feeds
    print()
    stored_matches, new_match_count, match_db_count = update_match_db(
        MATCH_DB_PATH,
        TOURNAMENT_TEAMS,
        team_ids,
        recent_limit=RECENT_MATCH_LIMIT,
    )

    # Step 3: Build head-to-head matrix from the persistent DB
    print()
    h2h = build_h2h_matrix(TOURNAMENT_TEAMS, team_ids, stored_matches)

    # Step 4: Build win probability matrices
    game_win_probs = build_game_win_prob_matrix(TOURNAMENT_TEAMS, ratings, h2h)
    win_probs = build_win_prob_matrix(TOURNAMENT_TEAMS, ratings, h2h)
    results_game_win_probs = build_bayesian_game_win_prob_matrix(TOURNAMENT_TEAMS, ratings, h2h)
    results_win_probs = build_bo_win_prob_matrix_from_games(results_game_win_probs)
    bayesian_rows = build_bayesian_matchup_rows(
        TOURNAMENT_TEAMS,
        ratings,
        h2h,
        results_game_win_probs,
    )

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

    h2h_rows: list[dict[str, object]] = []
    for (a, b), (ga, gb) in sorted(h2h.items(), key=lambda x: (-(x[1][0] + x[1][1]), x[0])):
        total = ga + gb
        if total <= 0:
            continue
        p_game = ga / total
        p_bo9 = bo_win_probability(p_game)
        h2h_rows.append({
            "team_a": a,
            "team_b": b,
            "games_a": ga,
            "games_b": gb,
            "game_pct_a": p_game * 100,
            "bo9_pct_a": p_bo9 * 100,
        })

    # Step 5: Run Monte Carlo simulations.
    # The results table uses Bayesian-smoothed probabilities. Bracket paths stay
    # on the raw BO9 matrix so the most-likely bracket remains unchanged.
    print(f"\nRunning {NUM_SIMULATIONS:,} simulations...")
    print(f"  Results model: Bayesian Elo prior ({BAYESIAN_PRIOR_GAMES:g} virtual games)")

    placement_counts: dict[str, dict[int, int]] = {
        team: defaultdict(int) for team in TOURNAMENT_TEAMS
    }
    win_counts: dict[str, int] = defaultdict(int)
    top3_counts: dict[str, int] = defaultdict(int)
    raw_win_counts: dict[str, int] = defaultdict(int)
    bracket_counts: dict[tuple[str, ...], int] = defaultdict(int)

    bracket_rng = random.Random(42)
    results_rng = random.Random(4242)

    for sim in range(NUM_SIMULATIONS):
        if sim > 0 and sim % 250_000 == 0:
            print(f"  ... {sim:,} / {NUM_SIMULATIONS:,}")

        raw_placements, trace = simulate_double_elim(teams_sorted, win_probs, bracket_rng)
        for team, place in raw_placements.items():
            if place == 1:
                raw_win_counts[team] += 1

        results_placements, _ = simulate_double_elim(teams_sorted, results_win_probs, results_rng)
        for team, place in results_placements.items():
            placement_counts[team][place] += 1
            if place == 1:
                win_counts[team] += 1
            if place <= 3:
                top3_counts[team] += 1

        # Track bracket by tuple of winners only
        bracket_key = tuple(w for _, _, w in trace)
        bracket_counts[bracket_key] += 1

    # Step 6: Display results
    print("\n" + "=" * 70)
    print("  RESULTS (BAYESIAN)")
    print("=" * 70)

    # Sort by win probability
    sorted_teams = sorted(TOURNAMENT_TEAMS, key=lambda t: -win_counts.get(t, 0) * NUM_SIMULATIONS - ratings.get(t, 1500))

    print(f"\n{'Team':35s} {'Win%':>7} {'Top3':>7} {'1st':>7} {'2nd':>7} "
          f"{'3rd':>7} {'4th':>7} {'5-6th':>7} {'7-8th':>7} {'9-12th':>7} {'13-16th':>7}")
    print("-" * 120)

    placement_rows: list[dict[str, object]] = []
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

        placement_rows.append({
            "team": team,
            "win_pct": win_pct,
            "top3_pct": top3_pct,
            "first": first,
            "second": second,
            "third": third,
            "fourth": fourth,
            "fifth_sixth": fifth,
            "seventh_eighth": seventh,
            "ninth_twelfth": ninth,
            "thirteenth_sixteenth": thirteenth,
        })
        print(f"{team:35s} {win_pct:>6.1f}% {top3_pct:>6.1f}% {first:>6.1f}% {second:>6.1f}% "
              f"{third:>6.1f}% {fourth:>6.1f}% {fifth:>6.1f}% {seventh:>6.1f}% "
              f"{ninth:>6.1f}% {thirteenth:>6.1f}%")

    # Step 7: Find and draw the most common bracket
    print("\n" + "=" * 70)
    print("  MOST COMMON BRACKET")
    print("=" * 70)

    # Find the most common bracket
    best_bracket_key, best_bracket_count = max(bracket_counts.items(), key=lambda x: x[1])
    bracket_prob = best_bracket_count / NUM_SIMULATIONS * 100
    print(f"\n  Occurred {best_bracket_count:,} / {NUM_SIMULATIONS:,} times ({bracket_prob:.3f}%)")

    bracket_matches = replay_bracket_key(best_bracket_key, teams_sorted, win_probs)
    has_reset = any(label == "GF-Reset" for label, *_ in bracket_matches)
    best_champion = bracket_champion_key(best_bracket_key)

    print("\nCalculating most likely different-champion brackets from winrates...")
    calculated_brackets = calculated_most_likely_brackets_by_champion(teams_sorted, win_probs)
    placement_win_pct_by_team = {
        team: raw_win_counts.get(team, 0) / NUM_SIMULATIONS * 100
        for team in TOURNAMENT_TEAMS
    }

    champion_bracket_entries: list[dict[str, object]] = []
    for champion_name in teams_sorted:
        if champion_name == best_champion:
            continue
        calculated = calculated_brackets.get(champion_name)
        if not calculated:
            continue
        path_probability = float(calculated.get("probability") or 0.0)
        matches = calculated.get("matches") if isinstance(calculated.get("matches"), list) else []
        champion_bracket_entries.append({
            "champion": champion_name,
            "source": "calculated",
            "count": 0,
            "observed_pct": placement_win_pct_by_team.get(champion_name, 0.0),
            "path_prob_pct": path_probability * 100,
            "matches": matches,
        })
    champion_bracket_entries.sort(
        key=lambda entry: (
            -float(entry.get("observed_pct") or 0.0),
            -float(entry.get("path_prob_pct") or 0.0),
            str(entry.get("champion") or ""),
        )
    )

    alternate_bracket_matches = (
        champion_bracket_entries[0]["matches"]
        if champion_bracket_entries
        else []
    )
    alternate_bracket_count = (
        int(champion_bracket_entries[0]["count"])
        if champion_bracket_entries
        else 0
    )

    # Compute overall bracket probability (product of each match probability)
    total_bracket_prob = 1.0
    for _, _, _, _, p in bracket_matches:
        total_bracket_prob *= p

    generated_at = utc_now_iso()
    html_out = HTML_OUT_PATH
    write_bracket_html(
        bracket_matches=bracket_matches,
        out_path=html_out,
        best_bracket_count=best_bracket_count,
        num_simulations=NUM_SIMULATIONS,
        bracket_prob_percent=bracket_prob,
        total_bracket_prob=total_bracket_prob,
        match_db_count=match_db_count,
        new_match_count=new_match_count,
        generated_at=generated_at,
        ratings=ratings,
        win_probs=win_probs,
        game_win_probs=game_win_probs,
        seed_map={team: idx + 1 for idx, team in enumerate(teams_sorted)},
        alternate_bracket_matches=alternate_bracket_matches,
        alternate_bracket_count=alternate_bracket_count,
        champion_brackets=champion_bracket_entries,
        placement_rows=placement_rows,
        h2h_rows=h2h_rows,
        results_model={
            "name": "Bayesian",
            "prior": "Elo",
            "prior_games": BAYESIAN_PRIOR_GAMES,
        },
        bayesian_matchup_rows=bayesian_rows,
    )
    print(f"\nVisual bracket written to: {html_out.resolve()}")
    publish_to_netlify_repo(html_out, MATCH_DB_PATH, generated_at)

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
