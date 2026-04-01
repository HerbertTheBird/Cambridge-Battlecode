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

def challenge_opponent(opponent_id: str) -> str | None:
    """Send ``cambc unrated <opponent_id>``. Returns match ID or None."""
    try:
        result = subprocess.run(
            ["cambc", "unrated", opponent_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        output = result.stdout.strip()
        m = MATCH_ID_RE.search(output)
        if m:
            match_id = m.group(1)
            print(f"  Challenged {opponent_id} -> match {match_id}")
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
) -> dict[str, OpponentStats]:
    """Submit bot, challenge opponents, poll results, return stats.

    Each round challenges all opponents in parallel, then waits for all
    results. Between rounds there's a 5-minute cooldown per opponent.
    """
    if not skip_submit:
        if not submit_bot(bot_dir):
            print("Submission failed. Aborting.", file=sys.stderr)
            sys.exit(1)
        # Give the platform a moment to process the submission
        print("Waiting 10s for submission to be processed...")
        time.sleep(10)

    our_team_id = get_our_team_id()
    if our_team_id:
        print(f"Our team ID: {our_team_id}")

    all_stats: dict[str, OpponentStats] = {
        opp_id: OpponentStats(opponent_id=opp_id) for opp_id in opponent_ids
    }

    for round_num in range(1, rounds + 1):
        if rounds > 1:
            print(f"\n{'=' * 60}")
            print(f"  Round {round_num}/{rounds}")
            print(f"{'=' * 60}")

        # Challenge all opponents in parallel
        match_ids: dict[str, str] = {}  # opponent_id -> match_id
        print("\nChallenging opponents...")
        with ThreadPoolExecutor(max_workers=len(opponent_ids)) as executor:
            future_to_opp = {
                executor.submit(challenge_opponent, opp_id): opp_id
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

        # Poll all matches in parallel
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

        # Record results
        for opp_id, outcome in outcomes.items():
            all_stats[opp_id].matches.append(outcome)

        # Cooldown before next round
        if round_num < rounds:
            print(f"\nWaiting {COOLDOWN}s before next round (5-min cooldown)...")
            time.sleep(COOLDOWN)

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
                        help="Number of challenge rounds (5-min cooldown between rounds).")
    parser.add_argument("--skip-submit", action="store_true",
                        help="Skip submission (use already-submitted bot).")
    args = parser.parse_args()

    bot_path = Path(args.bot)
    if not bot_path.is_dir():
        bot_path = Path("bots") / args.bot
    if not bot_path.is_dir():
        print(f"Bot directory not found: {args.bot}", file=sys.stderr)
        return 1

    stats = run_online_challenge(
        bot_dir=bot_path,
        opponent_ids=args.opponents,
        rounds=args.rounds,
        skip_submit=args.skip_submit,
    )

    print_results(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
