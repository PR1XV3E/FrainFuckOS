"""Exchange files with the embedded BFOS volume or a standalone data volume."""
import argparse
import os
import struct
from pathlib import Path

MAGIC = b"BFOSFS1\0"
SIZE = 1474560
MAX_FILE = 2047
COUNT = 16
ENTRY = 24
DATA_OFFSET = 128 * 1024 * 1024


def volume_offset(data):
    if len(data) == SIZE and data[:8] == MAGIC:
        return 0
    if len(data) >= DATA_OFFSET + SIZE and data[DATA_OFFSET:DATA_OFFSET+8] == MAGIC:
        return DATA_OFFSET
    raise ValueError("Not a BFOS data volume / embedded boot image")


def entries(data):
    volume = volume_offset(data)
    result = []
    names = set()
    for slot in range(COUNT):
        offset = volume + 16 + slot * ENTRY
        raw = data[offset:offset + ENTRY]
        if raw[18] == 0:
            continue
        name = raw[:16].split(b"\0", 1)[0].decode("ascii")
        length = struct.unpack_from("<H", raw, 16)[0]
        bank = raw[19]
        if raw[18] != 1 or bank > 1 or length > MAX_FILE or raw[15] != 0:
            raise ValueError(f"Invalid metadata for slot {slot}")
        validate_name(name)
        if name in names:
            raise ValueError(f"Duplicate file: {name}")
        names.add(name)
        start = volume + (1 + slot * 8 + bank * 4) * 512
        content = data[start:start + length]
        if sum(content) % 65536 != struct.unpack_from("<H", raw, 20)[0]:
            raise ValueError(f"Checksum mismatch: {name}")
        result.append((slot, name, content, bank))
    return result


def validate_name(name):
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789._-"
    if not 1 <= len(name) <= 15 or any(char not in allowed for char in name):
        raise ValueError("Filename must be 1..15 lowercase letters, digits, . _ or -")


def put(image, name, content):
    validate_name(name)
    if len(content) > MAX_FILE:
        raise ValueError("File exceeds 2047 bytes")
    data = image.read_bytes()
    volume = volume_offset(data)
    listing = entries(data)
    existing = next((item for item in listing if item[1] == name), None)
    if existing:
        slot, _, _, old_bank = existing
        bank = old_bank ^ 1
    else:
        used = {item[0] for item in listing}
        slot = next((index for index in range(COUNT) if index not in used), None)
        if slot is None:
            raise ValueError("Directory full")
        bank = 1
    metadata = bytearray(data[volume:volume+512])
    offset = 16 + slot * ENTRY
    record = bytearray(ENTRY)
    record[:len(name)] = name.encode("ascii")
    struct.pack_into("<HBBH", record, 16, len(content), 1, bank, sum(content) % 65536)
    metadata[offset:offset + ENTRY] = record
    with image.open("r+b") as disk:
        disk.seek(volume + (1 + slot * 8 + bank * 4) * 512)
        disk.write(content.ljust(2048, b"\0"))
        disk.flush()
        os.fsync(disk.fileno())
        disk.seek(volume)
        disk.write(metadata)
        disk.flush()
        os.fsync(disk.fileno())


def initialize(image):
    if image.exists():
        entries(image.read_bytes())
        print(f"Preserved existing data disk: {image}")
        return
    if image.name == "bfos-raw.img":
        raise ValueError("Build bfos-raw.img first; init cannot create a boot image")
    image.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(SIZE)
    data[:8] = MAGIC
    with image.open("xb") as disk:
        disk.write(data)
    readme = (
        "Welcome to BFOS v0.4 / raw Brainfuck!\r"
        "Files live inside the boot image. Rebuilding preserves them.\r\r"
        "ls                 list files and sizes\r"
        "cat readme.txt     read a file (Space: next page; Esc: stop)\r"
        "edit notes.txt     create or reopen a text file\r"
        "  Type to append. Backspace removes the last character.\r"
        "  Ctrl-S saves. Esc exits; unsaved changes need confirmation.\r"
        "cp notes.txt copy.txt   copy to a new name\r"
        "mv copy.txt new.txt     rename a file\r"
        "rm new.txt         delete a file\r"
        "run hello.bf       run a Brainfuck program\r"
        "calc 12*7          calculator (unsigned 16-bit integers)\r"
        "time / date / mem  machine information\r"
        "clear / reboot / halt\r\r"
        "Up recalls your last command. F1 fills help.\r"
        "Limits: 16 files, 2047 bytes each, 63 characters per command.\r"
        "Names: 1-15 lowercase letters/digits/dot/underscore/hyphen.\r"
        "Programs: standard Brainfuck, 4096 wrapping byte cells.\r"
        "Esc cancels programs, including keyboard input.\r"
        "Programs stop after 10,000 instructions.\r\r"
        "With QEMU stopped, disk.py can import/export files on the host.\r"
        "Keep a backup of bfos-raw.img to preserve your work.\r"
    )
    put(image, "readme.txt", readme.encode("ascii"))
    put(image, "hello.bf", b"+" * 66 + b".++++.+++++++++.++++.[-]+++++++++++++.\r")
    put(image, "input.bf", b",[.,]\r")
    put(image, "notes.txt", b"My BFOS notes\r")
    print(f"Created data disk with sample files: {image}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=Path(__file__).with_name("bfos-raw.img"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("ls")
    commands.add_parser("check")
    importer = commands.add_parser("put")
    importer.add_argument("source", type=Path)
    importer.add_argument("name")
    exporter = commands.add_parser("get")
    exporter.add_argument("name")
    exporter.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize(args.image)
        elif args.command == "put":
            put(args.image, args.name, args.source.read_bytes())
            print(f"Imported {args.name}")
        else:
            listing = entries(args.image.read_bytes())
            if args.command == "ls":
                for _, name, content, _ in listing:
                    print(f"{name:15} {len(content):4} bytes")
            elif args.command == "check":
                print(f"OK: {len(listing)}/{COUNT} files; metadata and checksums verified")
            else:
                content = next((item[2] for item in listing if item[1] == args.name), None)
                if content is None:
                    raise ValueError("File not found")
                with args.destination.open("xb") as output:
                    output.write(content)
                print(f"Exported {args.name} to {args.destination}")
    except (OSError, ValueError, UnicodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
