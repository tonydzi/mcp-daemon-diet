# PROMPT.md - give this to your Claude Code / Codex

Copy everything below the line into an agent session that is allowed to run shell commands.
It will measure your machine, convert the servers worth converting, and prove the result.

---

You are putting this machine's MCP servers on a diet: instead of one `stdio` copy per agent
session, each server becomes ONE shared local daemon that every session connects to. The
kit is https://github.com/tonydzi/mcp-daemon-diet - clone it or fetch the scripts you need.
Follow the steps in order. The warnings are field-tested; do not optimise them away.

**Step 0 - measure, and stop if there is nothing to fix.**
Run `python scripts/mcp_diet_measure.py`. Show me the table. If every server has `copies:
1`, tell me the diet is not needed here and stop - do not convert anything "for later".
Note the `unreadable command line` count if it appears: on Windows, processes owned at a
different elevation hide their command line and are only visible by port.

**Step 1 - inventory.** Read my MCP client config (`~/.claude.json`, or the Claude Desktop
config, or whatever client I use). List every server, its type, and - for stdio ones - the
exact command and args. Tell me which ones the measurement shows as multi-copy. Those are
the candidates, ranked by copy count.

**Step 2 - ask me one question before touching anything.** For each candidate: does it hold
a credential or a logged-in account? A daemon is reachable by any process on this machine
and stays alive after my sessions close (see the kit's `docs/SECURITY.md`). If I say a
server is sensitive, either skip it or plan to put a bearer token on it. Wait for my answer.

**Step 3 - find each server's HTTP mode.** Do not assume it lacks one. Run its `--help`,
read its README, grep the installed package for `transport|sse|streamable|http`. Three
outcomes: (a) native flags - use them; (b) the CLI binary is a stdio-only wrapper while the
package has an HTTP entry point deeper in the tree (`dist/mcp/index.js` and similar) -
install globally and launch that file directly; (c) genuinely stdio-only - show me the
patch you propose per the kit's `patches/README.md`, defaulting to `127.0.0.1` and leaving
stdio as the default when the env var is unset. Do not invent flags: if you cannot find
evidence for a transport option, say so plainly instead of guessing.

**Step 4 - assign ports and write launchers.** One fixed port per server (we use 8765
telegram, 8766 mongodb, 8767 n8n, 8768 whatsapp; any scheme is fine as long as it is
written down). Copy the launcher template for this OS from `daemon/` and edit the marked
lines. Bind `127.0.0.1` only. If the launcher needs secrets, read them from a file outside
any synced or committed folder - never inline them. On Windows the launcher MUST be saved
with CRLF line endings; with LF, `cmd.exe` fails on every line and the log stays empty.

**Step 5 - autostart at login, no admin.** Windows: an HKCU `Run` key (`schtasks /SC
ONLOGON` needs elevation and will fail). macOS: a launchd agent with `RunAtLoad` +
`KeepAlive`. Linux: `systemd --user` plus `loginctl enable-linger`. Use the kit templates.

**Step 6 - repoint the clients.** Back up the config file first and tell me where the
backup is. Replace each converted server's `command`/`args` block with
`{"type":"sse"|"http","url":"http://127.0.0.1:PORT/PATH"}` - type and path must match what
that server actually serves. Add an `Authorization` header if the server has a token.

**Step 7 - a watchdog, only where the OS does not already do it.** On macOS/Linux,
`KeepAlive`/`Restart=on-failure` is enough; do not add a script. On Windows, schedule
`daemon/windows/watchdog.ps1` every 30 minutes with `MultipleInstances=IgnoreNew`, after
filling in its `$Daemons` table. CRITICAL: a restart kills the HTTP/SSE stream of every
live session, and those sessions never recover - they answer `-32602 Invalid request
parameters` until restarted by hand. Never simplify that watchdog to "port dead -> restart".
Also: if this machine runs an orphan-process reaper, add the daemons to its allow-list -
a daemon has no parent session and looks exactly like the orphans it hunts.

**Step 8 - prove it, then hand me the evidence.** Run `python scripts/mcp_diet_verify.py
--servers <the ones you converted>` (must exit 0), then `mcp_diet_measure.py` again, then
restart one session and call one real tool from each converted server. Report: the before
and after tables, the verify output, and the tool call result. Do not tell me it works
because the config looks right - a perfect config in front of a dead daemon is the exact
failure this kit exists to catch. If the copy count has not dropped yet, that is expected:
sessions started earlier keep their old children until they exit.

**Step 9 - tell me how to undo it.** The config backup path, the autostart entries you
created and the command to remove each one.
