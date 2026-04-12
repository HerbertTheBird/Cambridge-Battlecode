from cambc import EntityType

from globals import CONVEYOR_TYPES, TURRET_TYPES
import map as map_mod

enemy_units = []
enemy_conveyors = []
enemy_launchers = []
enemy_other = []

ally_builder_bots = []
ally_turrets = []
ally_conveyors = []
ally_launchers = []
ally_other = []

harvesters = []
ally_builder_mask = 0
enemy_builder_mask = 0

def init():
    pass

def refresh(ct, player):
    global ally_builder_mask, enemy_builder_mask
    my_team = player.my_team
    width = map_mod.width

    eu = enemy_units; eu.clear()
    ec = enemy_conveyors; ec.clear()
    el = enemy_launchers; el.clear()
    eo = enemy_other; eo.clear()
    hv = harvesters; hv.clear()
    ab = ally_builder_bots; ab.clear()
    at_ = ally_turrets; at_.clear()
    ac = ally_conveyors; ac.clear()
    al = ally_launchers; al.clear()
    ao = ally_other; ao.clear()
    ally_builder_mask = 0
    enemy_builder_mask = 0

    eu_a = eu.append
    ec_a = ec.append
    el_a = el.append
    eo_a = eo.append
    hv_a = hv.append
    ab_a = ab.append
    at_a = at_.append
    ac_a = ac.append
    al_a = al.append
    ao_a = ao.append

    get_team = ct.get_team
    get_etype = ct.get_entity_type
    get_pos = ct.get_position

    _CORE = EntityType.CORE
    _BB = EntityType.BUILDER_BOT
    _GUN = EntityType.GUNNER
    _SEN = EntityType.SENTINEL
    _BRE = EntityType.BREACH
    _LAU = EntityType.LAUNCHER
    _CON = EntityType.CONVEYOR
    _AC = EntityType.ARMOURED_CONVEYOR
    _BRI = EntityType.BRIDGE
    _SPL = EntityType.SPLITTER
    _HARV = EntityType.HARVESTER
    _ROAD = EntityType.ROAD
    _MARKER = EntityType.MARKER

    for eid in ct.get_nearby_entities():
        etype = get_etype(eid)
        team = get_team(eid)

        if etype is _MARKER:
            continue

        pos = get_pos(eid)

        if etype is _HARV:
            hv_a((eid, pos, team))
            continue

        if team == my_team:
            if etype is _CORE:
                if player.core_pos is None:
                    player.core_pos = pos
            elif etype is _BB:
                ab_a((eid, pos))
                ally_builder_mask |= 1 << (pos.x + pos.y * width)
            elif etype is _GUN or etype is _SEN or etype is _BRE:
                at_a((eid, etype, pos))
            elif etype is _LAU:
                al_a((eid, etype, pos))
            elif etype is _CON or etype is _AC or etype is _BRI or etype is _SPL:
                ac_a((eid, etype, pos))
            else:
                ao_a((eid, etype, pos))
        else:
            if etype is _CORE:
                if player.enemy_core_pos is None:
                    player.enemy_core_pos = pos
            if etype is _CORE or etype is _GUN or etype is _SEN or etype is _BRE or etype is _BB:
                eu_a((eid, etype, pos))
                if etype is _BB:
                    enemy_builder_mask |= 1 << (pos.x + pos.y * width)
            elif etype is _LAU:
                el_a((eid, etype, pos))
            elif etype is _CON or etype is _AC or etype is _BRI or etype is _SPL:
                ec_a((eid, etype, pos))
            else:
                eo_a((eid, etype, pos))

def remove_entity(player, entity_id, entity_type, team, pos):
    global ally_builder_mask, enemy_builder_mask
    bit = 1 << (pos.x + pos.y * map_mod.width)

    try:
        if entity_type is EntityType.HARVESTER:
            item = (entity_id, pos, team)
            harvesters.remove(item)
            return

        if team == player.my_team:
            if entity_type is EntityType.CORE:
                pass
            elif entity_type is EntityType.BUILDER_BOT:
                item = (entity_id, pos)
                ally_builder_bots.remove(item)
                ally_builder_mask &= ~bit
            elif entity_type in TURRET_TYPES:
                item = (entity_id, entity_type, pos)
                ally_turrets.remove(item)
            elif entity_type is EntityType.LAUNCHER:
                item = (entity_id, entity_type, pos)
                ally_launchers.remove(item)
            elif entity_type in CONVEYOR_TYPES:
                item = (entity_id, entity_type, pos)
                ally_conveyors.remove(item)
            else:
                item = (entity_id, entity_type, pos)
                ally_other.remove(item)
            return

        if entity_type is EntityType.CORE or entity_type is EntityType.BUILDER_BOT or entity_type in TURRET_TYPES:
            item = (entity_id, entity_type, pos)
            enemy_units.remove(item)
            if entity_type is EntityType.BUILDER_BOT:
                enemy_builder_mask &= ~bit
        elif entity_type is EntityType.LAUNCHER:
            item = (entity_id, entity_type, pos)
            enemy_launchers.remove(item)
        elif entity_type in CONVEYOR_TYPES:
            item = (entity_id, entity_type, pos)
            enemy_conveyors.remove(item)
        else:
            item = (entity_id, entity_type, pos)
            enemy_other.remove(item)

    except ValueError:
        pass

def add_entity(player, entity_id, entity_type, team, pos):
    global ally_builder_mask, enemy_builder_mask
    bit = 1 << (pos.x + pos.y * map_mod.width)

    if entity_type is EntityType.MARKER:
        return

    if entity_type is EntityType.HARVESTER:
        item = (entity_id, pos, team)
        if item not in harvesters:
            harvesters.append(item)
        return

    if team == player.my_team:
        if entity_type is EntityType.CORE:
            if player.core_pos is None:
                player.core_pos = pos
            return
        if entity_type is EntityType.BUILDER_BOT:
            item = (entity_id, pos)
            if item not in ally_builder_bots:
                ally_builder_bots.append(item)
            ally_builder_mask |= bit
            return
        if entity_type in TURRET_TYPES:
            item = (entity_id, entity_type, pos)
            if item not in ally_turrets:
                ally_turrets.append(item)
            return
        if entity_type is EntityType.LAUNCHER:
            item = (entity_id, entity_type, pos)
            if item not in ally_launchers:
                ally_launchers.append(item)
            return
        if entity_type in CONVEYOR_TYPES:
            item = (entity_id, entity_type, pos)
            if item not in ally_conveyors:
                ally_conveyors.append(item)
            return
        item = (entity_id, entity_type, pos)
        if item not in ally_other:
            ally_other.append(item)
        return

    if entity_type is EntityType.CORE:
        if player.enemy_core_pos is None:
            player.enemy_core_pos = pos

    item = (entity_id, entity_type, pos)
    if entity_type is EntityType.CORE or entity_type is EntityType.BUILDER_BOT or entity_type in TURRET_TYPES:
        if item not in enemy_units:
            enemy_units.append(item)
        if entity_type is EntityType.BUILDER_BOT:
            enemy_builder_mask |= bit
    elif entity_type is EntityType.LAUNCHER:
        if item not in enemy_launchers:
            enemy_launchers.append(item)
    elif entity_type in CONVEYOR_TYPES:
        if item not in enemy_conveyors:
            enemy_conveyors.append(item)
    elif item not in enemy_other:
        enemy_other.append(item)
