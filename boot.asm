bits 16
org 0x7C00

start:
    cli
    xor ax, ax
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, 0x7C00
    cld
    sti
    mov [0x500], dl
    ; BIOS extended sector reads: bootable HDD/USB image, not a floppy.
    mov ah, 0x41
    mov bx, 0x55AA
    int 0x13
    jc error
    cmp bx, 0xAA55
    jne error
    test cx, 1
    jz error
    mov si, packet
    mov ah, 0x42
    mov dl, [0x500]
    int 0x13
    jc error
    jmp 0x0800:0
error:
    mov si, message
.print:
    lodsb
    test al, al
    jz .stop
    mov ah, 0x0E
    xor bx, bx
    int 0x10
    jmp .print
.stop:
    cli
    hlt
    jmp .stop
packet:
    db 16, 0
    dw 4, 0, 0x0800
    dq 1
message db 'BFOS: BIOS extended disk reads required / boot read failed',0
times 446-($-$$) db 0
; A custom active partition; this volume is intentionally not FAT.
db 0x80, 0, 2, 0, 0x7F, 0xFE, 0xFF, 0xFF
dd 1, 524287
times 510-($-$$) db 0
dw 0xAA55
