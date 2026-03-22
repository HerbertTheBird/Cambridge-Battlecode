from cambc import Controller
class Turret:
    def run(self):
        pass
    def __init__(self, c: Controller):
        self.rc = c
        self.type = c.get_entity_type()
    