param([switch]$Generate)
$ErrorActionPreference = "Stop"
& "$PSScriptRoot\build_raw.ps1" -Generate:$Generate
