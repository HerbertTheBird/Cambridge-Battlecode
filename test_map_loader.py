import sys


def load_map_walls(filename):
    with open(filename, "rb") as f:
        data = f.read()

    def read_varint(buf, pos):
        result, shift = 0, 0
        while True:
            b = buf[pos]
            pos += 1
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                return result, pos
            shift += 7

    def read_tag(buf, pos):
        tag, pos = read_varint(buf, pos)
        return tag >> 3, tag & 7, pos

    def skip(buf, pos, wire):
        if wire == 0:
            _, pos = read_varint(buf, pos)
        elif wire == 1:
            pos += 8
        elif wire == 2:
            length, pos = read_varint(buf, pos)
            pos += length
        elif wire == 5:
            pos += 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        return pos

    def parse_tile_row(buf):
        row = []
        p = 0
        while p < len(buf):
            field, wire, p = read_tag(buf, p)
            if field == 1 and wire == 2:
                length, p = read_varint(buf, p)
                end = p + length
                while p < end:
                    v, p = read_varint(buf, p)
                    row.append(v == 1)
            elif field == 1 and wire == 0:
                v, p = read_varint(buf, p)
                row.append(v == 1)
            else:
                p = skip(buf, p, wire)
        return row

    width = height = 0
    rows = []
    pos = 0
    while pos < len(data):
        field, wire, pos = read_tag(data, pos)
        if field == 1 and wire == 0:
            width, pos = read_varint(data, pos)
        elif field == 2 and wire == 0:
            height, pos = read_varint(data, pos)
        elif field == 3 and wire == 2:
            length, pos = read_varint(data, pos)
            rows.append(parse_tile_row(data[pos:pos + length]))
            pos += length
        else:
            pos = skip(data, pos, wire)

    if width and height:
        assert len(rows) == height and all(len(r) == width for r in rows), \
            f"map shape mismatch: expected {height}x{width}"
    return rows


def print_ascii(walls):
    print(f"size: {len(walls[0])} x {len(walls)}")
    for row in walls:
        print("".join("#" if w else "." for w in row))


def draw_matplotlib(walls, title):
    import matplotlib.pyplot as plt
    grid = [[1 if w else 0 for w in row] for row in walls]
    plt.figure(figsize=(6, 6))
    plt.imshow(grid, cmap="Greys", interpolation="nearest", origin="upper")
    plt.title(title)
    plt.gca().set_aspect("equal")
    plt.grid(False)
    plt.show()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "maps/minimaze.map26"
    walls = load_map_walls(path)
    print_ascii(walls)
    try:
        draw_matplotlib(walls, path)
    except ImportError:
        print("(matplotlib not installed — skipping plot)")
