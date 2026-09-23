[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$venvPath = Join-Path $repoRoot '.venv'
$pythonPath = Join-Path $venvPath 'Scripts\python.exe'
$serverPath = Join-Path $repoRoot 'src\simple_sticky_notes_mcp\server.py'
$skillSource = Join-Path $repoRoot 'skills\manage-simple-sticky-notes'
$skillTarget = Join-Path $env:USERPROFILE '.codex\skills\manage-simple-sticky-notes'

if ($env:OS -ne 'Windows_NT') {
    throw 'This connector supports Windows only.'
}

$launcher = Get-Command py -ErrorAction SilentlyContinue
if (-not $launcher) {
    $launcher = Get-Command python -ErrorAction SilentlyContinue
}
if (-not $launcher) {
    throw 'Python 3.11 or later is required.'
}

if ($launcher.Name -eq 'py.exe') {
    & $launcher.Source -3 -m venv $venvPath
} else {
    & $launcher.Source -m venv $venvPath
}

& $pythonPath -m pip install --upgrade pip
& $pythonPath -m pip install -e $repoRoot

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $skillTarget) | Out-Null
if (Test-Path -LiteralPath $skillTarget) {
    Remove-Item -Recurse -Force -LiteralPath $skillTarget
}
Copy-Item -Recurse -Force -LiteralPath $skillSource -Destination $skillTarget

$codex = Get-Command codex -ErrorAction Stop
& $codex.Source mcp remove simple_sticky_notes 2>$null
& $codex.Source mcp add simple_sticky_notes -- $pythonPath $serverPath

Write-Host 'Installed. Start a new Codex task before using the connector.'
