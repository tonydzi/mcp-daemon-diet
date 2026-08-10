# daemon/ - launchers, autostart, watchdog

One file per OS. Each is a template: copy it per server, edit the marked lines.

| file | what it is |
|---|---|
| [`windows/launcher-telegram.cmd`](windows/launcher-telegram.cmd) | launcher template + the HKCU Run key command for autostart without admin |
| [`windows/watchdog.ps1`](windows/watchdog.ps1) | multi-daemon watchdog, table-driven (`$Daemons` at the top) |
| [`macos/com.example.mcp-telegram.plist`](macos/com.example.mcp-telegram.plist) | launchd agent, `RunAtLoad` + `KeepAlive` |
| [`linux/mcp-telegram.service`](linux/mcp-telegram.service) | `systemd --user` unit, `Restart=on-failure` |

They are named `telegram` only because that is the server we converted first; nothing in
them is Telegram-specific beyond the three edited lines.

## Do you need the watchdog?

**macOS and Linux: no.** `KeepAlive` and `Restart=on-failure` already do the job, and
adding a script on top gives you two things restarting the same daemon.

**Windows: yes** - Task Scheduler has no equivalent for a Run-key process. Schedule
`watchdog.ps1` every 30 minutes with `MultipleInstances = IgnoreNew` (two copies racing
fight over the log file and neither restart completes). Check it first with:

```powershell
powershell -File watchdog.ps1 -DryRun
```

Dry run prints the verdict and changes nothing. Healthy daemons produce no output at all -
silence is the success signal, and the log only ever contains things worth reading.

## Why the watchdog looks over-careful

Because restarting a shared daemon severs the HTTP/SSE stream of every live client session,
and those sessions do not reconnect: every subsequent call answers `-32602 Invalid request
parameters` until a human restarts the session. Reviving a dead daemon costs seconds;
restarting a live one costs every open session on the machine.

So: two probes with a pause, a false alarm logged rather than acted on, evidence (port
owner, PIDs) written before anything is touched, and a daemon that holds the port but
answers nothing left alone for a human to judge. Read the header before editing.

## Rules that apply to all three

- Bind `127.0.0.1`. Never `0.0.0.0` - see [../docs/SECURITY.md](../docs/SECURITY.md).
- Keep secrets out of the launcher if it is committed or synced; read them from a file
  outside the tree.
- Windows launchers must be CRLF. With LF, `cmd.exe` breaks on every line and the daemon
  log stays empty. The repo's `.gitattributes` enforces this on checkout.
- Add the daemons to your orphan-process reaper's allow-list if you run one.
