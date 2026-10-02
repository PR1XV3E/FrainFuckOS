param([switch]$Generate)
$ErrorActionPreference = "Stop"
& "$PSScriptRoot\build.ps1" -Generate:$Generate
Write-Host "Launching raw Brainfuck BFOS..."
& "D:\msys2\ucrt64\bin\qemu-system-x86_64.exe" `
    -m 128 -boot c `
    -drive format=raw,file="$PSScriptRoot\bfos-raw.img",if=ide
