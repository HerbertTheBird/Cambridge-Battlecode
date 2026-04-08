import map_info
from pathing import Pathing
import comms
import units.builder
from cambc import *

rc: Controller = None
nav: Pathing = None

comm_flag = 2

def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)
def score():
    return 0
def run():
    print("SABOTAGE")
    
