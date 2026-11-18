# Silent per-user install of the Inno Setup installer; exports LOBSTERVM_CLI.
param([Parameter(Mandatory)][string]$InstallerDir)
$ErrorActionPreference = 'Stop'
$installer = Get-ChildItem -Path $InstallerDir -Filter 'LobsterVM-Setup-*-x64.exe' | Select-Object -First 1
if (-not $installer) { throw "no installer in $InstallerDir" }
$logFile = Join-Path $InstallerDir 'install.log'
$process = Start-Process -FilePath $installer.FullName -Wait -PassThru -ArgumentList @(
  '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/LOG=$logFile")
if ($process.ExitCode -ne 0) { throw "installer exited with $($process.ExitCode)" }
$appDir = Join-Path $env:LOCALAPPDATA 'Programs\LobsterVM'
$cli = Join-Path $appDir 'lobstervm-cli.exe'
if (-not (Test-Path $cli)) { throw "$cli missing after install" }
Write-Host "installed to $appDir"
if ($env:GITHUB_ENV) { Add-Content -Path $env:GITHUB_ENV -Value "LOBSTERVM_CLI=$cli" }
