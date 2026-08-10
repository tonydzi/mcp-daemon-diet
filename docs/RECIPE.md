# The recipe - any stdio MCP server -> one shared daemon

Server-agnostic. The only thing that differs per server is step 2.

## 0. Measure first

```
python scripts/mcp_diet_measure.py
```

If every server shows `copies 1`, you have nothing to fix - close the tab. The diet is
worth doing when you run several agent sessions at once and the copy count tracks them.

## 1. Pick a port per server and write the table down

One port per server, fixed forever, the same on every machine you own. Ours:

| port | server   |
|------|----------|
| 8765 | telegram |
| 8766 | mongodb  |
| 8767 | n8n      |
| 8768 | whatsapp |

Boring, but the port number ends up in the client config, the launcher, the watchdog and
the firewall rule; changing it later means changing four things that fail separately.

## 2. Make the server speak HTTP instead of stdio

Three cases, in order of luck:

**It already supports HTTP.** Most maintained servers do. Read `--help`. Examples we run:

```
npx -y mongodb-mcp-server --transport http --httpPort 8766 --httpHost 127.0.0.1 --connectionString <yours>
node <global>/node_modules/@scope/whatsapp-mcp/dist/index.js --http --port 8768 --host 127.0.0.1
```

**It supports HTTP but not through the binary you are calling.** The trap that cost us an
evening: an `npx`-installed CLI wrapper can be stdio-only while the package's HTTP entry
point sits deeper in the tree. Install globally and launch the real entry point:

```
npm i -g <server-pkg>
node "<global node_modules>/<server-pkg>/dist/mcp/index.js"     # env: MCP_MODE=http PORT=8767
```

**It is stdio-only.** Patch it - see [../patches/README.md](../patches/README.md). It is
usually a handful of lines, because the underlying framework (FastMCP and friends) already
has the transport; the server just never exposed the choice.

## 3. Start it at login, without admin rights

- **Windows**: an `HKCU\...\CurrentVersion\Run` key. `schtasks /Create /SC ONLOGON` needs
  elevation and returns `Access denied` on a normal account; the Run key does not.
  Template: [`../daemon/windows/launcher-telegram.cmd`](../daemon/windows/launcher-telegram.cmd)
- **macOS**: a launchd agent in `~/Library/LaunchAgents`, `RunAtLoad` + `KeepAlive`.
  Template: [`../daemon/macos/com.example.mcp-telegram.plist`](../daemon/macos/com.example.mcp-telegram.plist)
- **Linux**: `systemd --user` + `loginctl enable-linger`.
  Template: [`../daemon/linux/mcp-telegram.service`](../daemon/linux/mcp-telegram.service)

## 4. Point the clients at the daemon

Replace the `command`/`args` stdio block with a URL. Claude Code / Claude Desktop:

```json
"mcpServers": {
  "telegram": { "type": "sse",  "url": "http://127.0.0.1:8765/sse" },
  "n8n":      { "type": "http", "url": "http://127.0.0.1:8767/mcp",
                "headers": { "Authorization": "Bearer <token>" } }
}
```

`sse` or `http` must match what the server actually serves, and so must the path
(`/sse` vs `/mcp`) - the server's docs are the authority, not this table. SSE is
deprecated in the MCP spec in favour of Streamable HTTP; prefer `http` where the server
offers both.

Back up the config file first. Restart your client afterwards: existing sessions keep
their old stdio children until they exit.

## 5. Guard it - carefully

On macOS and Linux, `KeepAlive` / `Restart=on-failure` is the whole watchdog; do not write
another. On Windows, schedule
[`../daemon/windows/watchdog.ps1`](../daemon/windows/watchdog.ps1) every 30 minutes with
`MultipleInstances = IgnoreNew`, and read its header before editing: restarting a daemon
blinds every live session, so it probes twice, logs false alarms instead of "fixing" them,
and records evidence before it touches anything.

If you run an orphan-process reaper, add the daemon to its allow-list. A daemon has no
client parent by design - that is exactly the shape a reaper is built to kill.

## 6. Prove it

```
python scripts/mcp_diet_verify.py --servers telegram,n8n
python scripts/mcp_diet_measure.py
```

Green verify plus a copy count of 1 per server is the finish line. Then open a session and
call one real tool - the config can be perfect while the daemon serves nothing.

## What this does not buy you

**Tokens.** The context cost of an MCP server is its tool schemas, and those are sent by
the client regardless of transport. One daemon saves memory, processes, sockets and
upstream locks - not a single token. To cut tokens, disable servers you do not need in a
given project (`disabledMcpServers` in Claude Code) or use a client that defers schemas.
