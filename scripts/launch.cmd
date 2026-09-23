@echo off
setlocal
set "REPO_ROOT=%~dp0.."
set "VENV_PYTHON=%REPO_ROOT%\.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
  >&2 echo Simple Sticky Notes MCP is not installed. Run scripts\install.ps1 first.
  exit /b 1
)

"%VENV_PYTHON%" -m simple_sticky_notes_mcp.server
