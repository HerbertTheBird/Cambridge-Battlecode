from cambc import Controller


class Turret:
    def __init__(self, c: Controller):
        self.rc = c
        self.type = c.get_entity_type()

    def run(self):
        pass
