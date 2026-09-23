[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = 'Stop'
$skillTarget = Join-Path $env:USERPROFILE '.codex\skills\manage-simple-sticky-notes'
$codex = Get-Command codex -ErrorAction Stop

if ($PSCmdlet.ShouldProcess('simple_sticky_notes', 'Remove Codex MCP registration')) {
    & $codex.Source mcp remove simple_sticky_notes
}
if ((Test-Path -LiteralPath $skillTarget) -and $PSCmdlet.ShouldProcess($skillTarget, 'Remove installed skill')) {
    Remove-Item -Recurse -Force -LiteralPath $skillTarget
}

Write-Host 'Uninstalled. Backups and the cloned repository were preserved.'
