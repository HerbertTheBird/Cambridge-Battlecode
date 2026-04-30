from cambc import Controller, Position, Team

import map_info
from log import log

rc: Controller = None
my_pos: Position = None
my_team: Team = None

def init(c: Controller):
    global rc, my_pos, my_team
    rc = c
    my_pos = rc.get_position()
    my_team = map_info._my_team

def run():
    map_info.update()
