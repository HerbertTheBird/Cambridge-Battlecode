from globals import Symmetry


class Comms:
    def __init__(self):
        self.symmetry: Symmetry | None = None

    def encode_symmetry(self, symmetry: Symmetry) -> int:
        return symmetry.value & 0x3

    def read_marker(self, msg: int):
        sym_bits = msg & 0x3
        if sym_bits != 0:
            self.symmetry = Symmetry(sym_bits)