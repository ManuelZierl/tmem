#requires -Version 7.3
[CmdletBinding()]
param(
    [string] $Python = 'python',
    [string] $AppDir = $(if ($env:TMEM_INSTALL_APP_DIR) { $env:TMEM_INSTALL_APP_DIR } else { Join-Path $env:LOCALAPPDATA 'tmem/app' }),
    [string] $BinDir = $(if ($env:TMEM_INSTALL_BIN_DIR) { $env:TMEM_INSTALL_BIN_DIR } else { Join-Path $env:LOCALAPPDATA 'tmem/bin' }),
    [string] $ConfigDir = $(if ($env:TMEM_INSTALL_CONFIG_DIR) { $env:TMEM_INSTALL_CONFIG_DIR } else { Join-Path $env:APPDATA 'tmem' }),
    [string] $ProfilePath = $(if ($env:TMEM_INSTALL_PROFILE) { $env:TMEM_INSTALL_PROFILE } else { $PROFILE.CurrentUserAllHosts })
)
$ErrorActionPreference = 'Stop'
if (!$IsWindows) { throw 'Use install.sh on Linux/macOS.' }
$pythonPath = & $Python -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"; print(sys.executable)'
if ($LASTEXITCODE -ne 0 -or !$pythonPath) { throw 'tmem requires Python 3.10 or newer.' }
$AppDir = [IO.Path]::GetFullPath($AppDir)
$BinDir = [IO.Path]::GetFullPath($BinDir)
$ConfigDir = [IO.Path]::GetFullPath($ConfigDir)
$ProfilePath = [IO.Path]::GetFullPath($ProfilePath)
$marker = '# tmem managed file'
$installMarker = Join-Path $AppDir '.tmem-install'
$statePath = Join-Path $ConfigDir 'powershell-profile.json'
$integration = Join-Path $ConfigDir 'tmem.ps1'
$core = Join-Path $BinDir 'tmem-core.ps1'
$owned = (Test-Path $installMarker) -and ((Get-Content $installMarker -Raw).Trim() -eq 'tmem managed installation')
if ((Test-Path (Join-Path $AppDir 'tmem')) -and !$owned) { throw "Refusing to overwrite non-tmem path: $AppDir/tmem" }
if ((Test-Path $installMarker) -and !$owned) { throw "Refusing to overwrite non-tmem path: $installMarker" }
foreach ($path in @($core, $integration)) {
    if ((Test-Path $path) -and !((Get-Content $path) -contains $marker)) { throw "Refusing to overwrite non-tmem path: $path" }
}
if (Test-Path $statePath) {
    $oldState = Get-Content $statePath -Raw | ConvertFrom-Json
    if ($oldState.owner -ne 'tmem' -or $oldState.profile -ne $ProfilePath) { throw "Refusing to overwrite profile state: $statePath" }
}
function Quote-Literal([string] $Value) {
    foreach ($code in @(39, 0x2018, 0x2019, 0x201a, 0x201b)) {
        $quote = [string][char]$code
        $Value = $Value.Replace($quote, $quote + $quote)
    }
    "'" + $Value + "'"
}
$sourceLine = '. ' + (Quote-Literal $integration)
$block = "# >>> tmem >>>`n$sourceLine`n# <<< tmem <<<"
$profileText = if (Test-Path $ProfilePath) { [IO.File]::ReadAllText($ProfilePath) } else { '' }
if ($profileText.Contains('# >>> tmem >>>') -and !(Test-Path $statePath)) { throw 'An untracked tmem profile block already exists; refusing to change it.' }
New-Item -ItemType Directory -Force -Path $AppDir, $BinDir, $ConfigDir, (Split-Path $ProfilePath) | Out-Null
if (Test-Path (Join-Path $AppDir 'tmem')) { Remove-Item (Join-Path $AppDir 'tmem') -Recurse -Force }
Copy-Item (Join-Path $PSScriptRoot 'src/tmem') (Join-Path $AppDir 'tmem') -Recurse
New-Item -ItemType Directory -Force -Path (Join-Path $AppDir 'tmem/shell') | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'shell/tmem.*') (Join-Path $AppDir 'tmem/shell')
Set-Content $installMarker 'tmem managed installation' -Encoding utf8
$launcher = @'
# tmem managed file
$oldPath = $env:PYTHONPATH
$oldConfig = $env:TMEM_CONFIG_DIR
$oldEncoding = $env:PYTHONUTF8
try {
    $env:PYTHONPATH = __APP__
    if (!$env:TMEM_CONFIG_DIR) { $env:TMEM_CONFIG_DIR = __CONFIG__ }
    $env:PYTHONUTF8 = '1'
    $OutputEncoding = [Text.UTF8Encoding]::new($false)
    # PowerShell script wrappers receive pipeline objects in $input; they do
    # not automatically forward those objects to a child native executable.
    if ($MyInvocation.ExpectingInput) {
        $input | & __PYTHON__ -m tmem @args
    } else {
        & __PYTHON__ -m tmem @args
    }
} finally {
    $env:PYTHONPATH = $oldPath
    $env:TMEM_CONFIG_DIR = $oldConfig
    $env:PYTHONUTF8 = $oldEncoding
}
'@
$launcher = $launcher.Replace('__APP__', (Quote-Literal $AppDir)).Replace('__CONFIG__', (Quote-Literal $ConfigDir)).Replace('__PYTHON__', (Quote-Literal $pythonPath))
Set-Content $core $launcher -Encoding utf8
$prefix = "$marker`n" + 'if (!$env:TMEM_CORE) { $env:TMEM_CORE = ' + (Quote-Literal $core) + " }`n"
Set-Content $integration ($prefix + (Get-Content (Join-Path $PSScriptRoot 'shell/tmem.ps1') -Raw)) -Encoding utf8
if (!(Test-Path (Join-Path $ConfigDir 'config.json'))) {
    Set-Content (Join-Path $ConfigDir 'config.json') '{"history_limit":50000}' -Encoding utf8
}
$inserted = $false
if (!(($profileText -split "`r?`n") -contains $sourceLine)) {
    [IO.File]::AppendAllText($ProfilePath, "`n$block`n", [Text.UTF8Encoding]::new($false))
    $inserted = $true
} elseif (Test-Path $statePath) { $inserted = [bool]$oldState.inserted }
@{ owner='tmem'; profile=$ProfilePath; block=$block; inserted=$inserted } | ConvertTo-Json | Set-Content $statePath -Encoding utf8
Write-Host "Installed tmem. Load it in this PowerShell session with:`n$sourceLine"
if (!(Get-Command fzf -ErrorAction Ignore)) { Write-Host 'Interactive UI dependency: winget install junegunn.fzf' }
Write-Host 'No administrator access or execution-policy changes were requested.'
