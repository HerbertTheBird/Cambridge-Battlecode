#!/usr/bin/env python3
"""
Loss Diagnosis — given a replay we lost, identify *when* and *why*.

Walks the replay turn-by-turn and tracks: unit counts (by category), HP totals,
resource gap, territory control (Voronoi from each core over passable tiles),
damage events, and net build-vs-loss. Outputs:

  - turn-by-turn deltas (sampled), with a "decisive moment" marker
  - the turn at which the lead flipped permanently to the winner
  - top damage events that hurt us (largest single losses)
  - terse one-paragraph summary

Usage:
    python loss_diagnosis.py replay.replay26 --our-team A
    python loss_diagnosis.py replay.replay26 --our-team B --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "bots" / "_debug_wrapper"))
from replay_parser import (
    parse_replay, GameReplay, GameMap, PlaceEntity, RemoveEntity, MoveBuilderBot,
    UpdateHp, UpdatePlayers, BotOutput, Pos, RawEntity,
)


# ── Categories ───────────────────────────────────────────────────────────────

COMBAT = {"GUNNER", "SENTINEL", "BREACH", "LAUNCHER"}
ECONOMY = {"HARVESTER", "FOUNDRY"}
INFRA = {"CONVEYOR", "SPLITTER", "ARMOURED_CONVEYOR", "BRIDGE", "ROAD", "BARRIER"}
UNIT_LIKE = {"BUILDER_BOT", "CORE"}

ENV_WALL = 1


def _i32(v: int) -> int:
    """Force a (possibly 64-bit-padded) varint into a signed 32-bit int.

    The cambc encoder emits int32 deltas as multi-byte varints whose upper
    bits aren't sign-extended consistently; replay_parser._signed32 returns
    huge garbage values. Mask to low 32 bits, then two's-complement.
    """
    v = v & 0xFFFFFFFF
    if v >= (1 << 31):
        v -= (1 << 32)
    return v


# ── Live game-state tracker ──────────────────────────────────────────────────

@dataclass
class _Entity:
    id: int
    team: int
    etype: str
    pos: Pos
    hp: int
    maxhp: int
    alive: bool = True


@dataclass
class TurnSnapshot:
    turn: int
    a_combat: int = 0
    a_econ: int = 0
    a_infra: int = 0
    a_builders: int = 0
    a_total_hp: int = 0
    a_titanium: int = 0
    a_axionite: int = 0
    a_territory: int = 0
    b_combat: int = 0
    b_econ: int = 0
    b_infra: int = 0
    b_builders: int = 0
    b_total_hp: int = 0
    b_titanium: int = 0
    b_axionite: int = 0
    b_territory: int = 0


def _voronoi_territory(gmap: GameMap, ents: dict[int, _Entity]) -> tuple[int, int]:
    """BFS dual flood-fill from each core; tiles claimed first by team A vs B."""
    w, h = gmap.width, gmap.height
    a_front: deque[tuple[int, int]] = deque()
    b_front: deque[tuple[int, int]] = deque()
    claimed = [-1] * (w * h)  # -1 = unclaimed, 0 = A, 1 = B

    for c in gmap.cores:
        # Cores occupy 3x3; seed every tile of their footprint
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                x, y = c.pos.x + dx, c.pos.y + dy
                if 0 <= x < w and 0 <= y < h:
                    idx = y * w + x
                    if claimed[idx] == -1:
                        claimed[idx] = c.team
                        if c.team == 0:
                            a_front.append((x, y))
                        else:
                            b_front.append((x, y))

    DIRS_8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
    while a_front or b_front:
        next_a: deque[tuple[int, int]] = deque()
        next_b: deque[tuple[int, int]] = deque()
        for x, y in a_front:
            for dx, dy in DIRS_8:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    idx = ny * w + nx
                    if claimed[idx] == -1 and gmap.terrain[ny][nx] != ENV_WALL:
                        claimed[idx] = 0
                        next_a.append((nx, ny))
        for x, y in b_front:
            for dx, dy in DIRS_8:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    idx = ny * w + nx
                    if claimed[idx] == -1 and gmap.terrain[ny][nx] != ENV_WALL:
                        claimed[idx] = 1
                        next_b.append((nx, ny))
        a_front, b_front = next_a, next_b

    a_count = sum(1 for c in claimed if c == 0)
    b_count = sum(1 for c in claimed if c == 1)
    return a_count, b_count


def _category(etype: str) -> str:
    if etype in COMBAT:
        return "combat"
    if etype in ECONOMY:
        return "economy"
    if etype in INFRA:
        return "infra"
    if etype == "BUILDER_BOT":
        return "builder"
    if etype == "CORE":
        return "core"
    return "other"


def _snapshot(turn: int, ents: dict[int, _Entity], a_ti: int, a_ax: int,
              b_ti: int, b_ax: int, a_terr: int, b_terr: int) -> TurnSnapshot:
    snap = TurnSnapshot(turn=turn,
                       a_titanium=a_ti, a_axionite=a_ax,
                       b_titanium=b_ti, b_axionite=b_ax,
                       a_territory=a_terr, b_territory=b_terr)
    for e in ents.values():
        if not e.alive:
            continue
        cat = _category(e.etype)
        if e.team == 0:
            if cat == "combat":
                snap.a_combat += 1
            elif cat == "economy":
                snap.a_econ += 1
            elif cat == "infra":
                snap.a_infra += 1
            elif cat == "builder":
                snap.a_builders += 1
            snap.a_total_hp += max(0, e.hp)
        else:
            if cat == "combat":
                snap.b_combat += 1
            elif cat == "economy":
                snap.b_econ += 1
            elif cat == "infra":
                snap.b_infra += 1
            elif cat == "builder":
                snap.b_builders += 1
            snap.b_total_hp += max(0, e.hp)
    return snap


# ── Diagnosis ────────────────────────────────────────────────────────────────

@dataclass
class DamageHit:
    turn: int
    target_id: int
    target_etype: str
    target_team: int
    delta: int   # negative = damage
    pos: Pos


@dataclass
class Diagnosis:
    map_size: tuple[int, int]
    total_turns: int
    winner: int   # 0=A, 1=B, -1=none
    our_team: int
    snapshots: list[TurnSnapshot]
    big_hits_against_us: list[DamageHit]
    losses_us: dict[str, int]   # type -> count we lost
    losses_them: dict[str, int]
    flip_turn: int | None       # turn when we permanently fell behind in combat units
    decisive_turn: int | None
    decisive_reason: str
    summary: str


def diagnose(replay: GameReplay, our_team: int, sample_every: int = 25) -> Diagnosis:
    ents: dict[int, _Entity] = {}
    a_ti = a_ax = b_ti = b_ax = 0
    snapshots: list[TurnSnapshot] = []
    big_hits: list[DamageHit] = []
    losses = {0: defaultdict(int), 1: defaultdict(int)}

    # Voronoi recomputation is O(W*H); recompute every `voronoi_every` turns.
    voronoi_every = max(20, len(replay.turns) // 40)
    cached_terr = (0, 0)

    for turn_idx, turn in enumerate(replay.turns):
        turn_num = turn_idx + 1

        for upd in turn.updates:
            if isinstance(upd, PlaceEntity):
                ent = upd.entity
                if ent.entity_type == "MARKER":
                    continue
                ents[ent.id] = _Entity(
                    id=ent.id, team=ent.team, etype=ent.entity_type,
                    pos=ent.pos, hp=ent.hp, maxhp=ent.maxhp,
                )
            elif isinstance(upd, MoveBuilderBot):
                e = ents.get(upd.id)
                if e:
                    e.pos = upd.to
            elif isinstance(upd, UpdateHp):
                e = ents.get(upd.id)
                if e:
                    delta = _i32(upd.delta)
                    e.hp = max(0, e.hp + delta)
                    if delta < 0:
                        hit = DamageHit(
                            turn=turn_num, target_id=e.id, target_etype=e.etype,
                            target_team=e.team, delta=delta, pos=e.pos,
                        )
                        if e.team == our_team and abs(delta) >= 15:
                            big_hits.append(hit)
            elif isinstance(upd, RemoveEntity):
                e = ents.get(upd.id)
                if e and e.etype != "MARKER":
                    e.alive = False
                    losses[e.team][e.etype] += 1
            elif isinstance(upd, UpdatePlayers):
                a_ti = upd.a_titanium
                a_ax = upd.a_axionite
                b_ti = upd.b_titanium
                b_ax = upd.b_axionite

        if turn_idx % voronoi_every == 0 or turn_idx == len(replay.turns) - 1:
            cached_terr = _voronoi_territory(replay.map, ents)
        if turn_idx % sample_every == 0 or turn_idx == len(replay.turns) - 1:
            snap = _snapshot(turn_num, ents, a_ti, a_ax, b_ti, b_ax,
                            cached_terr[0], cached_terr[1])
            snapshots.append(snap)

    # Determine flip turn (first turn where our combat-unit count fell behind
    # and stayed behind for the rest of the game).
    flip_turn = _find_flip_turn(snapshots, our_team)
    decisive_turn, decisive_reason = _find_decisive_turn(snapshots, our_team)

    losses_us = dict(losses[our_team])
    losses_them = dict(losses[1 - our_team])

    summary = _build_summary(
        snapshots=snapshots, our_team=our_team, winner=replay.winner,
        flip_turn=flip_turn, decisive_turn=decisive_turn,
        decisive_reason=decisive_reason,
        losses_us=losses_us, losses_them=losses_them,
        big_hits=big_hits,
    )

    # Sort and trim big hits
    big_hits.sort(key=lambda h: h.delta)
    big_hits = big_hits[:20]

    return Diagnosis(
        map_size=(replay.map.width, replay.map.height),
        total_turns=len(replay.turns),
        winner=replay.winner,
        our_team=our_team,
        snapshots=snapshots,
        big_hits_against_us=big_hits,
        losses_us=losses_us,
        losses_them=losses_them,
        flip_turn=flip_turn,
        decisive_turn=decisive_turn,
        decisive_reason=decisive_reason,
        summary=summary,
    )


def _find_flip_turn(snapshots: list[TurnSnapshot], our_team: int) -> int | None:
    """First turn where (our_combat - their_combat) became negative and stayed negative."""
    flip = None
    for snap in snapshots:
        if our_team == 0:
            diff = snap.a_combat - snap.b_combat
        else:
            diff = snap.b_combat - snap.a_combat
        if diff < 0 and flip is None:
            flip = snap.turn
        elif diff >= 0:
            flip = None   # reset; lead recovered
    return flip


def _find_decisive_turn(snapshots: list[TurnSnapshot], our_team: int) -> tuple[int | None, str]:
    """Largest single-window swing in HP+resource lead against us."""
    if len(snapshots) < 3:
        return None, "insufficient data"

    def lead(snap: TurnSnapshot) -> float:
        if our_team == 0:
            return (snap.a_total_hp - snap.b_total_hp) + 0.3 * (snap.a_titanium - snap.b_titanium)
        return (snap.b_total_hp - snap.a_total_hp) + 0.3 * (snap.b_titanium - snap.a_titanium)

    leads = [lead(s) for s in snapshots]
    biggest_drop = 0.0
    biggest_idx = None
    for i in range(1, len(leads)):
        drop = leads[i - 1] - leads[i]
        if drop > biggest_drop:
            biggest_drop = drop
            biggest_idx = i

    if biggest_idx is None:
        return None, "no clear swing"

    snap_before = snapshots[biggest_idx - 1]
    snap_after = snapshots[biggest_idx]
    reason_parts = []

    if our_team == 0:
        if snap_after.a_combat < snap_before.a_combat:
            reason_parts.append(f"lost {snap_before.a_combat - snap_after.a_combat} combat units")
        if snap_after.a_total_hp - snap_before.a_total_hp < -50:
            reason_parts.append(f"took {snap_before.a_total_hp - snap_after.a_total_hp} damage")
        if snap_after.a_econ < snap_before.a_econ:
            reason_parts.append(f"lost {snap_before.a_econ - snap_after.a_econ} econ buildings")
    else:
        if snap_after.b_combat < snap_before.b_combat:
            reason_parts.append(f"lost {snap_before.b_combat - snap_after.b_combat} combat units")
        if snap_after.b_total_hp - snap_before.b_total_hp < -50:
            reason_parts.append(f"took {snap_before.b_total_hp - snap_after.b_total_hp} damage")
        if snap_after.b_econ < snap_before.b_econ:
            reason_parts.append(f"lost {snap_before.b_econ - snap_after.b_econ} econ buildings")

    reason = "; ".join(reason_parts) if reason_parts else "lead eroded gradually"
    return snap_after.turn, reason


def _build_summary(snapshots, our_team, winner, flip_turn, decisive_turn,
                   decisive_reason, losses_us, losses_them, big_hits) -> str:
    if not snapshots:
        return "no data"

    last = snapshots[-1]
    if our_team == 0:
        our_combat, their_combat = last.a_combat, last.b_combat
        our_econ, their_econ = last.a_econ, last.b_econ
        our_terr, their_terr = last.a_territory, last.b_territory
    else:
        our_combat, their_combat = last.b_combat, last.a_combat
        our_econ, their_econ = last.b_econ, last.a_econ
        our_terr, their_terr = last.b_territory, last.a_territory

    won_str = "won" if winner == our_team else "lost" if winner == 1 - our_team else "drew"

    # Find earliest-turn snapshot where opponent first had a turret advantage
    early_combat_lead = None
    for snap in snapshots:
        ours = snap.a_combat if our_team == 0 else snap.b_combat
        theirs = snap.b_combat if our_team == 0 else snap.a_combat
        if theirs > ours and theirs - ours >= 2:
            early_combat_lead = snap.turn
            break

    parts = [f"We {won_str} (winner team={winner})."]
    if flip_turn is not None:
        parts.append(f"Combat-unit lead flipped to opponent at T{flip_turn} and stayed flipped.")
    if early_combat_lead is not None:
        parts.append(f"Opponent first had a 2+ combat-unit lead at T{early_combat_lead}.")
    if decisive_turn is not None:
        parts.append(f"Biggest swing against us was around T{decisive_turn}: {decisive_reason}.")

    if losses_us:
        top_loss = max(losses_us.items(), key=lambda x: x[1])
        parts.append(f"We lost {sum(losses_us.values())} buildings/units total (most: {top_loss[1]}× {top_loss[0]}).")

    parts.append(f"Final state: us combat={our_combat}/econ={our_econ}/territory={our_terr}, "
                 f"them combat={their_combat}/econ={their_econ}/territory={their_terr}.")

    return " ".join(parts)


# ── Output ───────────────────────────────────────────────────────────────────

def print_diagnosis(d: Diagnosis) -> None:
    we_label = "A" if d.our_team == 0 else "B"
    they_label = "B" if d.our_team == 0 else "A"
    winner_label = {0: "A", 1: "B", -1: "draw"}[d.winner]

    print(f"\n{'=' * 80}")
    print(f"  Loss Diagnosis  ({d.map_size[0]}x{d.map_size[1]} map, {d.total_turns} turns)")
    print(f"  Our team: {we_label}   Winner: {winner_label}")
    print(f"{'=' * 80}\n")

    print("  --- Summary ---")
    print(f"  {d.summary}\n")

    print("  --- Snapshots (turn / our combat / their combat / our econ / their econ / our HP / their HP / our terr / their terr / Ti gap) ---")
    print(f"  {'Turn':>5}  {'oC':>3}  {'tC':>3}  {'oE':>3}  {'tE':>3}  {'oHP':>5}  {'tHP':>5}  {'oTr':>5}  {'tTr':>5}  {'TiGap':>6}")
    print(f"  {'-' * 5}  {'-' * 3}  {'-' * 3}  {'-' * 3}  {'-' * 3}  {'-' * 5}  {'-' * 5}  {'-' * 5}  {'-' * 5}  {'-' * 6}")
    for s in d.snapshots:
        if d.our_team == 0:
            row = (s.turn, s.a_combat, s.b_combat, s.a_econ, s.b_econ,
                   s.a_total_hp, s.b_total_hp, s.a_territory, s.b_territory,
                   s.a_titanium - s.b_titanium)
        else:
            row = (s.turn, s.b_combat, s.a_combat, s.b_econ, s.a_econ,
                   s.b_total_hp, s.a_total_hp, s.b_territory, s.a_territory,
                   s.b_titanium - s.a_titanium)
        print(f"  {row[0]:>5}  {row[1]:>3}  {row[2]:>3}  {row[3]:>3}  {row[4]:>3}  "
              f"{row[5]:>5}  {row[6]:>5}  {row[7]:>5}  {row[8]:>5}  {row[9]:>+6}")

    if d.flip_turn is not None:
        print(f"\n  Combat lead flipped at: T{d.flip_turn}")
    if d.decisive_turn is not None:
        print(f"  Decisive swing turn:    T{d.decisive_turn}  ({d.decisive_reason})")

    print(f"\n  --- What we lost ({we_label}) ---")
    for etype, count in sorted(d.losses_us.items(), key=lambda x: -x[1]):
        print(f"    {etype:20s}  {count}")
    print(f"\n  --- What they lost ({they_label}) ---")
    for etype, count in sorted(d.losses_them.items(), key=lambda x: -x[1]):
        print(f"    {etype:20s}  {count}")

    if d.big_hits_against_us:
        print(f"\n  --- Top damage events against us (largest single hits) ---")
        for h in d.big_hits_against_us:
            print(f"    T{h.turn:04d}  {h.target_etype:18s} @ ({h.pos.x:>2},{h.pos.y:>2})  hp{h.delta:+d}")


def diagnosis_to_jsonable(d: Diagnosis) -> dict:
    return {
        "map_size": list(d.map_size),
        "total_turns": d.total_turns,
        "winner": d.winner,
        "our_team": d.our_team,
        "flip_turn": d.flip_turn,
        "decisive_turn": d.decisive_turn,
        "decisive_reason": d.decisive_reason,
        "summary": d.summary,
        "losses_us": d.losses_us,
        "losses_them": d.losses_them,
        "snapshots": [asdict(s) for s in d.snapshots],
        "big_hits_against_us": [
            {"turn": h.turn, "target_id": h.target_id, "target_etype": h.target_etype,
             "target_team": h.target_team, "delta": h.delta,
             "pos": [h.pos.x, h.pos.y]}
            for h in d.big_hits_against_us
        ],
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose why we lost a game from its replay.")
    parser.add_argument("replay", type=Path, help=".replay26 file to analyze.")
    parser.add_argument("--our-team", choices=["A", "B"], default="A",
                        help="Which team is ours (default: A).")
    parser.add_argument("--sample-every", type=int, default=25,
                        help="Sample snapshot every N turns (default: 25).")
    parser.add_argument("--json", type=Path, default=None,
                        help="Write structured diagnosis JSON to this path.")
    args = parser.parse_args()

    if not args.replay.exists():
        print(f"Replay not found: {args.replay}", file=sys.stderr)
        return 1

    print(f"Parsing {args.replay} ...")
    replay = parse_replay(str(args.replay))
    print(f"  {replay.map.width}x{replay.map.height}, {len(replay.turns)} turns, winner={replay.winner}")

    our_team = 0 if args.our_team == "A" else 1
    diag = diagnose(replay, our_team=our_team, sample_every=args.sample_every)
    print_diagnosis(diag)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(diagnosis_to_jsonable(diag), indent=2))
        print(f"\n  JSON written to {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
