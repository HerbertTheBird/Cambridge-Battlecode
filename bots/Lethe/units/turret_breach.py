from cambc import Controller, Position, Team

import map_info

rc: Controller = None
my_pos: Position = None
my_team: Team = None

def init(c: Controller):
    global rc, my_pos, my_team
    rc = c
    my_pos = rc.get_position()
    map_info.init(c)
    my_team = map_info._my_team

def run():
    pass