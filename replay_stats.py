#!/usr/bin/env python3
"""
Replay Stats Extractor — aggregate game statistics from .replay26 files.

Extracts: resource curves, unit/building counts over time, damage events,
economy snapshots, build orders, and game phase timing.

Unlike replay_analyzer.py (which re-runs bot logic), this tool passively
extracts quantitative data from the replay for strategic analysis.

Usage:
    python replay_stats.py replay.replay26
    python replay_stats.py replay.replay26 --csv stats.csv
    python replay_stats.py replay.replay26 --build-order
    python replay_stats.py replay.replay26 --economy-graph
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Import the existing replay parser
sys.path.insert(0, str(Path(__file__).parent / "bots" / "debug_wrapper"))
from replay_parser import (
    parse_replay, GameReplay, PlaceEntity, MoveBuilderBot, RemoveEntity,
    UpdateHp, UpdatePlayers, SetActionCooldown, SetMoveCooldown, BotOutput,
    Pos, RawEntity,
)


# ── Data structures ──────────────────────────────────────────────────────────

TEAM_NAMES = {0: "A", 1: "B"}

COMBAT_UNITS = {"GUNNER", "SENTINEL", "BREACH", "LAUNCHER"}
INFRASTRUCTURE = {"CONVEYOR", "SPLITTER", "ARMOURED_CONVEYOR", "BRIDGE", "ROAD", "BARRIER"}
ECONOMY = {"HARVESTER", "FOUNDRY"}

ENTITY_CATEGORIES = {
    "BUILDER_BOT": "unit",
    "CORE": "unit",
    "GUNNER": "combat",
    "SENTINEL": "combat",
    "BREACH": "combat",
    "LAUNCHER": "combat",
    "CONVEYOR": "infra",
    "SPLITTER": "infra",
    "ARMOURED_CONVEYOR": "infra",
    "BRIDGE": "infra",
    "ROAD": "infra",
    "BARRIER": "infra",
    "HARVESTER": "economy",
    "FOUNDRY": "economy",
    "MARKER": "marker",
}


@dataclass
class EntityState:
    id: int
    team: int
    entity_type: str
    pos: Pos
    hp: int
    max_hp: int
    spawn_turn: int
    death_turn: int | None = None


@dataclass
class DamageEvent:
    turn: int
    target_id: int
    target_type: str
    target_team: int
    delta: int  # negative = damage, positive = heal


@dataclass
class BuildEvent:
    turn: int
    team: int
    entity_type: str
    pos: Pos
    entity_id: int


@dataclass
class DeathEvent:
    turn: int
    entity_id: int
    entity_type: str
    team: int
    pos: Pos


@dataclass
class ResourceSnapshot:
    turn: int
    a_titanium: int
    a_axionite: int
    b_titanium: int
    b_axionite: int


@dataclass
class TurnSnapshot:
    turn: int
    # Entity counts by team
    a_builders: int = 0
    a_combat: int = 0
    a_infra: int = 0
    a_economy: int = 0
    b_builders: int = 0
    b_combat: int = 0
    b_infra: int = 0
    b_economy: int = 0
    # Resources
    a_titanium: int = 0
    a_axionite: int = 0
    b_titanium: int = 0
    b_axionite: int = 0


@dataclass
class GameStats:
    map_name: str
    map_width: int
    map_height: int
    total_turns: int
    winner: str  # "A", "B", or "draw"

    # Build orders
    build_events: list[BuildEvent]
    death_events: list[DeathEvent]
    damage_events: list[DamageEvent]
    resource_snapshots: list[ResourceSnapshot]
    turn_snapshots: list[TurnSnapshot]

    # Aggregates
    entities_built: dict[str, dict[str, int]]  # team -> type -> count
    entities_lost: dict[str, dict[str, int]]    # team -> type -> count
    total_damage_dealt: dict[str, int]          # team -> total damage dealt to enemy
    total_healing: dict[str, int]               # team -> total healing
    peak_resources: dict[str, dict[str, int]]   # team -> resource -> max

    # Timing
    first_combat_turn: int | None
    first_harvester_turn: dict[str, int | None]
    first_turret_turn: dict[str, int | None]

    # CPU stats from bot output
    cpu_times: dict[str, list[int]]  # team -> list of exec times in us


# ── Extraction ───────────────────────────────────────────────────────────────

def extract_stats(replay: GameReplay) -> GameStats:
    entities: dict[int, EntityState] = {}
    build_events: list[BuildEvent] = []
    death_events: list[DeathEvent] = []
    damage_events: list[DamageEvent] = []
    resource_snapshots: list[ResourceSnapshot] = []
    turn_snapshots: list[TurnSnapshot] = []

    entities_built: dict[str, dict[str, int]] = {"A": defaultdict(int), "B": defaultdict(int)}
    entities_lost: dict[str, dict[str, int]] = {"A": defaultdict(int), "B": defaultdict(int)}
    total_damage_dealt: dict[str, int] = {"A": 0, "B": 0}
    total_healing: dict[str, int] = {"A": 0, "B": 0}
    peak_resources: dict[str, dict[str, int]] = {
        "A": {"titanium": 0, "axionite": 0},
        "B": {"titanium": 0, "axionite": 0},
    }

    first_combat_turn: int | None = None
    first_harvester_turn: dict[str, int | None] = {"A": None, "B": None}
    first_turret_turn: dict[str, int | None] = {"A": None, "B": None}

    cpu_times: dict[str, list[int]] = {"A": [], "B": []}

    current_resources = ResourceSnapshot(0, 0, 0, 0, 0)

    for turn_idx, turn in enumerate(replay.turns):
        turn_num = turn_idx + 1

        for update in turn.updates:
            if isinstance(update, PlaceEntity):
                ent = update.entity
                team_name = TEAM_NAMES.get(ent.team, "?")

                es = EntityState(
                    id=ent.id, team=ent.team, entity_type=ent.entity_type,
                    pos=ent.pos, hp=ent.hp, max_hp=ent.maxhp, spawn_turn=turn_num,
                )
                entities[ent.id] = es

                if ent.entity_type != "MARKER":
                    entities_built[team_name][ent.entity_type] += 1
                    build_events.append(BuildEvent(
                        turn=turn_num, team=ent.team, entity_type=ent.entity_type,
                        pos=ent.pos, entity_id=ent.id,
                    ))

                # Track firsts
                if ent.entity_type == "HARVESTER" and first_harvester_turn[team_name] is None:
                    first_harvester_turn[team_name] = turn_num
                if ent.entity_type in COMBAT_UNITS and first_turret_turn[team_name] is None:
                    first_turret_turn[team_name] = turn_num

            elif isinstance(update, MoveBuilderBot):
                if update.id in entities:
                    entities[update.id].pos = update.to

            elif isinstance(update, RemoveEntity):
                if update.id in entities:
                    es = entities[update.id]
                    es.death_turn = turn_num
                    team_name = TEAM_NAMES.get(es.team, "?")
                    if es.entity_type != "MARKER":
                        entities_lost[team_name][es.entity_type] += 1
                        death_events.append(DeathEvent(
                            turn=turn_num, entity_id=es.id, entity_type=es.entity_type,
                            team=es.team, pos=es.pos,
                        ))

            elif isinstance(update, UpdateHp):
                if update.id in entities:
                    es = entities[update.id]
                    team_name = TEAM_NAMES.get(es.team, "?")
                    enemy_team = "B" if team_name == "A" else "A"

                    damage_events.append(DamageEvent(
                        turn=turn_num, target_id=es.id, target_type=es.entity_type,
                        target_team=es.team, delta=update.delta,
                    ))

                    if update.delta < 0:
                        # Damage was dealt TO this entity, so the enemy dealt it
                        total_damage_dealt[enemy_team] += abs(update.delta)
                        if first_combat_turn is None:
                            first_combat_turn = turn_num
                    elif update.delta > 0:
                        total_healing[team_name] += update.delta

                    es.hp = max(0, es.hp + update.delta)

            elif isinstance(update, UpdatePlayers):
                current_resources = ResourceSnapshot(
                    turn=turn_num,
                    a_titanium=update.a_titanium,
                    a_axionite=update.a_axionite,
                    b_titanium=update.b_titanium,
                    b_axionite=update.b_axionite,
                )
                resource_snapshots.append(current_resources)

                peak_resources["A"]["titanium"] = max(peak_resources["A"]["titanium"], update.a_titanium)
                peak_resources["A"]["axionite"] = max(peak_resources["A"]["axionite"], update.a_axionite)
                peak_resources["B"]["titanium"] = max(peak_resources["B"]["titanium"], update.b_titanium)
                peak_resources["B"]["axionite"] = max(peak_resources["B"]["axionite"], update.b_axionite)

            elif isinstance(update, BotOutput):
                if update.id in entities:
                    team_name = TEAM_NAMES.get(entities[update.id].team, "?")
                    if update.exec_time_us > 0:
                        cpu_times[team_name].append(update.exec_time_us)

        # Build turn snapshot
        snap = TurnSnapshot(turn=turn_num)
        snap.a_titanium = current_resources.a_titanium
        snap.a_axionite = current_resources.a_axionite
        snap.b_titanium = current_resources.b_titanium
        snap.b_axionite = current_resources.b_axionite

        for es in entities.values():
            if es.death_turn is not None and es.death_turn <= turn_num:
                continue
            cat = ENTITY_CATEGORIES.get(es.entity_type, "other")
            team_name = TEAM_NAMES.get(es.team, "?")
            if team_name == "A":
                if cat == "unit":
                    snap.a_builders += 1
                elif cat == "combat":
                    snap.a_combat += 1
                elif cat == "infra":
                    snap.a_infra += 1
                elif cat == "economy":
                    snap.a_economy += 1
            elif team_name == "B":
                if cat == "unit":
                    snap.b_builders += 1
                elif cat == "combat":
                    snap.b_combat += 1
                elif cat == "infra":
                    snap.b_infra += 1
                elif cat == "economy":
                    snap.b_economy += 1

        turn_snapshots.append(snap)

    winner = {0: "A", 1: "B"}.get(replay.winner, "draw")

    return GameStats(
        map_name=f"{replay.map.width}x{replay.map.height}",
        map_width=replay.map.width,
        map_height=replay.map.height,
        total_turns=len(replay.turns),
        winner=winner,
        build_events=build_events,
        death_events=death_events,
        damage_events=damage_events,
        resource_snapshots=resource_snapshots,
        turn_snapshots=turn_snapshots,
        entities_built=entities_built,
        entities_lost=entities_lost,
        total_damage_dealt=total_damage_dealt,
        total_healing=total_healing,
        peak_resources=peak_resources,
        first_combat_turn=first_combat_turn,
        first_harvester_turn=first_harvester_turn,
        first_turret_turn=first_turret_turn,
        cpu_times=cpu_times,
    )


# ── Output formatters ────────────────────────────────────────────────────────

def print_summary(stats: GameStats) -> None:
    print(f"\n{'=' * 70}")
    print(f"  Replay Stats — {stats.map_name} map, {stats.total_turns} turns, winner: {stats.winner}")
    print(f"{'=' * 70}")

    # Build totals
    print(f"\n  --- Entities Built ---")
    all_types = sorted(set(list(stats.entities_built["A"].keys()) + list(stats.entities_built["B"].keys())))
    if all_types:
        type_width = max(len(t) for t in all_types)
        print(f"  {'Type':{type_width}}  {'A':>5}  {'B':>5}")
        print(f"  {'-' * type_width}  -----  -----")
        for t in all_types:
            a_count = stats.entities_built["A"].get(t, 0)
            b_count = stats.entities_built["B"].get(t, 0)
            print(f"  {t:{type_width}}  {a_count:>5}  {b_count:>5}")

    # Death totals
    print(f"\n  --- Entities Lost ---")
    lost_types = sorted(set(list(stats.entities_lost["A"].keys()) + list(stats.entities_lost["B"].keys())))
    if lost_types:
        type_width = max(len(t) for t in lost_types)
        print(f"  {'Type':{type_width}}  {'A':>5}  {'B':>5}")
        print(f"  {'-' * type_width}  -----  -----")
        for t in lost_types:
            a_count = stats.entities_lost["A"].get(t, 0)
            b_count = stats.entities_lost["B"].get(t, 0)
            print(f"  {t:{type_width}}  {a_count:>5}  {b_count:>5}")

    # Combat stats
    print(f"\n  --- Combat ---")
    print(f"  Total damage dealt:  A={stats.total_damage_dealt['A']:>6}  B={stats.total_damage_dealt['B']:>6}")
    print(f"  Total healing:       A={stats.total_healing['A']:>6}  B={stats.total_healing['B']:>6}")
    print(f"  First combat turn:   {stats.first_combat_turn or 'N/A'}")

    # Economy
    print(f"\n  --- Economy ---")
    print(f"  Peak titanium:       A={stats.peak_resources['A']['titanium']:>6}  B={stats.peak_resources['B']['titanium']:>6}")
    print(f"  Peak axionite:       A={stats.peak_resources['A']['axionite']:>6}  B={stats.peak_resources['B']['axionite']:>6}")
    print(f"  First harvester:     A={stats.first_harvester_turn['A'] or 'N/A':>6}  B={stats.first_harvester_turn['B'] or 'N/A':>6}")
    print(f"  First turret:        A={stats.first_turret_turn['A'] or 'N/A':>6}  B={stats.first_turret_turn['B'] or 'N/A':>6}")

    # CPU stats
    print(f"\n  --- CPU Time (microseconds) ---")
    for team in ["A", "B"]:
        times = stats.cpu_times[team]
        if times:
            avg = sum(times) / len(times)
            mx = max(times)
            p95 = sorted(times)[int(len(times) * 0.95)] if len(times) >= 20 else mx
            print(f"  Team {team}: avg={avg:.0f}us  p95={p95}us  max={mx}us  samples={len(times)}")
        else:
            print(f"  Team {team}: no data")


def print_build_order(stats: GameStats, team: str | None = None) -> None:
    print(f"\n  --- Build Order ---")
    events = stats.build_events
    if team:
        team_int = 0 if team == "A" else 1
        events = [e for e in events if e.team == team_int]

    for e in events:
        t_name = TEAM_NAMES.get(e.team, "?")
        print(f"  T{e.turn:04d}  [{t_name}]  {e.entity_type:20s}  at ({e.pos.x},{e.pos.y})")


def print_economy_graph(stats: GameStats) -> None:
    """Print a simple ASCII economy timeline."""
    if not stats.resource_snapshots:
        print("  No resource data available.")
        return

    print(f"\n  --- Economy Timeline (sampled every 50 turns) ---")
    print(f"  {'Turn':>6}  {'A_Ti':>7}  {'A_Ax':>7}  {'B_Ti':>7}  {'B_Ax':>7}  {'A_Units':>7}  {'B_Units':>7}")
    print(f"  {'----':>6}  {'----':>7}  {'----':>7}  {'----':>7}  {'----':>7}  {'-------':>7}  {'-------':>7}")

    sample_interval = max(1, stats.total_turns // 40)
    for snap in stats.turn_snapshots:
        if snap.turn % sample_interval == 0 or snap.turn == 1 or snap.turn == stats.total_turns:
            a_units = snap.a_builders + snap.a_combat
            b_units = snap.b_builders + snap.b_combat
            print(f"  {snap.turn:>6}  {snap.a_titanium:>7}  {snap.a_axionite:>7}  "
                  f"{snap.b_titanium:>7}  {snap.b_axionite:>7}  {a_units:>7}  {b_units:>7}")


def print_death_timeline(stats: GameStats) -> None:
    """Print a timeline of entity deaths."""
    if not stats.death_events:
        print("  No deaths recorded.")
        return

    print(f"\n  --- Death Timeline ---")
    for e in stats.death_events:
        t_name = TEAM_NAMES.get(e.team, "?")
        print(f"  T{e.turn:04d}  [{t_name}]  {e.entity_type:20s}  at ({e.pos.x},{e.pos.y})")


def export_csv(stats: GameStats, path: Path) -> None:
    """Export turn snapshots to CSV for external analysis."""
    with open(path, "w") as f:
        f.write("turn,a_titanium,a_axionite,b_titanium,b_axionite,"
                "a_builders,a_combat,a_infra,a_economy,"
                "b_builders,b_combat,b_infra,b_economy\n")
        for snap in stats.turn_snapshots:
            f.write(f"{snap.turn},{snap.a_titanium},{snap.a_axionite},"
                    f"{snap.b_titanium},{snap.b_axionite},"
                    f"{snap.a_builders},{snap.a_combat},{snap.a_infra},{snap.a_economy},"
                    f"{snap.b_builders},{snap.b_combat},{snap.b_infra},{snap.b_economy}\n")
    print(f"  Exported {len(stats.turn_snapshots)} rows to {path}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Extract aggregate statistics from .replay26 files.")
    parser.add_argument("replay", help="Path to .replay26 file.")
    parser.add_argument("--build-order", action="store_true", help="Show detailed build order.")
    parser.add_argument("--build-order-team", choices=["A", "B"], help="Filter build order to one team.")
    parser.add_argument("--economy-graph", action="store_true", help="Show economy timeline.")
    parser.add_argument("--deaths", action="store_true", help="Show death timeline.")
    parser.add_argument("--csv", type=Path, help="Export turn snapshots to CSV file.")
    parser.add_argument("--all", action="store_true", help="Show all sections.")
    args = parser.parse_args()

    replay_path = Path(args.replay)
    if not replay_path.exists():
        print(f"File not found: {replay_path}", file=sys.stderr)
        return 1

    print(f"Parsing {replay_path.name}...")
    replay = parse_replay(str(replay_path))
    print(f"Extracting stats from {len(replay.turns)} turns...")
    stats = extract_stats(replay)

    print_summary(stats)

    if args.build_order or args.all:
        print_build_order(stats, team=args.build_order_team)

    if args.economy_graph or args.all:
        print_economy_graph(stats)

    if args.deaths or args.all:
        print_death_timeline(stats)

    if args.csv:
        export_csv(stats, args.csv)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
