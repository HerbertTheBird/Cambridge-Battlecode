#!/usr/bin/env python3
"""
Diff Replay — compare two replays of the same map+seed (e.g. baseline vs variant).

Walks both replays in lockstep and reports:
  - first turn where unit positions diverge
  - first turn where build orders diverge
  - per-turn aggregate divergence (count of differing entity positions)
  - score gap from each turn forward (whose lead expanded faster)

Useful for: "I changed bot X, did it help on this specific game?" — feed in
the baseline replay and the variant replay, see exactly when they branched.

Note: the two replays must be from games run on the SAME map and SAME seed,
otherwise divergence will appear from turn 1 (which is still a valid signal).

Usage:
    python diff_replay.py baseline.replay26 variant.replay26 --our-team A
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "bots" / "_debug_wrapper"))
from replay_parser import (
    parse_replay, GameReplay, PlaceEntity, RemoveEntity, MoveBuilderBot,
    UpdateHp, UpdatePlayers, BotOutput, Pos, RawEntity,
)


def _i32(v: int) -> int:
    v = v & 0xFFFFFFFF
    if v >= (1 << 31):
        v -= (1 << 32)
    return v


# ── Live state for one replay ───────────────────────────────────────────────

@dataclass
class _LiveEntity:
    id: int
    team: int
    etype: str
    pos: Pos
    hp: int
    alive: bool = True


@dataclass
class _LiveState:
    ents: dict[int, _LiveEntity]
    a_ti: int = 0
    a_ax: int = 0
    b_ti: int = 0
    b_ax: int = 0
    builds_this_turn: list[tuple[int, str, Pos]] = None  # (team, etype, pos)


def _new_state() -> _LiveState:
    return _LiveState(ents={}, builds_this_turn=[])


def _apply_turn(state: _LiveState, turn_updates) -> None:
    state.builds_this_turn = []
    for upd in turn_updates:
        if isinstance(upd, PlaceEntity):
            ent = upd.entity
            state.ents[ent.id] = _LiveEntity(
                id=ent.id, team=ent.team, etype=ent.entity_type,
                pos=ent.pos, hp=ent.hp,
            )
            if ent.entity_type != "MARKER":
                state.builds_this_turn.append((ent.team, ent.entity_type, ent.pos))
        elif isinstance(upd, MoveBuilderBot):
            e = state.ents.get(upd.id)
            if e:
                e.pos = upd.to
        elif isinstance(upd, UpdateHp):
            e = state.ents.get(upd.id)
            if e:
                e.hp = max(0, e.hp + _i32(upd.delta))
        elif isinstance(upd, RemoveEntity):
            e = state.ents.get(upd.id)
            if e:
                e.alive = False
        elif isinstance(upd, UpdatePlayers):
            state.a_ti = upd.a_titanium
            state.a_ax = upd.a_axionite
            state.b_ti = upd.b_titanium
            state.b_ax = upd.b_axionite


# ── Diffing ─────────────────────────────────────────────────────────────────

@dataclass
class TurnDiff:
    turn: int
    pos_diffs: int      # entities alive in both, but at different positions
    only_in_a: int      # entity ids in baseline but not variant (or dead in variant)
    only_in_b: int      # vice versa
    builds_a: list[tuple[int, str, Pos]]
    builds_b: list[tuple[int, str, Pos]]
    score_a: tuple[int, int, int]   # (combat, econ, total_hp) for "us" team in baseline
    score_b: tuple[int, int, int]   # same for variant


def _team_score(state: _LiveState, our_team: int) -> tuple[int, int, int]:
    combat = econ = hp = 0
    for e in state.ents.values():
        if not e.alive or e.team != our_team:
            continue
        if e.etype in ("GUNNER", "SENTINEL", "BREACH", "LAUNCHER"):
            combat += 1
        elif e.etype in ("HARVESTER", "FOUNDRY"):
            econ += 1
        hp += max(0, e.hp)
    return combat, econ, hp


def diff_replays(rep_a: GameReplay, rep_b: GameReplay, our_team: int) -> tuple[list[TurnDiff], int | None, int | None]:
    """Walk both replays in lockstep. Returns (per_turn_diffs, first_pos_div_turn, first_build_div_turn)."""
    state_a = _new_state()
    state_b = _new_state()

    n = min(len(rep_a.turns), len(rep_b.turns))
    diffs: list[TurnDiff] = []
    first_pos_div: int | None = None
    first_build_div: int | None = None

    for i in range(n):
        _apply_turn(state_a, rep_a.turns[i].updates)
        _apply_turn(state_b, rep_b.turns[i].updates)
        turn = i + 1

        # Position diff: entities alive in both, at different positions
        ids_a = {eid for eid, e in state_a.ents.items() if e.alive}
        ids_b = {eid for eid, e in state_b.ents.items() if e.alive}
        common = ids_a & ids_b
        pos_diffs = sum(1 for eid in common if state_a.ents[eid].pos != state_b.ents[eid].pos)
        only_a = len(ids_a - ids_b)
        only_b = len(ids_b - ids_a)

        builds_a = list(state_a.builds_this_turn)
        builds_b = list(state_b.builds_this_turn)

        # Normalize builds to comparable tuples (Pos is a dataclass, not orderable)
        def _key(b: tuple[int, str, Pos]) -> tuple[int, str, int, int]:
            return (b[0], b[1], b[2].x, b[2].y)
        if sorted(builds_a, key=_key) != sorted(builds_b, key=_key) and first_build_div is None:
            first_build_div = turn

        if (pos_diffs > 0 or only_a > 0 or only_b > 0) and first_pos_div is None:
            first_pos_div = turn

        diffs.append(TurnDiff(
            turn=turn, pos_diffs=pos_diffs, only_in_a=only_a, only_in_b=only_b,
            builds_a=builds_a, builds_b=builds_b,
            score_a=_team_score(state_a, our_team),
            score_b=_team_score(state_b, our_team),
        ))

    return diffs, first_pos_div, first_build_div


# ── Output ──────────────────────────────────────────────────────────────────

def print_diffs(diffs: list[TurnDiff], first_pos: int | None, first_build: int | None,
                rep_a: GameReplay, rep_b: GameReplay, sample_every: int) -> None:
    print(f"\n{'=' * 80}")
    print(f"  Replay Diff")
    print(f"{'=' * 80}")
    print(f"  Replay A: {len(rep_a.turns)} turns, winner={rep_a.winner}")
    print(f"  Replay B: {len(rep_b.turns)} turns, winner={rep_b.winner}")
    print(f"  Map A: {rep_a.map.width}x{rep_a.map.height}")
    print(f"  Map B: {rep_b.map.width}x{rep_b.map.height}")

    if (rep_a.map.width, rep_a.map.height) != (rep_b.map.width, rep_b.map.height):
        print("  WARNING: map sizes differ — these are different maps. Diff will be meaningless.")

    print(f"\n  First position divergence:    {first_pos if first_pos else 'none (replays identical at position level)'}")
    print(f"  First build-order divergence: {first_build if first_build else 'none'}")

    if first_build:
        # Show the actual diverging builds at that turn
        td = diffs[first_build - 1]
        print(f"\n  --- Diverging builds at T{first_build} ---")
        only_a = sorted(set(td.builds_a) - set(td.builds_b))
        only_b = sorted(set(td.builds_b) - set(td.builds_a))
        print(f"  A built (B did not): {only_a}")
        print(f"  B built (A did not): {only_b}")

    print(f"\n  --- Per-turn aggregate (sampled every {sample_every} turns) ---")
    print(f"  {'Turn':>5}  {'PosD':>5}  {'A-only':>6}  {'B-only':>6}  "
          f"{'A combat/econ/HP':>22}  {'B combat/econ/HP':>22}")
    print(f"  {'-' * 5}  {'-' * 5}  {'-' * 6}  {'-' * 6}  {'-' * 22}  {'-' * 22}")
    for td in diffs:
        if td.turn % sample_every != 0 and td.turn != len(diffs):
            continue
        score_a_str = f"{td.score_a[0]:>2}/{td.score_a[1]:>2}/{td.score_a[2]:>5}"
        score_b_str = f"{td.score_b[0]:>2}/{td.score_b[1]:>2}/{td.score_b[2]:>5}"
        print(f"  {td.turn:>5}  {td.pos_diffs:>5}  {td.only_in_a:>6}  {td.only_in_b:>6}  "
              f"{score_a_str:>22}  {score_b_str:>22}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Diff two .replay26 files in lockstep.")
    parser.add_argument("replay_a", type=Path, help="First replay (e.g. baseline).")
    parser.add_argument("replay_b", type=Path, help="Second replay (e.g. variant).")
    parser.add_argument("--our-team", choices=["A", "B"], default="A",
                        help="Whose perspective to score from.")
    parser.add_argument("--sample-every", type=int, default=50,
                        help="Print one row per N turns (default: 50).")
    args = parser.parse_args()

    if not args.replay_a.exists():
        print(f"Replay A not found: {args.replay_a}", file=sys.stderr)
        return 1
    if not args.replay_b.exists():
        print(f"Replay B not found: {args.replay_b}", file=sys.stderr)
        return 1

    print(f"Parsing {args.replay_a} ...")
    rep_a = parse_replay(str(args.replay_a))
    print(f"Parsing {args.replay_b} ...")
    rep_b = parse_replay(str(args.replay_b))

    our_team = 0 if args.our_team == "A" else 1
    diffs, first_pos, first_build = diff_replays(rep_a, rep_b, our_team=our_team)
    print_diffs(diffs, first_pos, first_build, rep_a, rep_b, args.sample_every)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
