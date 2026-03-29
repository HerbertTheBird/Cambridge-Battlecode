import sys, os
ROOT = r'C:\Users\panti\PycharmProjects\Cambridge-Battlecode'
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, 'bots', 'debug_wrapper'))
import replay_parser as rp

with open(os.path.join(ROOT, 'replays', '22f4ab8b-f660-4ba0-afe8-990245ffdc3f_game_1.replay26'), 'rb') as f:
    raw = f.read()

top = rp._parse_fields(raw)
map_fields = rp._parse_fields(top[1][0])

# Decode core positions with team info
print("=== CorePosition full decode ===")
for cp in map_fields.get(4, []):
    cf = rp._parse_fields(cp)
    core_id = cf.get(1, [0])[0]
    team = cf.get(2, [0])[0]   # absent = 0 (team A)
    pos = rp._decode_pos(cf[3][0]) if 3 in cf else rp.Pos(0,0)
    print(f"  id={core_id}, team={team}, pos={pos}")

print()

# Check BotOutput in first 3 turns to find core unit IDs
print("=== BotOutput unit IDs in first 3 turns ===")
for turn_idx, turn_raw in enumerate(top[3][:3]):
    tf = rp._parse_fields(turn_raw)
    for upd_raw in tf.get(1, []):
        uf = rp._parse_fields(upd_raw)
        if 9 in uf:
            bf = rp._parse_fields(uf[9][0])
            uid = bf.get(1, [0])[0]
            stdout = bf[2][0].decode('utf-8', errors='replace') if 2 in bf else ''
            print(f"  Turn {turn_idx+1}: unit {uid} -> {repr(stdout[:60])}")

print()

# Search for PlaceEntity with CORE type across first 50 turns
print("=== Searching for CORE PlaceEntity in first 50 turns ===")
found_core = False
for turn_idx, turn_raw in enumerate(top[3][:50]):
    tf = rp._parse_fields(turn_raw)
    for upd_raw in tf.get(1, []):
        uf = rp._parse_fields(upd_raw)
        if 1 in uf:
            pe_fields = rp._parse_fields(uf[1][0])
            if 1 in pe_fields:
                e = rp._decode_entity(pe_fields[1][0])
                if e.entity_type == 'CORE':
                    print(f"  Turn {turn_idx+1}: CORE id={e.id} team={e.team} pos={e.pos}")
                    found_core = True
if not found_core:
    print("  No CORE PlaceEntity found in first 50 turns")
