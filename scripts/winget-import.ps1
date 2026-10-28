<#
.SYNOPSIS
    Compatibility wrapper for `txacli` (WinGet to TXA Package Importer).
    Prefer using `txacli` directly from your terminal!
#>
$ErrorActionPreference = "Stop"

if (Get-Command txacli -ErrorAction SilentlyContinue) {
    & txacli @args
    exit $LASTEXITCODE
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $RepoRoot "tools\.venv\Scripts\python.exe"
$ScriptPath = Join-Path $PSScriptRoot "winget-import.py"

if (Test-Path $VenvPy) {
    & $VenvPy $ScriptPath @args
} else {
    & python $ScriptPath @args
}
exit $LASTEXITCODE
