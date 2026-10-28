<#
.SYNOPSIS
    Convenience wrapper for importing WinGet packages into TXA.
.EXAMPLE
    .\scripts\winget-import.ps1 -s "vscode"
    .\scripts\winget-import.ps1 7zip.7zip --dry-run
    .\scripts\winget-import.ps1 Google.Chrome
#>
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $RepoRoot "tools\.venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
}

if (-not $PythonExe) {
    Write-Error "Python was not found. Please activate tools\.venv or install Python 3.10+."
    exit 1
}

$ScriptPy = Join-Path $PSScriptRoot "winget-import.py"
& $PythonExe $ScriptPy $args
exit $LASTEXITCODE
