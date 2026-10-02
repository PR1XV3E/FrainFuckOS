"""Package committed raw BF and the BIOS shim; preserve the embedded volume."""
from pathlib import Path
import argparse
import re
from disk import MAGIC, SIZE, initialize, entries

ROOT = Path(__file__).resolve().parent
DATA_OFFSET = 128 * 1024 * 1024
IMAGE_SIZE = 256 * 1024 * 1024

def prepare():
    code = (ROOT / "kernel.bf").read_bytes()
    if not code or set(code)-set(b"><+-.,[]"):
        raise ValueError("kernel.bf must contain only ><+-.,[]")
    depth = 0
    maximum_depth = 0
    for ch in code:
        if ch == 91: depth += 1
        elif ch == 93: depth -= 1
        if depth < 0: raise ValueError("Unmatched closing bracket")
        maximum_depth = max(maximum_depth,depth)
    if depth: raise ValueError("Unmatched opening bracket")
    records = sum(1 for _ in re.finditer(rb'[<>]+|[+-]+|[.,\[\]]',code))
    token_base = (0x100000+((len(code)+32767)//32768*32768)+4095)&~4095
    token_limit = token_base+records*8+8
    tape_base = (token_limit+4095)&~4095
    bracket_base = tape_base+65536
    required = bracket_base+maximum_depth*4+4096
    (ROOT / "kernel_config.inc").write_text(
        f"KERNEL_BYTES equ {len(code)}\nKERNEL_SECTORS equ {(len(code)+32767)//32768*64}\n"
        f"VM_TOKENS equ {token_base}\nVM_TOKEN_LIMIT equ {token_limit}\n"
        f"VM_TAPE equ {tape_base}\nVM_BRACKETS equ {bracket_base}\n"
        f"VM_REQUIRED_BYTES equ {required}\n",
        encoding="ascii")
    print(f"VM RAM bound: {(required+1048575)//1048576} MiB (use -m 128)")

def package():
    code = (ROOT / "kernel.bf").read_bytes()
    if not code or set(code) - set(b"><+-.,[]"):
        raise ValueError("kernel.bf must contain only the eight literal BF instructions")
    config = (ROOT / "kernel_config.inc").read_text()
    if f"KERNEL_BYTES equ {len(code)}\n" not in config:
        raise ValueError("kernel_config.inc is stale; run raw_kernel.py")
    if len(code) + 1024*1024 >= DATA_OFFSET:
        raise ValueError("Raw BF exceeds the VM's 127 MiB source area")
    boot = (ROOT / "boot.bin").read_bytes()
    runtime = (ROOT / "stage2.bin").read_bytes()
    if len(boot) != 512 or len(runtime) != 2048 or boot[510:] != b"\x55\xaa":
        raise ValueError("Invalid boot/runtime layout")
    image = ROOT / "bfos-raw.img"
    if image.exists():
        with image.open("rb") as source:
            source.seek(DATA_OFFSET)
            volume = source.read(SIZE)
            if not volume and image.stat().st_size == 128*1024*1024:
                source.seek(64*1024*1024)
                volume = source.read(SIZE)
        entries(volume)
        print("Preserved embedded data volume")
    else:
        old = ROOT / "bfos-data.img"
        if not old.exists():
            initialize(old)
        volume = old.read_bytes()
        entries(volume)
        print("Migrated data volume from bfos-data.img")
    # Stage the complete image before replacing the previous boot image.
    temporary = image.with_suffix(".building")
    with temporary.open("wb") as target:
        target.truncate(IMAGE_SIZE)
        target.seek(0)
        target.write(boot)
        target.write(runtime)
        target.write(code)
        target.write(b"\0" * ((-len(code)) % 32768))
        target.seek(DATA_OFFSET)
        target.write(volume)
    temporary.replace(image)
    print(f"Built {image.name}: raw BF {len(code):,} bytes; native shim {len(runtime):,} bytes")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare",action="store_true")
    options = parser.parse_args()
    if options.prepare: prepare()
    else: package()
