param([switch]$Generate)
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    if ($Generate) {
        python raw_kernel.py
        if ($LASTEXITCODE -ne 0) { throw "Raw BF generation failed" }
    }
    python package_raw.py --prepare
    if ($LASTEXITCODE -ne 0) { throw "Raw BF validation failed" }
    $nasmPath = "C:\Users\meani\AppData\Local\bin\NASM\nasm.exe"
    & $nasmPath -f bin boot.asm -o boot.bin
    if ($LASTEXITCODE -ne 0) { throw "Boot assembly failed" }
    & $nasmPath -f bin stage2.asm -o stage2.bin -l runtime.lst
    if ($LASTEXITCODE -ne 0) { throw "Hardware shim assembly failed" }
    python package_raw.py
    if ($LASTEXITCODE -ne 0) { throw "Image packaging failed" }
}
finally { Pop-Location }
