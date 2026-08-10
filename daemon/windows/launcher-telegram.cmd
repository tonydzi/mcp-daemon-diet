@echo off
REM Template launcher: ONE shared MCP daemon for the whole machine.
REM Copy per server, edit the marked lines, keep it next to watchdog.ps1.
REM
REM THIS FILE MUST HAVE CRLF LINE ENDINGS. Saved with LF, cmd.exe parses it one broken
REM line at a time and the daemon never starts -- with no useful error anywhere. The
REM repo's .gitattributes enforces CRLF on checkout; if you copy this content by hand,
REM check the line endings yourself.
REM
REM Autostart at login WITHOUT admin rights (schtasks /Create /SC ONLOGON needs elevation,
REM an HKCU Run key does not):
REM   reg add HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v McpDaemonTelegram /d "\"%~f0\""
REM Remove it with:  reg delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v McpDaemonTelegram /f
REM
REM SECRETS DO NOT BELONG IN THIS FILE if it lives in a synced or committed folder.
REM Read them from a file outside the repo (pattern below) or from the user environment.

setlocal

REM --- edit: where your secrets live (KEY=VALUE per line). Delete if you use env vars. ---
set "SECRETS=%USERPROFILE%\.mcp-secrets\telegram.env"
if not exist "%SECRETS%" (
  echo [telegram-daemon] no secrets file: %SECRETS% >> "%USERPROFILE%\mcp_telegram_daemon.log"
  exit /b 2
)
for /f "usebackq tokens=1,* delims==" %%A in ("%SECRETS%") do set "%%A=%%B"

REM --- edit: transport + bind. 127.0.0.1 ONLY -- this daemon is your logged-in account. ---
set MCP_TRANSPORT=sse
set MCP_HOST=127.0.0.1
set MCP_PORT=8765
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

REM --- edit: the server's own start command, unchanged except for the env above. ---
"C:\mcp\telegram-mcp\.venv\Scripts\python.exe" "C:\mcp\telegram-mcp\main.py" "C:\mcp\media" >> "%USERPROFILE%\mcp_telegram_daemon.log" 2>&1

endlocal
