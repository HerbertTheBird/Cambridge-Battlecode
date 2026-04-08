import map_info
from pathing import Pathing
import comms
from cambc import *
import units.builder

rc: Controller = None
nav: Pathing = None
comm_flag = 3
forget = None
def init(c: Controller):
    global rc, nav, forget
    rc = c
    nav = Pathing(rc)
    forget = units.builder.forget[comm_flag]
def score():
    return 0
def run():
    pass