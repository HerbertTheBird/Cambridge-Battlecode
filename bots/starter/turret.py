from cambc import Controller
rc = None
type = None
def run(self):
    pass
def init(self, c: Controller):
    global rc, type
    rc = c
    type = c.get_entity_type()
