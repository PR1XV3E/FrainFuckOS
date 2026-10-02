"""Audit literal BF, tape/RAM bounds, flashed layout, and host volume offsets."""
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from disk import DATA_OFFSET, SIZE, entries, put
from package_raw import IMAGE_SIZE

ROOT = Path(__file__).resolve().parent
code = (ROOT / "kernel.bf").read_bytes()
assert code and not set(code) - set(b"><+-.,[]"), "Non-BF instruction in kernel"
depth = 0
maximum = 0
for byte in code:
    depth += (byte == 91) - (byte == 93)
    assert depth >= 0, "Unmatched closing bracket"
    maximum = max(maximum, depth)
assert depth == 0, "Unmatched opening bracket"

mapping = json.loads((ROOT / "kernel.map.json").read_text())
assert mapping["bytes"] == len(code)
assert mapping["cells"] <= 65536
assert max(mapping["variables"].values()) < 1024
for start, count in mapping["arrays"].values():
    assert start >= 1024 and start + count * 8 <= mapping["cells"]
previous = mapping["blocks"][0]["offset"]
for block in mapping["blocks"]:
    assert block["bytes"] > 0 and block["offset"] == previous
    previous += block["bytes"]
assert previous < len(code)

config = {key: int(value) for key, value in re.findall(
    r"^(\w+) equ (\d+)$", (ROOT / "kernel_config.inc").read_text(), re.M)}
records = sum(1 for _ in re.finditer(rb"[<>]+|[+-]+|[.,\[\]]", code))
assert config["KERNEL_BYTES"] == len(code)
assert config["VM_TOKENS"] >= 0x100000 + config["KERNEL_SECTORS"] * 512
assert config["VM_TOKEN_LIMIT"] >= config["VM_TOKENS"] + records * 8 + 8
assert config["VM_TAPE"] >= config["VM_TOKEN_LIMIT"]
assert config["VM_BRACKETS"] >= config["VM_TAPE"] + 65536
assert config["VM_REQUIRED_BYTES"] >= config["VM_BRACKETS"] + maximum * 4
assert config["VM_REQUIRED_BYTES"] < 128 * 1024 * 1024

boot = (ROOT / "boot.bin").read_bytes()
shim = (ROOT / "stage2.bin").read_bytes()
assert len(boot) == 512 and boot[-2:] == b"\x55\xaa"
assert len(shim) == 2048
image = ROOT / "bfos-raw.img"
assert image.stat().st_size == IMAGE_SIZE
with image.open("rb") as disk:
    assert disk.read(512) == boot
    assert disk.read(2048) == shim
    assert disk.read(len(code)) == code, "Image kernel differs from raw source"
    disk.seek(DATA_OFFSET)
    volume = disk.read(SIZE)
entries(volume)
assembly = (ROOT / "stage2.asm").read_text()
active = "\n".join(line.split(";", 1)[0] for line in assembly.splitlines())
assert re.findall(r'%include\s+"([^"]+)"', active) == ["kernel_config.inc"]
assert not any(token in active.lower() for token in (
    "services.asm", "ui.asm", "bfosfs1", "cmd_edit", "cmd_calc", "cmd_run", "shell"))

with tempfile.TemporaryDirectory() as directory:
    copy = Path(directory) / "exchange.img"
    shutil.copyfile(image, copy)
    original = entries(copy.read_bytes())
    name = original[0][1]
    with copy.open("rb") as disk:
        prefix_hash = hashlib.sha256(disk.read(DATA_OFFSET)).digest()
        disk.seek(DATA_OFFSET + SIZE)
        suffix_hash = hashlib.sha256(disk.read()).digest()
    put(copy, name, b"host import through embedded offset\r\n")
    assert next(row[2] for row in entries(copy.read_bytes()) if row[1] == name) == (
        b"host import through embedded offset\r\n")
    with copy.open("rb") as disk:
        assert hashlib.sha256(disk.read(DATA_OFFSET)).digest() == prefix_hash
        disk.seek(DATA_OFFSET + SIZE)
        assert hashlib.sha256(disk.read()).digest() == suffix_hash
print("PASS: eight-op BF, brackets, tape/RAM bounds, native isolation, flash layout, host file exchange")
