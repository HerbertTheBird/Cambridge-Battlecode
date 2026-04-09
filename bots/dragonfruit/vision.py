from cambc import EntityType

from globals import CONVEYOR_TYPES, TURRET_TYPES

class VisionCache:
    __slots__ = (
        'enemy_units', 'enemy_conveyors', 'enemy_launchers', 'enemy_other',
        'ally_builder_bots', 'ally_turrets', 'ally_conveyors', 'ally_launchers', 'ally_other',
        'harvesters',
    )

    def __init__(self):
        self.enemy_units = []
        self.enemy_conveyors = []
        self.enemy_launchers = []
        self.enemy_other = []

        self.ally_builder_bots = []
        self.ally_turrets = []
        self.ally_conveyors = []
        self.ally_launchers = []
        self.ally_other = []
        
        self.harvesters = []

    def refresh(self, ct, player):
        # Having local aliases speeds up runtime
        my_team = player.my_team

        eu = self.enemy_units; eu.clear()
        ec = self.enemy_conveyors; ec.clear()
        el = self.enemy_launchers; el.clear()
        eo = self.enemy_other; eo.clear()
        hv = self.harvesters; hv.clear()
        ab = self.ally_builder_bots; ab.clear()
        at_ = self.ally_turrets; at_.clear()
        ac = self.ally_conveyors; ac.clear()
        al = self.ally_launchers; al.clear()
        ao = self.ally_other; ao.clear()

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

        # Iterate over entities and add to appropriate list
        for eid in ct.get_nearby_entities():
            etype = get_etype(eid)
            team = get_team(eid)

            # Don't add markers to list since they can be safely built over
            if etype is _MARKER:
                continue
            
            pos = get_pos(eid)

            # Add harvesters
            if etype is _HARV:
                hv_a((eid, pos, team))
                continue

            # Add ally entities
            if team == my_team:
                if etype is _CORE:
                    if player.core_pos is None:
                        player.core_pos = pos
                elif etype is _BB:
                    ab_a((eid, pos))
                elif etype is _GUN or etype is _SEN or etype is _BRE:
                    at_a((eid, etype, pos))
                elif etype is _LAU:
                    al_a((eid, etype, pos))
                elif etype is _CON or etype is _AC or etype is _BRI or etype is _SPL:
                    ac_a((eid, etype, pos))
                else:
                    ao_a((eid, etype, pos))
            
            # Add enemy entities
            else:
                if etype is _CORE:
                    if player.enemy_core_pos is None:
                        player.enemy_core_pos = pos
                if etype is _CORE or etype is _GUN or etype is _SEN or etype is _BRE or etype is _BB:
                    eu_a((eid, etype, pos))
                elif etype is _LAU:
                    el_a((eid, etype, pos))
                elif etype is _CON or etype is _AC or etype is _BRI or etype is _SPL:
                    ec_a((eid, etype, pos))
                else:
                    eo_a((eid, etype, pos))

    def remove_entity(self, player, entity_id, entity_type, team, pos):
        """Remove a visible entity from the cached lists after we destroy it."""

        try:
            # Remove harvester
            if entity_type is EntityType.HARVESTER:
                item = (entity_id, pos, team)
                self.harvesters.remove(item)
                return

            # Remove ally entity from appropriate list
            if team == player.my_team:
                if entity_type is EntityType.CORE:
                    pass
                elif entity_type is EntityType.BUILDER_BOT:
                    item = (entity_id, pos)
                    self.ally_builder_bots.remove(item)
                elif entity_type in TURRET_TYPES:
                    item = (entity_id, entity_type, pos)
                    self.ally_turrets.remove(item)
                elif entity_type is EntityType.LAUNCHER:
                    item = (entity_id, entity_type, pos)
                    self.ally_launchers.remove(item)
                elif entity_type in CONVEYOR_TYPES:
                    item = (entity_id, entity_type, pos)
                    self.ally_conveyors.remove(item)
                else:
                    item = (entity_id, entity_type, pos)
                    self.ally_other.remove(item)
                return

            # Remove enemy entity from appropriate list
            if entity_type is EntityType.CORE or entity_type is EntityType.BUILDER_BOT or entity_type in TURRET_TYPES:
                item = (entity_id, entity_type, pos)
                self.enemy_units.remove(item)
            elif entity_type is EntityType.LAUNCHER:
                item = (entity_id, entity_type, pos)
                self.enemy_launchers.remove(item)
            elif entity_type in CONVEYOR_TYPES:
                item = (entity_id, entity_type, pos)
                self.enemy_conveyors.remove(item)
            else:
                item = (entity_id, entity_type, pos)
                self.enemy_other.remove(item)

        except ValueError:
            # Item not found in the list, safe to ignore since we wanted to remove it anyways
            pass

    def add_entity(self, player, entity_id, entity_type, team, pos):
        """Add a visible entity to the cached lists after we create it."""

        if entity_type is EntityType.MARKER:
            return

        if entity_type is EntityType.HARVESTER:
            item = (entity_id, pos, team)
            if item not in self.harvesters:
                self.harvesters.append(item)
            return

        if team == player.my_team:
            if entity_type is EntityType.CORE:
                if player.core_pos is None:
                    player.core_pos = pos
                return
            if entity_type is EntityType.BUILDER_BOT:
                item = (entity_id, pos)
                if item not in self.ally_builder_bots:
                    self.ally_builder_bots.append(item)
                return
            if entity_type in TURRET_TYPES:
                item = (entity_id, entity_type, pos)
                if item not in self.ally_turrets:
                    self.ally_turrets.append(item)
                return
            if entity_type is EntityType.LAUNCHER:
                item = (entity_id, entity_type, pos)
                if item not in self.ally_launchers:
                    self.ally_launchers.append(item)
                return
            if entity_type in CONVEYOR_TYPES:
                item = (entity_id, entity_type, pos)
                if item not in self.ally_conveyors:
                    self.ally_conveyors.append(item)
                return
            item = (entity_id, entity_type, pos)
            if item not in self.ally_other:
                self.ally_other.append(item)
            return

        if entity_type is EntityType.CORE:
            if player.enemy_core_pos is None:
                player.enemy_core_pos = pos

        item = (entity_id, entity_type, pos)
        if entity_type is EntityType.CORE or entity_type is EntityType.BUILDER_BOT or entity_type in TURRET_TYPES:
            if item not in self.enemy_units:
                self.enemy_units.append(item)
        elif entity_type is EntityType.LAUNCHER:
            if item not in self.enemy_launchers:
                self.enemy_launchers.append(item)
        elif entity_type in CONVEYOR_TYPES:
            if item not in self.enemy_conveyors:
                self.enemy_conveyors.append(item)
        elif item not in self.enemy_other:
            self.enemy_other.append(item)
