"""The QR code in the terminal (gui#247): every module drawn, with a quiet zone of four
modules, read back from the half blocks."""
import segno

from unseenet_tui.identity_dialogs import qr_text

TEXT = "unseenet-copy:1:" + "a" * 56 + ".onion:7-guitar-tunnel"
HALF = {" ": (0, 0), "▀": (1, 0), "▄": (0, 1), "█": (1, 1)}


def test_the_qr_code_is_whole_with_its_quiet_zone():
    lines = [l for l in qr_text(TEXT).plain.split("\n") if l]
    grid = []
    for line in lines:
        top, bottom = zip(*(HALF[c] for c in line))
        grid += [list(top), list(bottom)]
    matrix = [[int(bool(m)) for m in row] for row in segno.make(TEXT, error="m", boost_error=False).matrix]
    size = len(matrix)
    assert all(len(r) == size + 8 for r in grid)
    inner = [r[4:4 + size] for r in grid[4:4 + size]]
    assert inner == matrix                                   # every module, in place
    assert not any(any(r) for r in grid[:4]) and not any(any(r[:4]) or any(r[-4:]) for r in grid)
