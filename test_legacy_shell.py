"""Exercise the actual compiled Brainfuck shell with BIOS-style input."""
from pathlib import Path

import subprocess
import sys
import tempfile
root = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory() as directory:
    compiled = Path(directory) / "legacy.bf"
    subprocess.run([sys.executable, str(root / "bfasm.py"), str(root / "kernel_legacy.bfs"), str(compiled)], check=True)
    program = compiled.read_text()
jumps = {}
stack = []
for i, op in enumerate(program):
    if op == "[": stack.append(i)
    elif op == "]":
        start = stack.pop()
        jumps[start] = i
        jumps[i] = start
assert not stack

def run(text):
    tape = bytearray(4096)
    data = iter(text.encode("ascii"))
    output = []
    pc = ptr = 0
    for steps in range(20_000_000):
        if pc == len(program): return "".join(output), True
        op = program[pc]
        if op == ">": ptr = (ptr + 1) % 4096
        elif op == "<": ptr = (ptr - 1) % 4096
        elif op == "+": tape[ptr] = (tape[ptr] + 1) % 256
        elif op == "-": tape[ptr] = (tape[ptr] - 1) % 256
        elif op == ".": output.append(chr(tape[ptr]))
        elif op == ",":
            try: tape[ptr] = next(data)
            except StopIteration: return "".join(output), False
        elif op == "[" and not tape[ptr]: pc = jumps[pc]
        elif op == "]" and tape[ptr]: pc = jumps[pc]
        pc += 1
    raise AssertionError("Shell did not make progress")

out, halted = run("help\rversion\rabout\recho hello\rclear\r\rwat\rversionx\rechoX\rhalt\r")
assert halted
for expected in ["help version about", "BFOS v0.2", "Brainfuck OS", "hello\r", "\x0c", "Halted."]:
    assert expected in out, repr(out)
assert out.count("Unknown command") == 3, repr(out)
assert out.endswith("Halted.\r"), repr(out)
out, halted = run("versiox\bn\recho 12345678901EXCESS\rhalt\r")
assert halted and "Unknown command" not in out, repr(out)
assert "12345678901\r" in out and "EXCESS" not in out, repr(out)
out, halted = run("\b\b\rhalt\r")
assert halted and "Unknown command" not in out
print("PASS: commands, exact matching, blank input, backspace, input limit, halt")
