
import sys

from cambc import Position

def is_in_vision(my_pos, pos):
    return my_pos.distance_squared(pos) <= 20

class Player:
    def __init__(self):
        pass

    def run(self, ct) -> None:
        try:
            pos = Position(5, 5)
            my_pos = ct.get_position()
            
            before = ct.get_cpu_time_elapsed()

            after = ct.get_cpu_time_elapsed()
            print("distance_squared: ", after - before)
            
            before = ct.get_cpu_time_elapsed()

            after = ct.get_cpu_time_elapsed()
            print("distance_squared with self: ", after - before)
            

        except Exception as e:
            print(f"Error: {e} on turn {ct.get_current_round()} by ID: {ct.get_id()}", file=sys.stderr)
