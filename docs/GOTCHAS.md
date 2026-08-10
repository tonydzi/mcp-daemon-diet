# GOTCHAS - the hours we already paid, so you don't

Field notes from running shared MCP daemons on a multi-machine agent fleet. Dates are the
day each one bit us.

## 1. A restart blinds every live session (2026-08-06) - the expensive one

A daemon restart severs the HTTP/SSE stream of every attached client session, and those
sessions **do not reconnect**. From that moment every call to that server returns
`-32602 Invalid request parameters` - including calls with no arguments, which is why it
reads like a parameter bug and not a transport death. The session stays broken until the
human restarts it.

Evidence chain from the day we found it: watchdog log `15:14:12 telegram:8765 dead ->
restart`, server processes stamped `15:14:13`, session attached before 15:14 dead at
15:30, while the same account speaking the upstream protocol directly worked fine.

Consequences baked into this kit: two probes with a pause, false alarms logged rather than
"healed", evidence written before anything is touched, and a zombie left alone for a human
to judge. **Restarting a live daemon is more expensive than most crashes.**

## 2. A TCP probe does not prove the service is alive (2026-08-06)

`Test-NetConnection`/`connect()` proves `bind` + `listen`, nothing more. A daemon whose
accept loop is dead still holds the port, so the watchdog stays happy while every session
gets nothing. Probe HTTP too - any status, 404 included, means alive.

## 3. Never probe the `/sse` endpoint (2026-08-06)

An SSE endpoint is a stream: it never closes, so a health GET against it hangs forever and
takes the watchdog with it. Probe the origin (`http://127.0.0.1:PORT/`) instead.

## 4. An `npx` CLI can be stdio-only while the package supports HTTP (2026-08-03)

One server's `npx` binary is a hard-coded stdio wrapper that ignores the mode env var
entirely. Its HTTP entry point lives at `dist/mcp/index.js` inside the package. Cost: an
evening of "the flag does nothing". Install globally and launch the real entry point.

## 5. A `.cmd` written with LF line endings breaks `cmd.exe` (2026-08-03)

Most editors and every LLM tool write LF. `cmd.exe` then parses the batch file one broken
line at a time; the daemon never starts and the log is **empty**, which reads like "the
launcher never ran" rather than "the launcher is corrupt". Keep launchers CRLF - this repo
ships a `.gitattributes` that enforces it on checkout.

## 6. An empty daemon log means the launcher never reached the binary

Two causes, both silent: gotcha 5, or a stale launcher wrapper from a previous start still
holding the log file open. The watchdog here clears stale wrappers before restarting.

## 7. `enabledelayedexpansion` eats `!` in paths (2026-08-03)

With delayed expansion on, a path containing `!` (a folder named `!work`, say) arrives
without it, and the launcher fails on a path that looks correct in the file. Do not enable
it in launchers; there is nothing in a launcher that needs it.

## 8. An orphan reaper will kill your daemon (2026-08-01)

A shared daemon has no client parent - precisely the shape a zombie-process patrol hunts.
Ours killed the daemon on two machines before we added it to the allow-list. Add the
daemon's command-line marker to whatever reaper you run, before the first night.

## 9. Windows hides the command line of processes you don't own (2026-08-10)

`Win32_Process.CommandLine` comes back **empty** for a process at a different elevation.
A daemon can be running, listening and serving while every command-line-based tool reports
it as absent - the first version of `mcp_diet_measure.py` did exactly that to a daemon that
had been up for days. Identify daemons by the port they listen on, and report the count of
unreadable processes instead of silently dropping them. A prober that omits what it is
measuring is worse than no prober.

## 10. PowerShell does not hand you UTF-8 (2026-08-10)

`Get-CimInstance | ConvertTo-Json` piped into another process arrives in the machine's ANSI
code page, so one non-ASCII byte in one command line kills the whole measurement with a
`UnicodeDecodeError`. Set `[Console]::OutputEncoding` and decode with `errors="replace"`.

Two more faces of the same trap, both hit while building this kit:

- `Add-Content` writes ANSI unless you pass `-Encoding UTF8`, so a log written by two
  scripts ends up in two encodings at once - unreadable exactly when you need to read it.
- **Windows PowerShell 5.1 reads a `.ps1` as ANSI unless the file has a UTF-8 BOM.** A
  non-ASCII character in a log message comes out as mojibake (`|` printed as `В·`), and a
  non-ASCII character in *code* is a parse error on the first such line. The robust answer
  for a script other people will edit is to keep it pure ASCII; the alternative is saving
  it as UTF-8 **with** BOM and remembering that forever.

## 11. Upstream `git pull` silently reverts a local transport patch (2026-08-01)

If you patched the server yourself (patches/README.md), a pull restores the stdio-only
file, the daemon starts, exits immediately or serves stdio into a void, and nothing listens
on the port. Re-apply after every pull, or better: get the transport option upstreamed -
ours landed natively and the patch became unnecessary.

## 12. Old sessions keep their old copies

Converting the config does not kill the stdio children of sessions that are already
running. The copy count drops as those sessions exit. Measuring right after the switch and
concluding "it didn't work" is a false alarm.

## 13. One daemon means one lock, and stdio copies may hold it (2026-08-03)

For servers that own an exclusive resource - a Telethon session file, a WhatsApp device
pairing, a SQLite database - the daemon cannot take the lock while old per-session copies
still hold it. You will see `database is locked` or a re-pairing prompt. Close the old
sessions first, then start the daemon. Do not re-authenticate to "fix" it; you will just
add a second device to your account.
