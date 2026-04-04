from cambc import EntityType

class VisionCache:
    __slots__ = (
        'my_team', 'core_pos', 'enemy_core_pos',
        'enemy_units', 'enemy_conveyors', 'enemy_other',
        'harvesters',
        'ally_builder_bots', 'ally_turrets', 'ally_conveyors', 'ally_other',
    )

    def __init__(self):
        self.my_team = None
        self.core_pos = None
        self.enemy_core_pos = None
        self.enemy_units = []
        self.enemy_conveyors = []
        self.enemy_other = []
        self.harvesters = []
        self.ally_builder_bots = []
        self.ally_turrets = []
        self.ally_conveyors = []
        self.ally_other = []

    def refresh(self, ct, my_team):
        self.my_team = my_team
        self.core_pos = None
        self.enemy_core_pos = None

        eu = self.enemy_units; eu.clear()
        ec = self.enemy_conveyors; ec.clear()
        eo = self.enemy_other; eo.clear()
        hv = self.harvesters; hv.clear()
        ab = self.ally_builder_bots; ab.clear()
        at_ = self.ally_turrets; at_.clear()
        ac = self.ally_conveyors; ac.clear()
        ao = self.ally_other; ao.clear()

        # Local aliases for append — avoids LOAD_ATTR per call
        eu_a = eu.append
        ec_a = ec.append
        eo_a = eo.append
        hv_a = hv.append
        ab_a = ab.append
        at_a = at_.append
        ac_a = ac.append
        ao_a = ao.append

        get_team = ct.get_team
        get_etype = ct.get_entity_type
        get_pos = ct.get_position

        _CORE = EntityType.CORE
        _BB = EntityType.BUILDER_BOT
        _GUN = EntityType.GUNNER
        _SEN = EntityType.SENTINEL
        _BRE = EntityType.BREACH
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

            if etype is _HARV:
                hv_a((eid, get_pos(eid), team))
                continue

            if team != my_team:
                pos = get_pos(eid)
                if etype is _CORE:
                    self.enemy_core_pos = pos
                if etype is _CORE or etype is _GUN or etype is _SEN or etype is _BRE or etype is _BB:
                    eu_a((eid, etype, pos))
                elif etype is _CON or etype is _AC or etype is _BRI or etype is _SPL:
                    ec_a((eid, etype, pos))
                else:
                    eo_a((eid, etype, pos))
            else:
                if etype is _CORE:
                    self.core_pos = get_pos(eid)
                elif etype is _BB:
                    ab_a((eid, get_pos(eid)))
                elif etype is _GUN or etype is _SEN or etype is _BRE:
                    at_a((eid, etype, get_pos(eid)))
                elif etype is _CON or etype is _AC or etype is _BRI or etype is _SPL:
                    ac_a((eid, etype, get_pos(eid)))
                else:
                    ao_a((eid, etype, get_pos(eid)))

    def remove_entity(self, entity_id, entity_type, team, pos):
        """Remove a visible entity from the cached lists after we destroy it."""
        _CORE = EntityType.CORE
        _BB = EntityType.BUILDER_BOT
        _GUN = EntityType.GUNNER
        _SEN = EntityType.SENTINEL
        _BRE = EntityType.BREACH
        _CON = EntityType.CONVEYOR
        _AC = EntityType.ARMOURED_CONVEYOR
        _BRI = EntityType.BRIDGE
        _SPL = EntityType.SPLITTER
        _HARV = EntityType.HARVESTER

        if entity_type is _HARV:
            item = (entity_id, pos, team)
            if item in self.harvesters:
                self.harvesters.remove(item)
            return

        if team != self.my_team:
            if entity_type is _CORE or entity_type is _GUN or entity_type is _SEN or entity_type is _BRE or entity_type is _BB:
                item = (entity_id, entity_type, pos)
                if item in self.enemy_units:
                    self.enemy_units.remove(item)
                if entity_type is _CORE and self.enemy_core_pos == pos:
                    self.enemy_core_pos = None
            elif entity_type is _CON or entity_type is _AC or entity_type is _BRI or entity_type is _SPL:
                item = (entity_id, entity_type, pos)
                if item in self.enemy_conveyors:
                    self.enemy_conveyors.remove(item)
            else:
                item = (entity_id, entity_type, pos)
                if item in self.enemy_other:
                    self.enemy_other.remove(item)
            return

        if entity_type is _CORE:
            if self.core_pos == pos:
                self.core_pos = None
        elif entity_type is _BB:
            item = (entity_id, pos)
            if item in self.ally_builder_bots:
                self.ally_builder_bots.remove(item)
        elif entity_type is _GUN or entity_type is _SEN or entity_type is _BRE:
            item = (entity_id, entity_type, pos)
            if item in self.ally_turrets:
                self.ally_turrets.remove(item)
        elif entity_type is _CON or entity_type is _AC or entity_type is _BRI or entity_type is _SPL:
            item = (entity_id, entity_type, pos)
            if item in self.ally_conveyors:
                self.ally_conveyors.remove(item)
        else:
            item = (entity_id, entity_type, pos)
            if item in self.ally_other:
                self.ally_other.remove(item)