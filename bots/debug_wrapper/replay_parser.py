"""
Minimal hand-written protobuf decoder for .replay26 files.

Schema reverse-engineered from the cambc visualizer JavaScript bundle.
No external protobuf library required.

Root message layout:
    Replay {
      map:    Map         (field 1, length-delimited)
      turns:  Turn        (field 3, repeated length-delimited)
      winner: int         (field 4, varint; 0=A, 1=B, absent=no winner)
    }
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ── Low-level protobuf primitives ─────────────────────────────────────────────

def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = data[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _signed32(v: int) -> int:
    """Interpret a varint as a signed 32-bit int (two's complement)."""
    if v >= (1 << 31):
        v -= (1 << 32)
    return v


def _parse_fields(data: bytes) -> dict[int, list]:
    """Parse every field in *data* into {field_number: [raw_values]}."""
    fields: dict[int, list] = {}
    pos, end = 0, len(data)
    while pos < end:
        tag, pos = _read_varint(data, pos)
        field_num = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 0:
            val, pos = _read_varint(data, pos)
        elif wire_type == 2:
            length, pos = _read_varint(data, pos)
            val = data[pos:pos + length]; pos += length
        elif wire_type == 1:
            val = data[pos:pos + 8]; pos += 8
        elif wire_type == 5:
            val = data[pos:pos + 4]; pos += 4
        else:
            raise ValueError(f"Unknown protobuf wire type {wire_type} at byte {pos}")
        fields.setdefault(field_num, []).append(val)
    return fields


# ── Shared types ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Pos:
    x: int
    y: int
    def __repr__(self): return f"({self.x},{self.y})"


def _decode_pos(data: bytes) -> Pos:
    f = _parse_fields(data)
    return Pos(
        x=_signed32(f[1][0]) if 1 in f else 0,
        y=_signed32(f[2][0]) if 2 in f else 0,
    )


# ── Entity decoding ────────────────────────────────────────────────────────────

# Proto field number → entity type name
_ENTITY_TYPE_FIELD = {
    10: "BUILDER_BOT", 11: "CONVEYOR", 12: "SPLITTER", 13: "ARMOURED_CONVEYOR",
    14: "BRIDGE",      15: "HARVESTER", 16: "FOUNDRY", 17: "ROAD",
    18: "BARRIER",     19: "MARKER",   20: "CORE",     21: "GUNNER",
    22: "SENTINEL",    23: "BREACH",   24: "LAUNCHER",
}

# Direction int → name (matches cambc Direction enum)
DIR_NAME = {
    0: "CENTRE", 1: "NORTH", 2: "NORTHEAST", 3: "EAST", 4: "SOUTHEAST",
    5: "SOUTH",  6: "SOUTHWEST", 7: "WEST",   8: "NORTHWEST",
}


@dataclass
class RawEntity:
    id:              int
    team:            int        # 0 = Team A, 1 = Team B
    pos:             Pos
    hp:              int
    maxhp:           int
    entity_type:     str        # e.g. "BUILDER_BOT", "CORE", ...
    direction:       int = 0    # Direction int (0=CENTRE, 1=NORTH, …)
    ammo_type:       int = 0    # ResourceType int
    ammo_amount:     int = 0
    action_cooldown: int = 0
    move_cooldown:   int = 0
    marker_value:    int = 0
    stored_resource: int = 0    # ResourceType int
    bridge_target:   Optional[Pos] = None


def _decode_entity(data: bytes) -> RawEntity:
    f = _parse_fields(data)
    eid    = f[1][0]   if 1 in f else 0
    team   = f[2][0]   if 2 in f else 0
    pos    = _decode_pos(f[3][0]) if 3 in f else Pos(0, 0)
    hp     = f[4][0]   if 4 in f else 0
    maxhp  = f[5][0]   if 5 in f else 0

    entity_type     = "UNKNOWN"
    direction       = 0
    ammo_type       = 0
    ammo_amount     = 0
    action_cooldown = 0
    move_cooldown   = 0
    marker_value    = 0
    stored_resource = 0
    bridge_target   = None

    for fnum, etype in _ENTITY_TYPE_FIELD.items():
        if fnum not in f:
            continue
        entity_type = etype
        tf = _parse_fields(f[fnum][0])

        if etype in ("GUNNER", "SENTINEL", "BREACH"):
            direction   = tf.get(1, [0])[0]
            ammo_type   = tf.get(2, [0])[0]
            ammo_amount = tf.get(3, [0])[0]
        elif etype == "LAUNCHER":
            ammo_type   = tf.get(2, [0])[0]
            ammo_amount = tf.get(3, [0])[0]
        elif etype in ("CONVEYOR", "SPLITTER", "ARMOURED_CONVEYOR"):
            direction       = tf.get(1, [0])[0]
            stored_resource = tf.get(2, [0])[0]
        elif etype == "BRIDGE":
            bridge_target   = _decode_pos(tf[1][0]) if 1 in tf else None
            stored_resource = tf.get(2, [0])[0]
        elif etype == "HARVESTER":
            action_cooldown = tf.get(1, [0])[0]
            stored_resource = tf.get(2, [0])[0]
        elif etype == "CORE":
            action_cooldown = tf.get(1, [0])[0]
        elif etype == "BUILDER_BOT":
            action_cooldown = tf.get(1, [0])[0]
            move_cooldown   = tf.get(2, [0])[0]
        elif etype == "MARKER":
            marker_value    = tf.get(1, [0])[0]
        break

    return RawEntity(
        id=eid, team=team, pos=pos, hp=hp, maxhp=maxhp,
        entity_type=entity_type, direction=direction,
        ammo_type=ammo_type, ammo_amount=ammo_amount,
        action_cooldown=action_cooldown, move_cooldown=move_cooldown,
        marker_value=marker_value, stored_resource=stored_resource,
        bridge_target=bridge_target,
    )


# ── Update message types ───────────────────────────────────────────────────────

@dataclass
class PlaceEntity:
    entity: RawEntity

@dataclass
class MoveBuilderBot:
    id: int
    to: Pos

@dataclass
class RemoveEntity:
    id: int

@dataclass
class UpdateHp:
    id:    int
    delta: int          # signed; add to current HP

@dataclass
class UpdatePlayers:
    a_titanium: int = 0
    a_axionite: int = 0  # refined axionite (the spendable kind)
    b_titanium: int = 0
    b_axionite: int = 0

@dataclass
class SetActionCooldown:
    id:    int
    value: int

@dataclass
class SetMoveCooldown:
    id:    int
    value: int

@dataclass
class BotOutput:
    id:           int
    stdout:       str
    exec_time_us: int  = 0
    tled:         bool = False


def _decode_update(data: bytes):
    """Return the decoded Update variant, or None for unneeded types."""
    f = _parse_fields(data)
    if 1 in f:                                  # placeEntity
        return PlaceEntity(_decode_entity(f[1][0]))
    if 2 in f:                                  # moveBuilderBot
        mf = _parse_fields(f[2][0])
        return MoveBuilderBot(
            id=mf.get(1, [0])[0],
            to=_decode_pos(mf[2][0]) if 2 in mf else Pos(0, 0),
        )
    if 3 in f:                                  # removeEntity
        rf = _parse_fields(f[3][0])
        return RemoveEntity(id=rf.get(1, [0])[0])
    if 4 in f:                                  # distributeResources — not needed
        return None
    if 5 in f:                                  # updateHp
        hf = _parse_fields(f[5][0])
        return UpdateHp(id=hf.get(1, [0])[0], delta=_signed32(hf.get(2, [0])[0]))
    if 6 in f:                                  # updatePlayers
        upf = _parse_fields(f[6][0])
        a_ti = a_ax = b_ti = b_ax = 0
        if 1 in upf:
            ap   = _parse_fields(upf[1][0])
            a_ti = ap.get(1, [0])[0]
            a_ax = ap.get(2, [0])[0]
        if 2 in upf:
            bp   = _parse_fields(upf[2][0])
            b_ti = bp.get(1, [0])[0]
            b_ax = bp.get(2, [0])[0]
        return UpdatePlayers(a_titanium=a_ti, a_axionite=a_ax, b_titanium=b_ti, b_axionite=b_ax)
    if 7 in f:                                  # setActionCooldown
        cf = _parse_fields(f[7][0])
        return SetActionCooldown(id=cf.get(1, [0])[0], value=cf.get(2, [0])[0])
    if 8 in f:                                  # setMoveCooldown
        cf = _parse_fields(f[8][0])
        return SetMoveCooldown(id=cf.get(1, [0])[0], value=cf.get(2, [0])[0])
    if 9 in f:                                  # botOutput
        bf = _parse_fields(f[9][0])
        stdout = bf[2][0].decode("utf-8", errors="replace") if 2 in bf else ""
        return BotOutput(id=bf.get(1, [0])[0], stdout=stdout,
                         exec_time_us=bf.get(3, [0])[0])
    # fireTurret (12), builderAttack (13), indicatorLine (10), indicatorDot (11)
    return None


# ── Map / Replay top-level ────────────────────────────────────────────────────

@dataclass
class GameTurn:
    updates: list   # list of decoded update objects


@dataclass
class GameMap:
    width:          int
    height:         int
    terrain:        list[list[int]]   # terrain[y][x] — ENV ints
    core_positions: list[Pos]


@dataclass
class GameReplay:
    map:    GameMap
    turns:  list[GameTurn]
    winner: int = -1       # -1 = none, 0 = Team A, 1 = Team B


def _decode_map(data: bytes) -> GameMap:
    f = _parse_fields(data)
    width  = f.get(1, [0])[0]
    height = f.get(2, [0])[0]

    terrain: list[list[int]] = []
    for row_data in f.get(3, []):
        rf    = _parse_fields(row_data)
        tiles = list(rf[1][0]) if 1 in rf else []   # packed bytes = env ints
        terrain.append(tiles)

    core_positions = [_decode_pos(cp) for cp in f.get(4, [])]
    return GameMap(width=width, height=height, terrain=terrain, core_positions=core_positions)


def _decode_turn(data: bytes) -> GameTurn:
    f = _parse_fields(data)
    updates = [u for raw in f.get(1, []) for u in [_decode_update(raw)] if u is not None]
    return GameTurn(updates=updates)


def parse_replay(path: str) -> GameReplay:
    """Parse a .replay26 file and return a GameReplay object."""
    with open(path, "rb") as fh:
        data = fh.read()
    f      = _parse_fields(data)
    gmap   = _decode_map(f[1][0]) if 1 in f else GameMap(0, 0, [], [])
    turns  = [_decode_turn(t) for t in f.get(3, [])]
    winner = f[4][0] if 4 in f else -1
    return GameReplay(map=gmap, turns=turns, winner=winner)
