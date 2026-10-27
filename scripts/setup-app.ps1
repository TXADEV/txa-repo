<#
.SYNOPSIS  Stores the GitHub App credentials of the TXA bot as repository secrets.
.EXAMPLE   .\scripts\setup-app.ps1 -AppId 1234567 -KeyFile .\txa-repo.private-key.pem
#>
param(
    [Parameter(Mandatory)][string]$AppId,
    [Parameter(Mandatory)][string]$KeyFile,
    [string]$Repo = 'TXAVL/txa-repo'
)
$ErrorActionPreference = 'Stop'
if ($AppId -notmatch '^\d+$') { throw 'AppId must be the numeric App ID shown on the App page.' }
if (-not (Test-Path $KeyFile)) { throw "No such file: $KeyFile" }
if ((Get-Content $KeyFile -TotalCount 1) -notmatch 'BEGIN (RSA )?PRIVATE KEY') { throw 'That does not look like a .pem private key.' }
gh auth status | Out-Null
$AppId | gh secret set TXA_APP_ID --repo $Repo
Get-Content $KeyFile -Raw | gh secret set TXA_APP_PRIVATE_KEY --repo $Repo
Write-Host "Done. Secrets set on $Repo. Delete $KeyFile from this machine now." -ForegroundColor Green
