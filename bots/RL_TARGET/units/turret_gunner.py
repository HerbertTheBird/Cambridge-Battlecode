from cambc import Controller
rc = None
def init(c: Controller):
    global rc
    rc = c
def run():
    target = rc.get_gunner_target()
    if rc.can_fire(target):
        rc.fire(target)