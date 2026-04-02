#!/usr/bin/env python3
"""Turn-by-turn frame analyzer for .replay26 files."""
from __future__ import annotations

import argparse
import sys
import os
from typing import Dict, List, Optional, Tuple

# Add replay_parser to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "bots", "_debug_wrapper"))

from replay_parser import (
    parse_replay, PlaceEntity, RemoveEntity, UpdatePlayers,
    MoveBuilderBot, UpdateHp, RawEntity,
)

TEAM = {0: "A", 1: "B"}


def parse_turn_range(s: str) -> Tuple[int, int]:
    lo, hi = s.split("-", 1)
    return int(lo), int(hi)


def main():
    parser = argparse.ArgumentParser(description="Analyze .replay26 frame-by-frame")
    parser.add_argument("replay", help="Path to .replay26 file")
    parser.add_argument("--turns", default=None, help="Turn range, e.g. 0-100")
    parser.add_argument("--team", default=None, choices=["A", "B"], help="Filter by team")
    args = parser.parse_args()

    turn_lo, turn_hi = 0, float("inf")
    if args.turns:
        turn_lo, turn_hi = parse_turn_range(args.turns)

    team_filter = None
    if args.team:
        team_filter = 0 if args.team == "A" else 1

    replay = parse_replay(args.replay)

    print(f"Map: {replay.map.width}x{replay.map.height}")
    print(f"Total turns: {len(replay.turns)}")
    print(f"Winner: Team {TEAM.get(replay.winner, '?')}" if replay.winner >= 0 else "Winner: none")
    print(f"Cores: {', '.join(f'Team {TEAM[c.team]} @ {c.pos}' for c in replay.map.cores)}")
    print()

    # State tracking
    entities = {}  # type: Dict[int, RawEntity]
    build_order = {0: [], 1: []}  # type: Dict[int, List[Tuple[int, str]]]
    last_resources = {0: (0, 0), 1: (0, 0)}  # type: Dict[int, Tuple[int, int]]
    harvester_first_delivery = {}  # type: Dict[int, Optional[int]]

    for turn_idx, turn in enumerate(replay.turns):
        in_range = turn_lo <= turn_idx <= turn_hi
        show_resources = in_range and (turn_idx % 50 == 0)
        events_this_turn: list[str] = []

        for upd in turn.updates:
            if isinstance(upd, PlaceEntity):
                ent = upd.entity
                entities[ent.id] = ent
                if team_filter is not None and ent.team != team_filter:
                    continue
                build_order[ent.team].append((turn_idx, ent.entity_type))
                if ent.entity_type == "HARVESTER":
                    harvester_first_delivery[ent.id] = None
                if in_range:
                    events_this_turn.append(
                        f"  PLACE  Team {TEAM[ent.team]}  {ent.entity_type:<20s} "
                        f"@ {ent.pos}  hp={ent.hp}/{ent.maxhp}"
                    )

            elif isinstance(upd, RemoveEntity):
                ent = entities.pop(upd.id, None)
                if ent is None:
                    if in_range and team_filter is None:
                        events_this_turn.append(f"  REMOVE id={upd.id} (unknown)")
                    continue
                if team_filter is not None and ent.team != team_filter:
                    continue
                if in_range:
                    events_this_turn.append(
                        f"  REMOVE Team {TEAM[ent.team]}  {ent.entity_type:<20s} "
                        f"@ {ent.pos}  (id={ent.id})"
                    )

            elif isinstance(upd, UpdatePlayers):
                last_resources[0] = (upd.a_titanium, upd.a_axionite)
                last_resources[1] = (upd.b_titanium, upd.b_axionite)

                # Detect harvester economy milestones: resource increase hints delivery
                # (approximation -- any resource bump while harvesters exist)
                for hid, delivered_turn in list(harvester_first_delivery.items()):
                    if delivered_turn is not None:
                        continue
                    h = entities.get(hid)
                    if h is None:
                        continue
                    ti, ax = last_resources[h.team]
                    if ti > 0 or ax > 0:
                        harvester_first_delivery[hid] = turn_idx

            elif isinstance(upd, MoveBuilderBot):
                ent = entities.get(upd.id)
                if ent is not None:
                    # Update tracked position
                    entities[upd.id] = RawEntity(
                        id=ent.id, team=ent.team, pos=upd.to, hp=ent.hp,
                        maxhp=ent.maxhp, entity_type=ent.entity_type,
                        direction=ent.direction, ammo_type=ent.ammo_type,
                        ammo_amount=ent.ammo_amount,
                        action_cooldown=ent.action_cooldown,
                        move_cooldown=ent.move_cooldown,
                        marker_value=ent.marker_value,
                        stored_resource=ent.stored_resource,
                        bridge_target=ent.bridge_target,
                    )

            elif isinstance(upd, UpdateHp):
                ent = entities.get(upd.id)
                if ent is not None:
                    entities[upd.id] = RawEntity(
                        id=ent.id, team=ent.team, pos=ent.pos,
                        hp=ent.hp + upd.delta, maxhp=ent.maxhp,
                        entity_type=ent.entity_type, direction=ent.direction,
                        ammo_type=ent.ammo_type, ammo_amount=ent.ammo_amount,
                        action_cooldown=ent.action_cooldown,
                        move_cooldown=ent.move_cooldown,
                        marker_value=ent.marker_value,
                        stored_resource=ent.stored_resource,
                        bridge_target=ent.bridge_target,
                    )

        if events_this_turn:
            print(f"--- Turn {turn_idx} ---")
            for line in events_this_turn:
                print(line)

        if show_resources:
            if team_filter is None or team_filter == 0:
                ti, ax = last_resources[0]
                print(f"  [T{turn_idx}] Team A resources: titanium={ti} axionite={ax}")
            if team_filter is None or team_filter == 1:
                ti, ax = last_resources[1]
                print(f"  [T{turn_idx}] Team B resources: titanium={ti} axionite={ax}")

    # ── Summary ──
    print("\n" + "=" * 60)
    print("BUILD ORDER SUMMARY")
    print("=" * 60)
    for team_id in (0, 1):
        if team_filter is not None and team_id != team_filter:
            continue
        orders = build_order[team_id]
        print(f"\nTeam {TEAM[team_id]} ({len(orders)} buildings):")
        for turn_idx, etype in orders:
            print(f"  T{turn_idx:>4d}  {etype}")

    print("\n" + "=" * 60)
    print("ECONOMY MILESTONES (first resource with harvester alive)")
    print("=" * 60)
    for hid, delivery_turn in sorted(harvester_first_delivery.items()):
        ent = entities.get(hid)
        team_id = ent.team if ent else None
        # Try to recover team from build orders if entity was removed
        if team_id is None:
            for tid in (0, 1):
                if any(True for _, _ in build_order[tid] if _ == "HARVESTER"):
                    pass  # can't reliably recover, skip
            continue
        if team_filter is not None and team_id != team_filter:
            continue
        if delivery_turn is not None:
            print(f"  Harvester id={hid} Team {TEAM[team_id]}: first delivery ~ turn {delivery_turn}")
        else:
            print(f"  Harvester id={hid} Team {TEAM[team_id]}: never delivered")

    print(f"\nFinal resources: A=({last_resources[0][0]}ti, {last_resources[0][1]}ax) "
          f"B=({last_resources[1][0]}ti, {last_resources[1][1]}ax)")


if __name__ == "__main__":
    main()
