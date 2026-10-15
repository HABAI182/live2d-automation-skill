param([switch]$RegisterMcp, [switch]$SkipRuntime)
$ErrorActionPreference = 'Stop'
$Repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$RuntimeDir = Join-Path $Repo '.venv'
$RuntimePython = Join-Path $RuntimeDir 'Scripts\python.exe'
$PythonCommand = Get-Command python -ErrorAction Stop
if (-not (Test-Path -LiteralPath $RuntimePython)) {
    & $PythonCommand.Source -m venv $RuntimeDir
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python runtime' }
}
$Wheel = Get-ChildItem -LiteralPath (Join-Path $Repo 'dist') -Filter '*.whl' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($null -ne $Wheel) {
    & $RuntimePython -m pip install $Wheel.FullName
} else {
    & $RuntimePython -m pip install $Repo
}
if ($LASTEXITCODE -ne 0) { throw 'Package installation failed' }
if ($null -ne $Wheel) {
    & $RuntimePython -m pip install --force-reinstall --no-deps $Wheel.FullName
    if ($LASTEXITCODE -ne 0) { throw 'Wheel refresh failed' }
}
$NodeModules = Join-Path $Repo 'runtime\node_modules'
if (-not $SkipRuntime) {
    $NpmCommand = Get-Command npm -ErrorAction Stop
    & $NpmCommand.Source ci --prefix (Join-Path $Repo 'runtime') --ignore-scripts --no-fund --no-audit
    if ($LASTEXITCODE -ne 0) { throw 'Optional SDK verifier runtime installation failed; use -SkipRuntime for API/file tools only' }
}
$CodexRoot = $env:CODEX_HOME
if ([string]::IsNullOrWhiteSpace($CodexRoot)) { $CodexRoot = Join-Path $env:USERPROFILE '.codex' }
$SkillsRoot = [IO.Path]::GetFullPath((Join-Path $CodexRoot 'skills'))
$Destination = [IO.Path]::GetFullPath((Join-Path $SkillsRoot 'live2d-automation'))
if ([IO.Path]::GetDirectoryName($Destination) -ne $SkillsRoot) { throw 'Unexpected skill destination' }
$BackupRoot = Join-Path $env:LOCALAPPDATA 'live2d-automation\install-backups'
New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
if (Test-Path -LiteralPath $Destination) {
    $BackupSkill = Join-Path $BackupRoot ('skill-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    Copy-Item -LiteralPath $Destination -Destination $BackupSkill -Recurse
}
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$Source = Join-Path $Repo 'skills\live2d-automation'
Get-ChildItem -LiteralPath $Source | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force }
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false
$RuntimeJson = @{python=$RuntimePython; package=$Repo; node_modules=$NodeModules} | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $Destination 'runtime.json'), $RuntimeJson, $Utf8NoBom)
$StateDir = Join-Path $env:LOCALAPPDATA 'live2d-automation'
$OutputDir = Join-Path $Repo 'generated'
$ConfigExample = @"
[mcp_servers.live2d-automation]
command = '$RuntimePython'
args = ['-m', 'live2d_automation.server']
startup_timeout_sec = 20
tool_timeout_sec = 120

[mcp_servers.live2d-automation.env]
LIVE2D_STATE_DIR = '$StateDir'
LIVE2D_OUTPUT_DIR = '$OutputDir'
LIVE2D_NODE_MODULES = '$NodeModules'
"@
[IO.File]::WriteAllText((Join-Path $Repo 'codex-mcp.toml'), $ConfigExample, $Utf8NoBom)
if ($RegisterMcp) {
    $CodexCommand = Get-Command codex -ErrorAction Stop
    $CodexConfig = Join-Path $CodexRoot 'config.toml'
    if (Test-Path -LiteralPath $CodexConfig) {
        Copy-Item -LiteralPath $CodexConfig -Destination (Join-Path $BackupRoot ('config-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.toml'))
    }
    & $CodexCommand.Source mcp add live2d-automation --env "LIVE2D_STATE_DIR=$StateDir" --env "LIVE2D_OUTPUT_DIR=$OutputDir" --env "LIVE2D_NODE_MODULES=$NodeModules" -- $RuntimePython -m live2d_automation.server
    if ($LASTEXITCODE -ne 0) { throw 'MCP registration failed; backup preserved' }
}
@{python=$RuntimePython; skill=$Destination; mcp_registered=[bool]$RegisterMcp} | ConvertTo-Json
