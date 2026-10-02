# BFOS v0.4 — raw Brainfuck

A BIOS workstation whose entire OS logic is literal Brainfuck. The desktop,
shell, parser, filesystem, editor, calculator, clock formatting, and Brainfuck
program interpreter all execute from `kernel.bf`. It contains only `><+-.,[]`:
no native service escape, hidden opcodes, or embedded assembly.

The running image contains a 512-byte assembly boot sector, a 2 KiB assembly
BF interpreter/hardware shim, and the raw BF kernel. Python and PowerShell are
host build tools; neither runs on BFOS.

## Build and run

```powershell
.\dev.ps1                 # build and start QEMU
.\build.ps1               # package the existing literal kernel
.\build.ps1 -Generate     # regenerate literal BF, then build
```

NASM and QEMU paths are configured in the scripts. Python is required for
packaging. The default build consumes `kernel.bf` directly; `raw_kernel.py`
provides readable build-time macros for maintaining that source. Its loops,
array indexing, calls, returns, and state dispatcher are emitted as BF.
`kernel.map.json` maps generated states to raw BF offsets.

The output is `bfos-raw.img`, a 256 MiB raw disk image. QEMU boots it as an IDE
hard disk with 128 MiB RAM. The kernel is 96,977,925 bytes (about 92.5 MiB);
most of that size is literal movement across the BF tape. Boot takes roughly
17 seconds on the development machine. Commands and programs are intentionally
slow, particularly the BF interpreter written in BF.

The image can be written as a **whole disk image** to a USB drive or disk for
a BIOS/CSM computer with extended INT 13h disk access and VGA/BIOS keyboard
support. Writing it replaces that target disk's contents. It has a custom
active MBR partition, not FAT. It has no UEFI loader. QEMU is tested; physical
hardware booting has not been verified. This is a bootable disk image, not a
replacement motherboard firmware image.

Close QEMU before rebuilding or using host disk tools. Rebuilds preserve the
embedded data volume at byte 134,217,728. The first conversion migrates the
existing `bfos-data.img`; later builds use the embedded volume. Back up the
whole `bfos-raw.img` to preserve your work.

## Using it

```
help
ls
cat readme.txt
edit journal.txt
```

In the editor, type to append, Enter adds a newline, and Backspace removes the
last character. **Ctrl-S saves; Esc exits.** Unsaved changes require Y to
discard. Existing files open at their end. A failed save stays visible and
can be retried. In a paged viewer, Space/Enter advances and Esc stops.

```
cat journal.txt
cp journal.txt backup.txt
mv backup.txt old.txt
rm old.txt
run hello.bf
calc 12*7
time
date
mem
reboot
```

Other commands: `echo TEXT`, `clear`, `version`, `about`, and `halt`. Up recalls the
previous command. F1 fills `help`. Esc clears the command line.

Limits: 16 files, 2047 bytes per file, 63 characters per command. Filenames
contain 1–15 lowercase letters, digits, dots, underscores, or hyphens. There
are no directories. The calculator accepts two unsigned 16-bit integers and
`+ - * /`; it rejects overflow, negative results, division by zero, and invalid
expressions. Programs use standard BF, 4096 wrapping byte cells, a wrapping
pointer, bracket validation, and a 10,000-instruction budget. Esc cancels a
program, including one waiting for input.

This remains a single-tasking hobby OS for small notes, arithmetic, file
exchange, and BF experiments. Networking, multitasking, memory protection,
mouse input, and general hardware drivers are absent.

## Host file exchange

With QEMU stopped:

```
python disk.py ls
python disk.py check
python disk.py put local.txt local.txt
python disk.py get journal.txt exported.txt
```

These operate on the embedded volume in `bfos-raw.img`. `put` can replace a
file; `get` refuses to overwrite a host file. CR, LF, and CRLF text are accepted.
For a separate data volume: `python disk.py --image other.img init`. Existing
volumes are validated and preserved, never reformatted by `init`.

The filesystem keeps the previous version of each file in its other bank.
Saves write the inactive bank before committing metadata. Checksums reject
damaged content. Data-write errors preserve the committed file. An interrupted
or failed directory-sector write is not guaranteed to be recoverable.

## What assembly still does

`boot.asm` performs the BIOS disk boot and loads four runtime sectors at
physical 0x8000. `stage2.asm` enables A20, loads the literal BF source from
LBA 5 to physical 0x100000, enters protected mode, and interprets BF using a
65,536-byte wrapping tape. Its only optimizations are generic BF run folding,
bracket linking, and clear-loop folding. It produces interpreter records, not
native OS code. Tape and interpreter locations are calculated from the source
by `package_raw.py`; the current upper bound is 107 MiB RAM.

BF needs a hardware definition for `,` and `.`. Every 16 output bytes form a
raw register packet, copied to tape cells 0–15. Operations are:

| Byte 0 | Primitive | Arguments / result |
|---|---|---|
| 1 | BIOS keyboard read | reply bytes: ASCII, scan code, ready |
| 2 | BIOS keyboard peek | same reply; does not consume the key |
| 4 | Sector read | bytes 1–4: uint32 LBA; 5: drive; 15: failure; `,` reads 512 bytes |
| 5 | Sector write | same registers; next 512 `.` bytes are sector data; 15: failure |
| 6 | VGA text mode | mode 3, bright backgrounds |
| 7 | VGA cell write | 1: column; 2: row; 3: character; 4: attribute |
| 8 | BIOS cursor | 1: column; 2: row |
| 9 | BIOS rectangle scroll | 1–4: top/left/bottom/right; 5: attribute; 6: lines |
| 10 | BIOS RTC | 1: function; 3–6: returned register bytes; 15: failure |
| 11 | Conventional memory | 1–2: KiB, little endian |
| 12 | BIOS reboot | restart |
| 13 | CPU halt | halt |

Drive 255 means the boot drive. The shim has no filenames, filesystem schema,
shell commands, UI layout, editor, calculator, or user BF runner. It switches
to real mode only for firmware calls. Source loading, the BF VM, and this
primitive I/O boundary are the remaining native responsibilities.

The embedded volume is compatible with the original format: `BFOSFS1\0`,
16 directory entries of 24 bytes starting at offset 16 in volume sector 0.
Each entry has a 16-byte terminated name, uint16 length, used byte, bank byte,
uint16 byte-sum checksum, and two reserved bytes. File banks start at relative
LBA `1 + slot*8 + bank*4`, four sectors per bank. All interpretation of this
format happens in BF.

The old assembly workstation is preserved under `legacy-native/` and excluded
from the current build. Original compiler experiments are preserved there.

## Verification

```
python test_raw.py
python -X utf8 test_shell.py
python -X utf8 test_ui.py
python test_legacy_shell.py
python disk.py check
```

QEMU tests use temporary copies of the boot image and temporary data volumes.
They check commands, editor save/discard, reboot/process-restart persistence,
copy/rename/delete, arithmetic, program validation/cancellation/budget, failed
sector writes, full directories, corruption rejection, missing volumes,
desktop frames, command editing, scrolling, clearing, and halt. The UI test
saves `desktop-preview.ppm`.
