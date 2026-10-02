"""Verify the real desktop and command input in headless QEMU."""
import tempfile
from pathlib import Path
from disk import initialize
from raw_test_support import Machine, ROOT

with tempfile.TemporaryDirectory() as directory:
    image = Path(directory) / "data.img"
    initialize(image)
    with Machine(image) as machine:
        def framed():
            data = machine.vga()
            rows = [data[i*160:(i+1)*160:2].decode("cp437") for i in range(25)]
            assert "BFOS / Raw Brainfuck Desktop" in rows[0]
            assert rows[4][2] == "╔" and rows[22][77] == "╝"
            assert all(rows[i][2] == "║" and rows[i][77] == "║" for i in range(5, 22))
            assert data[(6*80+3)*2+1] == 0xF1
            return "\n".join(rows)
        framed()
        machine.line("echo hello")
        machine.wait_for("hello")
        machine.line("versiox\bn")
        machine.wait_for("BFOS v0.4")
        assert "Unknown command" not in framed()
        for _ in range(7):
            machine.line("help")
        assert "FILES: ls" in framed()
        machine.line("clear")
        assert "FILES: ls" not in framed()
        machine.line("help")
        machine.screenshot(ROOT / "desktop-preview.ppm")
        machine.line("halt")
        machine.wait_for("HALTED")
        assert "Halted." in framed()
print("PASS: desktop, commands, editing, scrolling, clearing, halted status")
