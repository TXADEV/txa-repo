<#
.SYNOPSIS
    Installs the TXA toolchain (txapm, txacreate) for the current user.

.DESCRIPTION
    One-liner (PowerShell 5.1 or 7+, no admin rights needed):

        irm https://raw.githubusercontent.com/TXAVL/txa-repo/main/install.ps1 | iex

    With parameters:

        & ([scriptblock]::Create((irm https://raw.githubusercontent.com/TXAVL/txa-repo/main/install.ps1))) -Version 1.0.0

    Environment overrides (useful with the iex form): TXA_VERSION, TXA_HOME, TXA_REPO.

    Layout created under %LOCALAPPDATA%\TXA (or TXA_HOME):
        bin\      txapm.exe, txacreate.exe   (added to the user PATH)
        logs\     installer logs written by txapm
        state\    installed.json
        config.json, credentials.bin (DPAPI-encrypted, created by `txacreate login`)
#>
[CmdletBinding()]
param(
    [string]$Version = $(if ($env:TXA_VERSION) { $env:TXA_VERSION } else { 'latest' }),
    [string]$InstallDir = $(if ($env:TXA_HOME) { $env:TXA_HOME } else { Join-Path $env:LOCALAPPDATA 'TXA' }),
    [string]$Repo = $(if ($env:TXA_REPO) { $env:TXA_REPO } else { 'TXAVL/txa-repo' }),
    [switch]$NoPath,
    [switch]$Uninstall
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is far faster without the progress bar on 5.1

function Write-Step([string]$Message) { Write-Host "-> $Message" -ForegroundColor Cyan }
function Write-Done([string]$Message) { Write-Host "[ok] $Message" -ForegroundColor Green }

function Get-UserPath {
    # Read the raw value so entries such as %USERPROFILE%\bin are not expanded and lost.
    $key = Get-Item -LiteralPath 'HKCU:\Environment'
    return [string]$key.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
}

function Set-UserPath([string]$Value) {
    New-ItemProperty -LiteralPath 'HKCU:\Environment' -Name 'Path' -Value $Value -PropertyType ExpandString -Force | Out-Null
}

function Test-SamePath([string]$A, [string]$B) {
    return ([Environment]::ExpandEnvironmentVariables($A).TrimEnd('\')) -ieq ($B.TrimEnd('\'))
}

if ([Environment]::OSVersion.Platform -ne 'Win32NT') {
    throw 'TXA installs Windows software; run this script on Windows.'
}

$BinDir = Join-Path $InstallDir 'bin'

# ------------------------------------------------------------------ uninstall
if ($Uninstall) {
    Write-Step "Removing $BinDir from the user PATH"
    $entries = @((Get-UserPath) -split ';' | Where-Object { $_ -and -not (Test-SamePath $_ $BinDir) })
    Set-UserPath ($entries -join ';')
    [Environment]::SetEnvironmentVariable('TXA_HOME', $null, 'User')  # also broadcasts the change
    if (Test-Path -LiteralPath $InstallDir) {
        Write-Step "Deleting $InstallDir"
        Remove-Item -LiteralPath $InstallDir -Recurse -Force
    }
    Write-Done 'TXA removed. Open a new terminal to refresh PATH.'
    return
}

# ------------------------------------------------------------------ resolve release
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$headers = @{ 'User-Agent' = 'txa-install'; 'Accept' = 'application/vnd.github+json' }

$arch = switch ($env:PROCESSOR_ARCHITECTURE) {
    'ARM64' { 'arm64' }
    'AMD64' { 'x64' }
    default { throw "Unsupported CPU architecture '$($env:PROCESSOR_ARCHITECTURE)' - TXA ships x64 and arm64 builds." }
}
if ($env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { $arch = 'arm64' }

$releaseUrl = if ($Version -eq 'latest') {
    "https://api.github.com/repos/$Repo/releases/latest"
} else {
    "https://api.github.com/repos/$Repo/releases/tags/v$($Version.TrimStart('v'))"
}
Write-Step "Looking up TXA $Version in $Repo"
$release = Invoke-RestMethod -Uri $releaseUrl -Headers $headers

$asset = $release.assets | Where-Object { $_.name -eq "txa-windows-$arch.zip" } | Select-Object -First 1
if (-not $asset -and $arch -eq 'arm64') {
    Write-Host '   no native arm64 build in this release - using x64 (runs under emulation)' -ForegroundColor Yellow
    $arch = 'x64'
    $asset = $release.assets | Where-Object { $_.name -eq 'txa-windows-x64.zip' } | Select-Object -First 1
}
$sums = $release.assets | Where-Object { $_.name -eq 'SHA256SUMS.txt' } | Select-Object -First 1
if (-not $asset -or -not $sums) { throw "Release $($release.tag_name) is missing txa-windows-$arch.zip or SHA256SUMS.txt" }

# ------------------------------------------------------------------ download + verify
$staging = Join-Path ([IO.Path]::GetTempPath()) ("txa-install-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $staging | Out-Null
try {
    $zip = Join-Path $staging $asset.name
    Write-Step "Downloading $($asset.name) ($([Math]::Round($asset.size / 1MB, 1)) MB) from $($release.tag_name)"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -Headers $headers -UseBasicParsing
    $sumsText = (Invoke-WebRequest -Uri $sums.browser_download_url -Headers $headers -UseBasicParsing).Content
    if ($sumsText -is [byte[]]) { $sumsText = [Text.Encoding]::UTF8.GetString($sumsText) }

    $expected = $null
    foreach ($line in ($sumsText -split "`n")) {
        $parts = $line.Trim() -split '\s+', 2
        if ($parts.Count -eq 2 -and $parts[1].TrimStart('*') -eq $asset.name) { $expected = $parts[0].ToUpperInvariant() }
    }
    if (-not $expected) { throw "SHA256SUMS.txt has no entry for $($asset.name)" }
    $actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($actual -ne $expected) { throw "Checksum mismatch for $($asset.name): expected $expected, got $actual" }
    Write-Done "SHA-256 verified ($actual)"

    # -------------------------------------------------------------- lay out the install folder
    foreach ($dir in @($InstallDir, (Join-Path $InstallDir 'logs'), (Join-Path $InstallDir 'state'))) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $newBin = Join-Path $InstallDir 'bin.new'
    $oldBin = Join-Path $InstallDir 'bin.old'
    foreach ($dir in @($newBin, $oldBin)) {
        if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
    }
    Expand-Archive -LiteralPath $zip -DestinationPath $newBin -Force
    if (-not (Test-Path -LiteralPath (Join-Path $newBin 'txapm.exe'))) { throw 'Archive does not contain txapm.exe' }
    try {
        if (Test-Path -LiteralPath $BinDir) { Rename-Item -LiteralPath $BinDir -NewName 'bin.old' }
        Rename-Item -LiteralPath $newBin -NewName 'bin'
    } catch {
        throw "Could not replace $BinDir - close any running txapm/txacreate and retry. ($($_.Exception.Message))"
    }
    if (Test-Path -LiteralPath $oldBin) { Remove-Item -LiteralPath $oldBin -Recurse -Force -ErrorAction SilentlyContinue }
    Get-ChildItem -LiteralPath $BinDir -Recurse | Unblock-File -ErrorAction SilentlyContinue
    Write-Done "Installed to $BinDir"
} finally {
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
}

# ------------------------------------------------------------------ PATH + TXA_HOME
if (-not $NoPath) {
    $current = Get-UserPath
    $entries = @($current -split ';' | Where-Object { $_ })
    if (-not ($entries | Where-Object { Test-SamePath $_ $BinDir })) {
        Set-UserPath ((@($entries) + $BinDir) -join ';')
        Write-Done "Added $BinDir to the user PATH"
    } else {
        Write-Done 'PATH already contains the TXA bin folder'
    }
    if (-not (($env:Path -split ';') | Where-Object { $_ -and (Test-SamePath $_ $BinDir) })) {
        $env:Path = "$env:Path;$BinDir"
    }
}
# Setting a user variable broadcasts WM_SETTINGCHANGE, so new terminals pick up PATH too.
[Environment]::SetEnvironmentVariable('TXA_HOME', $InstallDir, 'User')
$env:TXA_HOME = $InstallDir

$installed = & (Join-Path $BinDir 'txapm.exe') --version
Write-Done "$installed is ready"
Write-Host ''
Write-Host '  txapm install <PackageId>      install a package silently'
Write-Host '  txapm search <text>            search the TXA index'
Write-Host '  txacreate login                sign in to GitHub (device code)'
Write-Host '  txacreate new <installer-url>  publish a new package version'
Write-Host ''
