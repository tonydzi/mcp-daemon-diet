# mcp-daemon-diet

**One shared MCP daemon per machine, instead of a copy in every agent session.**

Works with any MCP server and any MCP client. Nothing here is specific to one integration -
it is the recipe, the launcher and autostart templates for all three operating systems, a
watchdog that will not make things worse, two measurement scripts, and thirteen gotchas we
paid for in production.

Built and run at [Palo Alto AI Research Lab](https://github.com/tonydzi/Palo-Alto-AI-Research-Lab),
where a fleet of Claude sessions across five machines talks to its MCP servers through
exactly this setup.

## The problem

An MCP server registered as `stdio` is spawned **per client session**. Ten parallel agent
sessions means ten copies of the same server: ten times the memory, ten connections to
whatever it talks to, ten holders of the same lock.

What we measured on one laptop, 2026-08-01 to 08-03:

| server   | copies | summed RSS |
|----------|--------|------------|
| telegram | ~9     | ~2.7 GB    |
| mongodb  | ~26    | ~3.0 GB    |
| n8n      | ~15    | ~2.9 GB    |
| whatsapp | ~15    | ~1.5 GB    |
| launcher wrappers (`npx`/`cmd`) | ~60 | ~5 GB |

**Read those numbers honestly.** Summing RSS over-counts: copies share code pages, so the
operating system is not holding that many distinct bytes and you will not get that many
back. What is exact is the copy count - and that each copy is an independent client of the
upstream service, with its own socket, its own lock and its own session.

Measure your own machine before you believe anyone's table, including this one:

```
python scripts/mcp_diet_measure.py
```

If it prints `copies 1` everywhere, you have nothing to fix. That is also what a converted
machine looks like: on our hub the same script now reports one `telegram` and one `n8n`
process serving every open session.

## The fix, in one line

Run the server once, bound to `127.0.0.1:PORT`, and point every client at the URL:

```json
"mcpServers": { "telegram": { "type": "sse", "url": "http://127.0.0.1:8765/sse" } }
```

Everything else in this repo is the part that makes that survive a reboot, a crash and a
teammate.

## What you get

| | |
|---|---|
| [`docs/RECIPE.md`](docs/RECIPE.md) | the server-agnostic procedure, start to proof |
| [`PROMPT.md`](PROMPT.md) | paste into Claude Code / Codex and it does the conversion for you |
| [`daemon/`](daemon) | launcher + autostart templates for [windows](daemon/windows), [macos](daemon/macos), [linux](daemon/linux) (HKCU Run key / launchd / `systemd --user`) - none need admin |
| [`daemon/windows/watchdog.ps1`](daemon/windows/watchdog.ps1) | the careful watchdog: two probes, false alarms logged not "healed", evidence before action |
| [`scripts/mcp_diet_measure.py`](scripts/mcp_diet_measure.py) | before/after: copies, memory, redundancy - stdlib only |
| [`scripts/mcp_diet_verify.py`](scripts/mcp_diet_verify.py) | proof: config is off stdio **and** the daemon actually answers |
| [`docs/GOTCHAS.md`](docs/GOTCHAS.md) | thirteen dated failures, each with its fix |
| [`docs/SECURITY.md`](docs/SECURITY.md) | what changes when a per-session child becomes a machine-wide service |
| [`patches/README.md`](patches/README.md) | for stdio-only servers: how to add a transport switch, and get it upstreamed |

## The one thing to know before you start

**Restarting a shared daemon blinds every live session.** They do not reconnect - every
call answers `-32602 Invalid request parameters` until each session is restarted by hand.
So a naive "port dead -> restart" watchdog causes worse outages than the crashes it fixes.
Ours probes twice, logs a false alarm instead of acting on it, records evidence before it
touches anything, and refuses to restart a daemon that is merely mute. Read its header
before you edit it. Full story: [GOTCHAS #1](docs/GOTCHAS.md).

## What this does not do

It does not save tokens. Context cost comes from tool schemas, which the client sends
whatever the transport. One daemon saves memory, processes, sockets and locks. For tokens,
disable the servers a project does not need.

## License

MIT. Templates are meant to be copied, edited and shipped without asking.

---

Part of the connector kit series: [telegram-mcp-kit](https://github.com/tonydzi/telegram-mcp-kit),
[whatsapp-mcp-kit](https://github.com/tonydzi/whatsapp-mcp-kit), **mcp-daemon-diet**.
Broken step, or a gotcha we are missing? Open an issue - we answer within 24h.
