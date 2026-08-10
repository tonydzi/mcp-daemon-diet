# watchdog.ps1 -- keeps the shared MCP daemons alive WITHOUT blinding live sessions.
#
# READ THIS BEFORE "IMPROVING" IT.
# Restarting a daemon is not a cheap, safe action. A restart kills the HTTP/SSE stream of
# every client session currently attached, and those sessions do not reconnect: from then
# on every call to that server answers `-32602 Invalid request parameters` -- including
# calls with no arguments at all -- until the human restarts the session. Measured
# 2026-08-06: two watchdog restarts (03:14, 15:14) left a working session blind by 15:30.
# So the cost is asymmetric. Reviving a dead daemon costs seconds. Restarting a live one
# costs every open session on the machine. A watchdog that restarts on one failed probe
# is a worse outage than the crash it thinks it is fixing.
#
# THEREFORE
#   probe 1 alive            -> silence, exit 0                (silence == healthy)
#   probe 1 dead, probe 2 ok -> LOG "false alarm", change nothing. This line is the
#                               measurement that tells you whether your probe flaps.
#   both probes dead         -> log EVIDENCE first (port listener, server PIDs), then
#                               clean stale wrappers and start the launcher.
#   port open, HTTP mute     -> log "zombie", do NOT restart. A TCP connect only proves
#                               bind+listen; the socket can be held by a process serving
#                               nothing. Healing it would cost live sessions on a guess,
#                               so a human decides -- but the evidence is on record.
#
# The watchdog must NOT live inside the thing it watches: schedule it in Task Scheduler,
# not in the agent, or it dies with its patient and reports nothing.
#
# USAGE
#   powershell -File watchdog.ps1 -DryRun          # verdict only, touches nothing
#   powershell -File watchdog.ps1                  # the scheduled run
# Schedule: every 30 min, MultipleInstances = IgnoreNew (two watchdogs racing each other
# fight over the same log file and neither restart completes).

param(
    [int]$GapSeconds = 5,
    [switch]$DryRun
)

$ErrorActionPreference = 'SilentlyContinue'
$Log = Join-Path $env:USERPROFILE 'mcp_daemons_watchdog.log'

# ---- YOUR DAEMONS: one row per shared daemon. Port -> the launcher that starts it. ----
# Match = the ports in your client config. Keep the launchers next to this file.
$Daemons = @(
    @{ Name = 'telegram'; Port = 8765; Launcher = Join-Path $PSScriptRoot 'launcher-telegram.cmd' }
    # @{ Name = 'mongodb';  Port = 8766; Launcher = Join-Path $PSScriptRoot 'launcher-mongodb.cmd' }
    # @{ Name = 'n8n';      Port = 8767; Launcher = Join-Path $PSScriptRoot 'launcher-n8n.cmd' }
    # @{ Name = 'whatsapp'; Port = 8768; Launcher = Join-Path $PSScriptRoot 'launcher-whatsapp.cmd' }
)

function Write-Line([string]$text) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'), $text
    # -Encoding UTF8 is not optional: Add-Content defaults to the ANSI code page and the
    # log ends up in two encodings at once, unreadable exactly when you need to read it.
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Output $line
}

function Test-PortOpen([int]$Port) {
    return [bool](Test-NetConnection -ComputerName 127.0.0.1 -Port $Port `
                  -InformationLevel Quiet -WarningAction SilentlyContinue)
}

function Test-Answering([int]$Port) {
    # Probe the ORIGIN, never the /sse path: an SSE endpoint is an open stream, so a GET
    # against it never returns and the watchdog hangs forever. Verified the hard way.
    # Any HTTP status means alive -- 404 from the root is a perfectly healthy MCP daemon.
    try {
        $null = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/" -f $Port) -TimeoutSec 5 `
                -UseBasicParsing -MaximumRedirection 0 -ErrorAction Stop
        return $true
    } catch {
        if ($_.Exception.Response) { return $true }
        return $false
    }
}

function Get-Evidence([int]$Port) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    $ownerPid = if ($listener) { ($listener | Select-Object -First 1).OwningProcess } else { 'none' }
    return "portOwner=$ownerPid"
}

function Remove-StaleWrappers([string]$Launcher) {
    # A launcher wrapper whose child already died still holds the log file open, and the
    # next start fails silently with an empty log. Clear those before restarting.
    $leaf = Split-Path $Launcher -Leaf
    Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" |
        Where-Object { $_.CommandLine -like "*$leaf*" } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Line ("cleaned stale wrapper pid {0} of {1}" -f $_.ProcessId, $leaf)
        }
}

$exitCode = 0
foreach ($d in $Daemons) {
    if (Test-PortOpen $d.Port) {
        if (-not (Test-Answering $d.Port)) {
            Write-Line ("{0}:{1} ZOMBIE? port is listening but the service did not answer. NOT restarting (healing a live daemon costs every open session); if this line repeats, restart it by hand when nobody is working." -f $d.Name, $d.Port)
        }
        continue                                  # healthy -> stay silent
    }

    Start-Sleep -Seconds $GapSeconds

    if (Test-PortOpen $d.Port) {
        Write-Line ("{0}:{1} FALSE ALARM -- probe 1 dead, probe 2 alive after {2}s. Not touching it." -f $d.Name, $d.Port, $GapSeconds)
        continue
    }

    Write-Line ("{0}:{1} DEAD on both probes | {2} -> restarting" -f $d.Name, $d.Port, (Get-Evidence $d.Port))
    if ($DryRun) { Write-Line ("{0}: dry run, no restart performed" -f $d.Name); continue }

    if (-not (Test-Path $d.Launcher)) {
        Write-Line ("{0}: launcher NOT FOUND at {1} -- fix the path in this script" -f $d.Name, $d.Launcher)
        $exitCode = 1
        continue
    }
    Remove-StaleWrappers $d.Launcher
    Start-Process -FilePath $d.Launcher -WindowStyle Hidden
    Start-Sleep -Seconds 10
    if (Test-PortOpen $d.Port) {
        Write-Line ("{0}:{1} back up" -f $d.Name, $d.Port)
    } else {
        Write-Line ("{0}:{1} RESTART FAILED -- port still silent 10s after launch. Check the daemon log; an empty log means the launcher never reached the binary (stale wrapper holding it, or the .cmd saved with LF line endings)." -f $d.Name, $d.Port)
        $exitCode = 1
    }
}
exit $exitCode
