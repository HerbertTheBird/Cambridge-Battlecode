from cambc import Environment, Position
import cambc_pb2


def load_map26(filename):
    # Read protobuf file
    game_map = cambc_pb2.Map()

    with open(filename, "rb") as f:
        game_map.ParseFromString(f.read())

    width = game_map.width
    height = game_map.height

    # Initialize grid with EMPTY
    grid = [
        [Environment.EMPTY for x in range(width)]
        for y in range(height)
    ]

    # Tile enum values from proto:
    # 0 EMPTY
    # 1 WALL
    # 2 TITANIUM
    # 3 AXIONITE

    for y, row in enumerate(game_map.rows):
        for x, tile in enumerate(row.tiles):
            if tile == 0:
                grid[y][x] = Environment.EMPTY
            elif tile == 1:
                grid[y][x] = Environment.WALL
            elif tile == 2:
                grid[y][x] = Environment.ORE_TITANIUM
            elif tile == 3:
                grid[y][x] = Environment.ORE_AXIONITE
            else:
                raise ValueError(f"Unknown tile value {tile} at ({x}, {y})")

    # Spawn/core positions
    spawn_positions = []

    for core in game_map.cores:
        spawn_positions.append(
            Position(core.position.x, core.position.y)
        )
    return grid, spawn_positions

grid, spawns = load_map26("maps/battlebot.map26")

print("Height:", len(grid))
print("Width:", len(grid[0]))
print("Spawns:", spawns)
print(grid)