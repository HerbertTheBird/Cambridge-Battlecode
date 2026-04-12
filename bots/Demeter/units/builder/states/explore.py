from cambc import Controller

import nav
import vision as vc

from globals import USE_LAUNCHERS
from units.builder.logic import try_build_support_launcher

def run(player, ct: Controller) -> None:
    if (
        not USE_LAUNCHERS
        or len(vc.enemy_units) != 0
        or player.global_titanium < max(120, ct.get_launcher_cost()[0] * 4)
        or ct.get_current_round() - player.last_support_launcher_round < 20
    ):
        return

    my_pos = player.my_pos
    explore_objective = nav.original_destination if nav.destination_type == "adjacent" else nav.destination
    try_build_support_launcher(player, ct, my_pos, [my_pos], explore_objective, min_spacing_sq=20)
