from cambc import Controller

# Sabotage doesn't have state-specific logic
# (since we check adjacent sabotage positions at the end of each turn and set destination in decide_state)

def run(player, ct: Controller) -> None:
    pass