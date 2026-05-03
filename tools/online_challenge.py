#!/usr/bin/env python3
"""
Online Challenge — submit a bot and play unrated matches against specified opponents.

Submits the bot via ``cambc submit``, then challenges each opponent team via
``cambc unrated``, polls match results, and reports win rates.

Usage::

    python online_challenge.py Artemis_v0 --opponents TEAM_ID_1 TEAM_ID_2
    python online_challenge.py bots/Artemis_v0 --opponents TEAM_ID_1 --rounds 3
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path


# ── Data ───────────────────────────────────────────────────────────────────────

MATCH_ID_RE = re.compile(r"Match ID:\s*([0-9a-f-]+)", re.IGNORECASE)
SCORE_RE = re.compile(r"Score:\s*(\d+)-(\d+)")
STATUS_RE = re.compile(r"Status:\s*(\S+)")
TEAM_A_RE = re.compile(r"Team A:\s*.+?\(([0-9a-f-]+)\)")
TEAM_B_RE = re.compile(r"Team B:\s*.+?\(([0-9a-f-]+)\)")
WINNER_LINE_RE = re.compile(r"<-\s*winner", re.IGNORECASE)

GAMES_PER_MATCH = 5
POLL_INTERVAL = 15  # seconds between polling
COOLDOWN = 310  # seconds between challenges to the same opponent (5 min + buffer)


@dataclass
class MatchOutcome:
    match_id: str
    opponent_id: str
    our_score: int = 0
    their_score: int = 0
    error: bool = False
    error_msg: str = ""

    @property
    def total_games(self) -> int:
        return self.our_score + self.their_score

    @property
    def complete(self) -> bool:
        return self.total_games >= GAMES_PER_MATCH or self.error

    @property
    def win_rate(self) -> float:
        if self.total_games == 0:
            return 0.0
        return self.our_score / self.total_games


@dataclass
class OpponentStats:
    opponent_id: str
    matches: list[MatchOutcome] = field(default_factory=list)

    @property
    def total_wins(self) -> int:
        return sum(m.our_score for m in self.matches if not m.error)

    @property
    def total_losses(self) -> int:
        return sum(m.their_score for m in self.matches if not m.error)

    @property
    def total_games(self) -> int:
        return self.total_wins + self.total_losses

    @property
    def win_rate(self) -> float:
        if self.total_games == 0:
            return 0.0
        return self.total_wins / self.total_games

    @property
    def match_wins(self) -> int:
        return sum(1 for m in self.matches if not m.error and m.our_score > m.their_score)

    @property
    def match_losses(self) -> int:
        return sum(1 for m in self.matches if not m.error and m.our_score < m.their_score)


# ── Bot submission ─────────────────────────────────────────────────────────────

def submit_bot(bot_dir: Path) -> bool:
    """Submit the bot via ``cambc submit``. Returns True on success."""
    print(f"Submitting {bot_dir} ...")
    try:
        result = subprocess.run(
            ["cambc", "submit", str(bot_dir)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            print(f"  Submit failed (exit {result.returncode}):\n{output}", file=sys.stderr)
            return False
        print(f"  {output}")
        return True
    except subprocess.TimeoutExpired:
        print("  Submit timed out.", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("  'cambc' not found. Is the CLI installed?", file=sys.stderr)
        return False


# ── Challenge & polling ────────────────────────────────────────────────────────

def challenge_opponent(opponent_id: str,
                       prev_match_id: str | None = None,
                       maps: list[str] | None = None) -> str | None:
    """Send ``cambc match unrated <opponent_id>``. Returns match ID or None.

    If prev_match_id is given, uses that submission of the opponent (bypasses
    the per-opponent 5-minute cooldown — the platform treats prior versions
    as a different bot). If maps is given (max 5), passes --map repeatedly.
    """
    cmd = ["cambc", "match", "unrated", opponent_id]
    if prev_match_id:
        cmd += ["--match", prev_match_id]
    if maps:
        for m in maps[:5]:
            cmd += ["--map", m]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        output = result.stdout.strip()
        m = MATCH_ID_RE.search(output)
        if m:
            match_id = m.group(1)
            label = opponent_id if not prev_match_id else f"{opponent_id} (via prev match {prev_match_id[:8]})"
            print(f"  Challenged {label} -> match {match_id}")
            return match_id
        else:
            print(f"  Challenge {opponent_id} failed: {output}", file=sys.stderr)
            return None
    except subprocess.TimeoutExpired:
        print(f"  Challenge {opponent_id} timed out.", file=sys.stderr)
        return None


def poll_match(match_id: str, our_team_id: str | None = None) -> MatchOutcome:
    """Poll ``cambc match <match_id>`` until the match completes."""
    outcome = MatchOutcome(match_id=match_id, opponent_id="")

    while True:
        try:
            result = subprocess.run(
                ["cambc", "match", match_id],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=30,
            )
            output = result.stdout.strip()
        except subprocess.TimeoutExpired:
            time.sleep(POLL_INTERVAL)
            continue

        # Parse status
        status_m = STATUS_RE.search(output)
        status = status_m.group(1).lower() if status_m else ""

        if "error" in status:
            outcome.error = True
            outcome.error_msg = output
            return outcome

        # Parse score
        score_m = SCORE_RE.search(output)
        if score_m:
            score_a = int(score_m.group(1))
            score_b = int(score_m.group(2))

            # Determine which side is ours
            team_a_m = TEAM_A_RE.search(output)
            team_b_m = TEAM_B_RE.search(output)

            if our_team_id and team_a_m and team_b_m:
                team_a_id = team_a_m.group(1)
                team_b_id = team_b_m.group(1)
                if our_team_id == team_a_id:
                    outcome.our_score = score_a
                    outcome.their_score = score_b
                    outcome.opponent_id = team_b_id
                else:
                    outcome.our_score = score_b
                    outcome.their_score = score_a
                    outcome.opponent_id = team_a_id
            else:
                # Default: assume we are Team A (the challenger)
                outcome.our_score = score_a
                outcome.their_score = score_b

            total = score_a + score_b
            if total >= GAMES_PER_MATCH:
                return outcome

        if "completed" in status or "finished" in status:
            return outcome

        time.sleep(POLL_INTERVAL)


def get_our_team_id() -> str | None:
    """Read our team ID from cambc credentials."""
    import json
    creds_file = Path.home() / ".cambc" / "credentials.json"
    if not creds_file.exists():
        return None
    try:
        creds = json.loads(creds_file.read_text())
        return creds.get("team", {}).get("id")
    except Exception:
        return None


# ── Main challenge flow ────────────────────────────────────────────────────────

def run_online_challenge(
    bot_dir: Path,
    opponent_ids: list[str],
    rounds: int = 1,
    skip_submit: bool = False,
    prev_match_ids_by_opp: dict[str, list[str]] | None = None,
    maps_per_round: list[list[str]] | None = None,
    cooldown_override: int | None = None,
) -> dict[str, OpponentStats]:
    """Submit bot, challenge opponents, poll results, return stats.

    prev_match_ids_by_opp: optional opponent_id -> list of prior match IDs.
        If provided, round k uses prev_match_ids_by_opp[opp][k % len(list)] for
        that opponent (cycling through versions to bypass per-opponent cooldown).
        Pass an empty list (or omit the opp) to challenge their current version.

    maps_per_round: optional per-round map names list (each entry up to 5 maps,
        passed verbatim to `cambc match unrated --map`). Use to pin specific
        maps per round. Falls back to platform default when None.

    cooldown_override: if all opponents are challenged via prev versions, set
        this to a small number (e.g. 5) to skip the 5-min wait.
    """
    if not skip_submit:
        if not submit_bot(bot_dir):
            print("Submission failed. Aborting.", file=sys.stderr)
            sys.exit(1)
        print("Waiting 10s for submission to be processed...")
        time.sleep(10)

    our_team_id = get_our_team_id()
    if our_team_id:
        print(f"Our team ID: {our_team_id}")

    all_stats: dict[str, OpponentStats] = {
        opp_id: OpponentStats(opponent_id=opp_id) for opp_id in opponent_ids
    }
    prev_match_ids_by_opp = prev_match_ids_by_opp or {}

    for round_num in range(1, rounds + 1):
        if rounds > 1:
            print(f"\n{'=' * 60}")
            print(f"  Round {round_num}/{rounds}")
            print(f"{'=' * 60}")

        # Pick prior version per opp for this round (cycle through list).
        prev_for_round: dict[str, str | None] = {}
        for opp_id in opponent_ids:
            versions = prev_match_ids_by_opp.get(opp_id, [])
            if versions:
                prev_for_round[opp_id] = versions[(round_num - 1) % len(versions)]
            else:
                prev_for_round[opp_id] = None

        maps_for_round = None
        if maps_per_round:
            maps_for_round = maps_per_round[(round_num - 1) % len(maps_per_round)]

        # Challenge all opponents in parallel
        match_ids: dict[str, str] = {}
        print("\nChallenging opponents...")
        with ThreadPoolExecutor(max_workers=len(opponent_ids)) as executor:
            future_to_opp = {
                executor.submit(challenge_opponent, opp_id,
                                prev_for_round.get(opp_id),
                                maps_for_round): opp_id
                for opp_id in opponent_ids
            }
            for future in as_completed(future_to_opp):
                opp_id = future_to_opp[future]
                match_id = future.result()
                if match_id:
                    match_ids[opp_id] = match_id

        if not match_ids:
            print("No matches were queued. Check opponent IDs.", file=sys.stderr)
            if round_num < rounds:
                print(f"Waiting {COOLDOWN}s before next round...")
                time.sleep(COOLDOWN)
            continue

        print(f"\nPolling {len(match_ids)} match(es)...")
        outcomes: dict[str, MatchOutcome] = {}
        with ThreadPoolExecutor(max_workers=len(match_ids)) as executor:
            future_to_opp = {
                executor.submit(poll_match, mid, our_team_id): opp_id
                for opp_id, mid in match_ids.items()
            }
            for future in as_completed(future_to_opp):
                opp_id = future_to_opp[future]
                outcome = future.result()
                outcome.opponent_id = opp_id
                outcomes[opp_id] = outcome
                if outcome.error:
                    print(f"  {opp_id}: ERROR - {outcome.error_msg[:100]}")
                else:
                    result_str = "WIN" if outcome.our_score > outcome.their_score else "LOSS" if outcome.their_score > outcome.our_score else "DRAW"
                    print(f"  {opp_id}: {outcome.our_score}-{outcome.their_score} ({result_str})")

        for opp_id, outcome in outcomes.items():
            all_stats[opp_id].matches.append(outcome)

        if round_num < rounds:
            # If every opponent had a prior-version target, no per-opp cooldown
            # is needed (platform treats prior-version match as a different bot).
            all_via_prev = all(prev_for_round.get(opp) is not None for opp in opponent_ids)
            wait = cooldown_override if cooldown_override is not None else (5 if all_via_prev else COOLDOWN)
            if wait > 0:
                print(f"\nWaiting {wait}s before next round...")
                time.sleep(wait)

    return all_stats


def compute_overall_win_rate(stats: dict[str, OpponentStats]) -> float:
    """Compute aggregate game win rate across all opponents."""
    total_wins = sum(s.total_wins for s in stats.values())
    total_games = sum(s.total_games for s in stats.values())
    if total_games == 0:
        return 0.0
    return total_wins / total_games


# ── Output ─────────────────────────────────────────────────────────────────────

def print_results(stats: dict[str, OpponentStats]) -> None:
    total_wins = sum(s.total_wins for s in stats.values())
    total_losses = sum(s.total_losses for s in stats.values())
    total_games = total_wins + total_losses
    overall_wr = total_wins / total_games * 100 if total_games else 0

    print(f"\n{'=' * 70}")
    print("  ONLINE CHALLENGE RESULTS")
    print(f"{'=' * 70}")
    print(f"  Overall: {total_wins}W / {total_losses}L ({overall_wr:.1f}% game win rate)")
    print()

    opp_w = max((len(s.opponent_id) for s in stats.values()), default=10)
    print(f"  {'Opponent':{opp_w}}  {'Matches':>8}  {'Games':>10}  {'Match W-L':>10}  {'Game WR':>8}")
    print(f"  {'-' * opp_w}  {'-------':>8}  {'---------':>10}  {'---------':>10}  {'------':>8}")

    for opp_id, s in stats.items():
        n_matches = len([m for m in s.matches if not m.error])
        games_str = f"{s.total_wins}-{s.total_losses}"
        match_str = f"{s.match_wins}-{s.match_losses}"
        wr_str = f"{s.win_rate * 100:.1f}%"
        print(f"  {opp_id:{opp_w}}  {n_matches:>8}  {games_str:>10}  {match_str:>10}  {wr_str:>8}")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Submit a bot and challenge opponents to unrated online matches."
    )
    parser.add_argument("bot", help="Bot to submit (directory path or name in bots/).")
    parser.add_argument("--opponents", nargs="+", required=True,
                        help="Opponent team IDs to challenge.")
    parser.add_argument("--rounds", type=int, default=1,
                        help="Number of challenge rounds.")
    parser.add_argument("--skip-submit", action="store_true",
                        help="Skip submission (use already-submitted bot).")
    parser.add_argument("--prev-match",
                        action="append", default=[],
                        metavar="OPP_ID:MATCH_ID",
                        help="Use this prior-match opponent version (bypasses 5-min cooldown). "
                             "Repeat to provide multiple versions per opponent (will cycle).")
    parser.add_argument("--maps",
                        action="append", default=[],
                        metavar="m1,m2,...",
                        help="Comma-separated map names for one round (max 5). Repeat to vary by round.")
    parser.add_argument("--cooldown", type=int, default=None,
                        help="Override seconds-between-rounds wait (default 310, or 5 if all opps use --prev-match).")
    args = parser.parse_args()

    bot_path = Path(args.bot)
    if not bot_path.is_dir():
        bot_path = Path("bots") / args.bot
    if not bot_path.is_dir():
        print(f"Bot directory not found: {args.bot}", file=sys.stderr)
        return 1

    prev_by_opp: dict[str, list[str]] = {}
    for spec in args.prev_match:
        if ":" not in spec:
            print(f"--prev-match expects OPP_ID:MATCH_ID, got: {spec}", file=sys.stderr)
            return 1
        opp, mid = spec.split(":", 1)
        prev_by_opp.setdefault(opp, []).append(mid)

    maps_per_round = None
    if args.maps:
        maps_per_round = [m.split(",") for m in args.maps]

    stats = run_online_challenge(
        bot_dir=bot_path,
        opponent_ids=args.opponents,
        rounds=args.rounds,
        skip_submit=args.skip_submit,
        prev_match_ids_by_opp=prev_by_opp or None,
        maps_per_round=maps_per_round,
        cooldown_override=args.cooldown,
    )

    print_results(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
