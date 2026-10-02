; Only boot/loading, an eight-op BF VM, and primitive hardware I/O.
; No shell, strings/UI layout, filesystem, editor, arithmetic, or user runner.
bits 16
org 0
%include "kernel_config.inc"
BASE equ 0x8000
SOURCE equ 0x100000
TOKENS equ VM_TOKENS
TOKEN_LIMIT equ VM_TOKEN_LIMIT
TAPE equ VM_TAPE
BRACKETS equ VM_BRACKETS

start:
    cli
    cld
    xor ax, ax
    mov ds, ax
    mov al, [0x500]
    mov [cs:boot_drive], al
    ; Fast A20 gate; no firmware dependency after entering the flat VM.
    in al, 0x92
    or al, 2
    and al, 0xFE
    out 0x92, al
    lgdt [cs:gdt_descriptor]
    mov eax, cr0
    or al, 1
    mov cr0, eax
    jmp dword 0x08:(BASE+protected_start)

align 8
gdt:
    dq 0
    dq 0x00CF9A000000FFFF ; flat 32-bit code
    dq 0x00CF92000000FFFF ; flat 32-bit data
    ; 16-bit code based at this runtime, for the BIOS thunk.
    dw 0xFFFF, BASE & 0xFFFF
    db BASE >> 16, 0x9A, 0, 0
    dq 0x000092000000FFFF ; 16-bit data/stack based at zero
gdt_descriptor:
    dw $-gdt-1
    dd BASE+gdt

bits 32
protected_start:
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov fs, ax
    mov gs, ax
    mov ss, ax
    mov esp, 0x7C00
    mov dword [BASE+packet_lba], 5
    mov al, [BASE+boot_drive]
    mov [BASE+disk_drive], al
    mov word [BASE+packet_count], 64
    mov word [BASE+packet_offset], 0
    mov word [BASE+packet_segment], 0x1000
    mov edi, SOURCE
    mov ebp, KERNEL_SECTORS/64
.load:
    mov byte [BASE+bios_operation], 0x42
    call bios_call
    cmp byte [BASE+bios_failed], 0
    jne fatal
    mov esi, 0x10000
    mov ecx, 8192
    rep movsd
    add dword [BASE+packet_lba], 64
    dec ebp
    jnz .load
    ; Decode literal BF into compact VM records. This is interpreter-only
    ; run folding and bracket linking, not OS functionality or native code.
    mov esi, SOURCE
    mov edi, TOKENS
    mov ebp, BRACKETS
.decode:
    cmp esi, SOURCE+KERNEL_BYTES
    jae .decoded
    cmp edi, TOKEN_LIMIT-8
    jae fatal
    lodsb
    cmp al, '>'
    je .move_run
    cmp al, '<'
    je .move_run
    cmp al, '+'
    je .add_run
    cmp al, '-'
    je .add_run
    mov byte [edi], al
    mov dword [edi+4], 0
    cmp al, '['
    je .open
    cmp al, ']'
    je .close
    add edi, 8
    jmp .decode
.move_run:
    xor edx, edx
.move_next:
    cmp al, '>'
    jne .move_left
    inc edx
    jmp .move_check
.move_left:
    dec edx
.move_check:
    cmp esi, SOURCE+KERNEL_BYTES-4
    ja .move_single
    cmp dword [esi], 0x3E3E3E3E
    jne .move_four_left
    add edx, 4
    add esi, 4
    jmp .move_check
.move_four_left:
    cmp dword [esi], 0x3C3C3C3C
    jne .move_single
    sub edx, 4
    add esi, 4
    jmp .move_check
.move_single:
    cmp esi, SOURCE+KERNEL_BYTES
    jae .move_store
    lodsb
    cmp al, '>'
    je .move_next
    cmp al, '<'
    je .move_next
    dec esi
.move_store:
    test edx, edx
    jz .decode
    mov byte [edi], '>'
    mov [edi+4], edx
    add edi, 8
    jmp .decode
.add_run:
    xor edx, edx
.add_next:
    cmp al, '+'
    jne .add_minus
    inc edx
    jmp .add_check
.add_minus:
    dec edx
.add_check:
    cmp esi, SOURCE+KERNEL_BYTES-4
    ja .add_single
    cmp dword [esi], 0x2B2B2B2B
    jne .add_four_minus
    add edx, 4
    add esi, 4
    jmp .add_check
.add_four_minus:
    cmp dword [esi], 0x2D2D2D2D
    jne .add_single
    sub edx, 4
    add esi, 4
    jmp .add_check
.add_single:
    cmp esi, SOURCE+KERNEL_BYTES
    jae .add_store
    lodsb
    cmp al, '+'
    je .add_next
    cmp al, '-'
    je .add_next
    dec esi
.add_store:
    and edx, 255
    jz .decode
    mov byte [edi], '+'
    mov [edi+4], edx
    add edi, 8
    jmp .decode
.open:
    cmp esi, SOURCE+KERNEL_BYTES-2
    ja .ordinary_open
    cmp word [esi], 0x5D2D
    je .clear
    cmp word [esi], 0x5D2B
    jne .ordinary_open
.clear:
    add esi, 2
    mov byte [edi], 'Z'
    add edi, 8
    jmp .decode
.ordinary_open:
    mov [ebp], edi
    add ebp, 4
    add edi, 8
    jmp .decode
.close:
    cmp ebp, BRACKETS
    jbe fatal
    sub ebp, 4
    mov edx, [ebp]
    lea eax, [edi+8]
    mov [edx+4], eax
    add edx, 8
    mov [edi+4], edx
    add edi, 8
    jmp .decode
.decoded:
    cmp ebp, BRACKETS
    jne fatal
    mov byte [edi], 0
    mov edi, TAPE
    xor eax, eax
    mov ecx, 65536/4
    rep stosd
    xor ebx, ebx
    mov esi, TOKENS

interpret:
    mov al, [esi]
    add esi, 8
    test al, al
    jz halt_cpu
    cmp al, '>'
    je move_pointer
    cmp al, '+'
    je add_cell
    cmp al, '['
    je loop_open
    cmp al, ']'
    je loop_close
    cmp al, '.'
    je output
    cmp al, ','
    je input
    cmp al, 'Z'
    je clear_cell
    jmp fatal
move_pointer:
    add bx, word [esi-4]
    jmp interpret
add_cell:
    mov al, [esi-4]
    add [TAPE+ebx], al
    jmp interpret
clear_cell:
    mov byte [TAPE+ebx], 0
    jmp interpret
loop_open:
    cmp byte [TAPE+ebx], 0
    jne interpret
    mov esi, [esi-4]
    jmp interpret
loop_close:
    cmp byte [TAPE+ebx], 0
    je interpret
    mov esi, [esi-4]
    jmp interpret
input:
    xor eax, eax
    mov ax, [BASE+read_position]
    cmp ax, [BASE+read_limit]
    jae .empty
    mov al, [BASE+sector_buffer+eax]
    mov [TAPE+ebx], al
    inc word [BASE+read_position]
    jmp interpret
.empty:
    mov byte [TAPE+ebx], 0
    jmp interpret
output:
    movzx edx, word [BASE+write_position]
    cmp edx, 512
    jae .packet
    mov al, [TAPE+ebx]
    mov [BASE+sector_buffer+edx], al
    inc word [BASE+write_position]
    cmp word [BASE+write_position], 512
    jne interpret
    pushad
    mov byte [BASE+bios_operation], 0x43
    call bios_call
    call disk_status
    popad
    jmp interpret
.packet:
    movzx edx, byte [BASE+stream_position]
    mov al, [TAPE+ebx]
    mov [BASE+stream_buffer+edx], al
    inc byte [BASE+stream_position]
    cmp byte [BASE+stream_position], 16
    jne interpret
    mov byte [BASE+stream_position], 0
    pushad
    mov esi, BASE+stream_buffer
    mov edi, TAPE
    mov ecx, 4
    rep movsd
    popad
    jmp hardware

; Raw I/O packets: every 16 output bytes is a register mailbox request.
; During a disk write, exactly 512 output bytes are instead raw sector data.
hardware:
    pushad
    mov al, [TAPE]
    cmp al, 7
    je .vga_write
    cmp al, 13
    je halt_cpu
    mov esi, TAPE
    mov edi, BASE+mailbox
    mov ecx, 4
    rep movsd
    mov al, [TAPE]
    cmp al, 4
    je .disk_read
    cmp al, 5
    je .disk_write
    mov byte [BASE+bios_operation], 0
    call bios_call
    mov esi, BASE+mailbox
    mov edi, TAPE
    mov ecx, 4
    rep movsd
    cmp byte [TAPE], 1
    je .key_reply
    cmp byte [TAPE], 2
    jne .reply_done
.key_reply:
    mov ax, [TAPE+1]
    mov [BASE+sector_buffer], ax
    mov al, [TAPE+3]
    mov [BASE+sector_buffer+2], al
    mov word [BASE+read_position], 0
    mov word [BASE+read_limit], 3
.reply_done:
    popad
    jmp interpret
.disk_read:
    call disk_setup
    mov byte [BASE+bios_operation], 0x42
    call bios_call
    call disk_status
    mov word [BASE+read_position], 0
    mov word [BASE+read_limit], 512
    mov word [BASE+write_position], 512
    popad
    jmp interpret
.disk_write:
    call disk_setup
    mov word [BASE+write_position], 0
    mov word [BASE+read_position], 512
    mov byte [TAPE+15], 0
    popad
    jmp interpret
.vga_write:
    call vga_address
    mov al, [TAPE+3]
    mov ah, [TAPE+4]
    mov [edi], ax
    popad
    jmp interpret
vga_address:
    movzx edi, byte [TAPE+2]
    cmp edi, 24
    ja fatal
    imul edi, edi, 80
    movzx eax, byte [TAPE+1]
    cmp eax, 79
    ja fatal
    add edi, eax
    lea edi, [0xB8000+edi*2]
    ret
disk_setup:
    mov eax, [TAPE+1]
    mov [BASE+packet_lba], eax
    mov dword [BASE+packet_lba+4], 0
    mov word [BASE+packet_count], 1
    mov word [BASE+packet_offset], sector_buffer
    mov word [BASE+packet_segment], 0x0800
    mov al, [TAPE+5]
    cmp al, 255
    jne .drive
    mov al, [BASE+boot_drive]
.drive:
    mov [BASE+disk_drive], al
    ret
disk_status:
    mov al, [BASE+bios_failed]
    mov [TAPE+15], al
    ret

; Firmware calls require real mode. This thunk carries only raw registers.
bios_call:
    pushad
    cli
    jmp 0x18:leave_protected
bits 16
leave_protected:
    mov ax, 0x20
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov eax, cr0
    and al, 0xFE
    mov cr0, eax
    jmp 0x0800:real_mode
real_mode:
    mov ax, cs
    mov ds, ax
    mov es, ax
    xor ax, ax
    mov ss, ax
    sti
    mov byte [bios_failed], 0
    mov ah, [bios_operation]
    test ah, ah
    jz real_hardware
    mov dl, [disk_drive]
    mov si, packet
    xor al, al
    int 0x13
    jnc return_protected
    mov byte [bios_failed], 1
    jmp return_protected
real_hardware:
    mov al, [mailbox]
    cmp al, 1
    je .key
    cmp al, 2
    je .poll
    cmp al, 6
    je .video
    cmp al, 8
    je .cursor
    cmp al, 9
    je .scroll
    cmp al, 10
    je .rtc
    cmp al, 11
    je .memory
    cmp al, 12
    je .reboot
    jmp return_protected
.key:
    xor ah, ah
    int 0x16
    mov [mailbox+1], al
    mov [mailbox+2], ah
    mov byte [mailbox+3], 1
    jmp return_protected
.poll:
    mov ah, 1
    int 0x16
    jz .no_key
    mov [mailbox+1], al
    mov [mailbox+2], ah
    mov byte [mailbox+3], 1
    jmp return_protected
.no_key:
    mov byte [mailbox+3], 0
    jmp return_protected
.video:
    mov ax, 3
    int 0x10
    mov ax, 0x1003
    xor bx, bx
    int 0x10
    jmp return_protected
.cursor:
    mov dh, [mailbox+2]
    mov dl, [mailbox+1]
    mov ah, 2
    xor bh, bh
    int 0x10
    jmp return_protected
.scroll:
    mov ch, [mailbox+1]
    mov cl, [mailbox+2]
    mov dh, [mailbox+3]
    mov dl, [mailbox+4]
    mov bh, [mailbox+5]
    mov al, [mailbox+6]
    mov ah, 6
    int 0x10
    jmp return_protected
.rtc:
    mov ah, [mailbox+1]
    int 0x1A
    jc .rtc_bad
    mov [mailbox+3], ch
    mov [mailbox+4], cl
    mov [mailbox+5], dh
    mov [mailbox+6], dl
    mov byte [mailbox+15], 0
    jmp return_protected
.rtc_bad:
    mov byte [mailbox+15], 1
    jmp return_protected
.memory:
    int 0x12
    mov [mailbox+1], ax
    jmp return_protected
.reboot:
    int 0x19
    jmp 0xFFFF:0
return_protected:
    cli
    lgdt [gdt_descriptor]
    mov eax, cr0
    or al, 1
    mov cr0, eax
    jmp dword 0x08:(BASE+resume_protected)
bits 32
resume_protected:
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov fs, ax
    mov gs, ax
    mov ss, ax
    popad
    ret
fatal:
    ; Only VM/boot errors reach here; BF owns all user-facing OS messages.
    mov word [0xB8000], 0x4F45
halt_cpu:
    cli
    hlt
    jmp halt_cpu

bits 16
boot_drive db 0
disk_drive db 0x80
bios_operation db 0
bios_failed db 0
read_position dw 512
read_limit dw 512
write_position dw 512
stream_position db 0
stream_buffer times 16 db 0
mailbox times 16 db 0
align 4
packet:
    db 16,0
packet_count dw 4
packet_offset dw sector_buffer
packet_segment dw 0x0800
packet_lba dq 5
; Scratch sector storage is RAM, not part of the flashed runtime.
sector_buffer equ 2048
times 2048-($-$$) db 0
