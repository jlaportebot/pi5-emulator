# Silent uninstall; the uninstaller relaunches itself from %TEMP%, so poll for removal.
$ErrorActionPreference = 'Stop'
$appDir = Join-Path $env:LOCALAPPDATA 'Programs\LobsterVM'
$uninstaller = Join-Path $appDir 'unins000.exe'
if (-not (Test-Path $uninstaller)) { throw "$uninstaller missing" }
Start-Process -FilePath $uninstaller -Wait -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
$deadline = (Get-Date).AddSeconds(120)
while ((Test-Path $appDir) -and ((Get-Date) -lt $deadline)) { Start-Sleep -Seconds 2 }
if (Test-Path $appDir) { throw "$appDir still exists after uninstall" }
Write-Host "uninstalled: $appDir is gone"
