#requires -Version 7.3
[CmdletBinding()]
param(
    [string] $AppDir = $(if ($env:TMEM_INSTALL_APP_DIR) { $env:TMEM_INSTALL_APP_DIR } else { Join-Path $env:LOCALAPPDATA 'tmem/app' }),
    [string] $BinDir = $(if ($env:TMEM_INSTALL_BIN_DIR) { $env:TMEM_INSTALL_BIN_DIR } else { Join-Path $env:LOCALAPPDATA 'tmem/bin' }),
    [string] $ConfigDir = $(if ($env:TMEM_INSTALL_CONFIG_DIR) { $env:TMEM_INSTALL_CONFIG_DIR } else { Join-Path $env:APPDATA 'tmem' })
)
$ErrorActionPreference = 'Stop'
if (!$IsWindows) { throw 'Use uninstall.sh on Linux/macOS.' }
$statePath = Join-Path $ConfigDir 'powershell-profile.json'
if (Test-Path $statePath) {
    $state = Get-Content $statePath -Raw | ConvertFrom-Json
    if ($state.owner -ne 'tmem') { throw "Refusing to remove unknown profile state: $statePath" }
    if ($state.inserted -and (Test-Path $state.profile)) {
        $text = [IO.File]::ReadAllText($state.profile)
        [IO.File]::WriteAllText($state.profile, $text.Replace($state.block, ''), [Text.UTF8Encoding]::new($false))
    }
    Remove-Item $statePath
}
foreach ($path in @((Join-Path $BinDir 'tmem-core.ps1'), (Join-Path $ConfigDir 'tmem.ps1'))) {
    if ((Test-Path $path) -and ((Get-Content $path) -contains '# tmem managed file')) { Remove-Item $path }
}
$marker = Join-Path $AppDir '.tmem-install'
if ((Test-Path $marker) -and ((Get-Content $marker -Raw).Trim() -eq 'tmem managed installation')) {
    $package = Join-Path $AppDir 'tmem'
    if (Test-Path $package) { Remove-Item $package -Recurse -Force }
    Remove-Item $marker
}
Write-Host 'Removed tmem application and profile integration. History and configuration were preserved.'
Write-Host 'Open a new PowerShell session to unload the hooks.'
