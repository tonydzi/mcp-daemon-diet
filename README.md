# mcp-daemon-diet

Stops your AI tools from starting a separate copy of every MCP server in every session, so your computer stops running out of memory.

**Who it's for:** people who run several Claude Code, Codex or other agent sessions at once and see the laptop slow down.
**What you get:** one shared background copy of each server per machine, and scripts that measure the before and after.

## Quick start
1. Download the repo: `git clone https://github.com/tonydzi/mcp-daemon-diet` and open the folder.
2. Measure first: `python scripts/mcp_diet_measure.py`. If every server shows `copies 1`, you are done, nothing to fix.
3. If one server shows many copies, ask your AI agent to convert it (prompt below). Do not edit configs by hand.

**Done when:** `python scripts/mcp_diet_verify.py --servers <name>` exits `0` and the measure table shows `copies 1` for that server.

> Want an AI agent to do it for you? Paste this to Claude Code / Codex / Cursor:
> "Read the 'For AI agents' section of https://github.com/tonydzi/mcp-daemon-diet and set it up for me."

## For AI agents

### Preconditions
- Python 3 (the measure and verify scripts are standard library only) and permission to run shell commands. No admin rights are needed for the autostart templates.
- `pip install psutil` is needed only for `scripts/orphan_reaper.py`.
- Read access to the human's MCP client config (`~/.claude.json`, Claude Desktop config, or whatever client they use).
- Ask the human, before changing anything: does any candidate server hold a credential or a logged-in account (Telegram, WhatsApp, a database)? A daemon is reachable by every local process and stays alive after sessions close (`docs/SECURITY.md`). For sensitive ones, skip or add a bearer token.

### Install
Nothing to install. Clone the repo and follow `PROMPT.md` step by step. It is the full procedure for an agent.
```bash
git clone https://github.com/tonydzi/mcp-daemon-diet
cd mcp-daemon-diet
python scripts/mcp_diet_measure.py          # step 0: measure; stop here if all copies are 1
```
Then, for each multi-copy server: find its HTTP mode from its own `--help` or README (never invent flags), pick a fixed local port, copy the launcher template for the OS from `daemon/windows`, `daemon/macos` or `daemon/linux`, set up login-time autostart, and repoint the client config to `{"type":"sse"|"http","url":"http://127.0.0.1:PORT/PATH"}`. `docs/RECIPE.md` is the server-agnostic recipe.

### Configure
- Bind every daemon to `127.0.0.1` only, one fixed port per server.
- Secrets for a launcher come from a file outside any synced or committed folder, supplied by the human. Never inline them.
- Windows launchers must be saved with CRLF line endings, or `cmd.exe` fails silently.
- Autostart without admin: Windows HKCU `Run` key, macOS launchd agent (`RunAtLoad` and `KeepAlive`), Linux `systemd --user` with `loginctl enable-linger`.
- Windows watchdog: schedule `daemon/windows/watchdog.ps1` every 30 minutes after filling its `$Daemons` table. On macOS and Linux the OS restart policy is enough, add no script.
- Back up the client config first and tell the human where the backup is.

### Verify
```bash
python scripts/mcp_diet_verify.py --servers <names you converted>
python scripts/mcp_diet_measure.py
python scripts/_test_mcp_diet.py
```
Always pass `--servers`: without it nothing is required to be off stdio, so a config that is still all stdio also prints `OK` and exits `0`, which proves nothing. With `--servers`, a name that is still stdio, missing from the config, or whose daemon does not answer gives a `FAIL:` line and exit `1`. Expected: `mcp_diet_verify.py` exits `0` (config is off stdio and the daemon answers). The measure table shows `copies 1` for each converted server (sessions that were already open keep old copies until they exit). The measure script always exits `0`, even when it finds nothing; if it prints `No MCP server processes found`, you have not measured anything, so check the names or run it with `--all`. `_test_mcp_diet.py` ends with `ALL GREEN` and exits `0` (checked on this repo). Then restart one session and call one real tool from each converted server.

### Uninstall / rollback
- Restore the client config from the backup you made.
- Remove the autostart entry you created (HKCU Run value, launchd plist, or `systemd --user` unit) and stop the daemon.
- Remove the `watchdog.ps1` scheduled task if you added it.
- Restarting a live daemon cuts off every open session until each is restarted, so do the rollback when no important session is running.

### Rules for the agent
- Do not convert anything "for later". If the measure shows one copy, stop.
- Never simplify the watchdog to "port dead, then restart". A restart makes every live session answer `-32602 Invalid request parameters` until restarted by hand.
- `scripts/orphan_reaper.py` is a dry run by default. Run `--apply` only if the human asks, and pass `--protect-port <daemon port>` so it never kills the shared daemon. Run one reaper per machine.
- Do not kill processes by name or by parent id yourself. That is the mistake the reaper is built to avoid.
- Add the daemons to any existing orphan reaper's allow-list. A daemon has no parent session and looks like an orphan.
- This kit saves memory and processes, not tokens. Do not promise token savings.

## Details

**One shared MCP daemon per machine, instead of a copy in every agent session.**

Works with any MCP server and any MCP client. Nothing here is specific to one integration -
it is the recipe, the launcher and autostart templates for all three operating systems, a
watchdog that will not make things worse, two measurement scripts, and thirteen gotchas we
paid for in production.

Built and run at [Palo Alto AI Research Lab](https://github.com/tonydzi/tonydzi),
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

## Cleaning up the orphans agents leave behind

Per-session stdio servers are half the leak. The other half: an agent CLI times out, its wrapper
dies, and the `node -> codex` tree or a `grep -r` it spawned keeps running with nobody to read
the output. We once counted 79 codex, 253 node and 57 bun orphans on one box.

"Parent is dead, kill the child" is the obvious fix and it is wrong. PIDs get reused (on Windows
a stale PPID points at whoever got that number next), services legitimately outlive their
launcher, and git, ssh and backup jobs look exactly like orphans. Each of those mistakes has cost
us something: a crashed desktop app, our own shared MCP daemon, a git pack, live SSH sessions.

[`scripts/orphan_reaper.py`](scripts/orphan_reaper.py) kills only a tree it can prove was
abandoned: identity is PID plus start time and is re-checked at the signal, the tree walk follows
only true children, it looks twice with a grace period, zombies are reported with the parent that
is not reaping them (never "killed"), and listeners, git, ssh, rsync and backup tools are off
limits even as descendants.

```bash
pip install psutil
python scripts/orphan_reaper.py                                  # dry run
python scripts/orphan_reaper.py --apply --protect-port 8765      # protect your shared daemon
python scripts/_test_orphan_reaper.py                            # fixtures + a live orphan tree on Windows
```

Run one automatic reaper per machine. Two patrols racing each other is how it goes wrong.

Known limits, found by cross-model review before release:
- Linux subreapers (`systemd --user`, container shims, tmux) adopt orphans, so their PPID never
  becomes 1 and the Unix test does not fire. Such orphans are missed, not killed.
- A deliberate `nohup job &` started from a non-interactive shell shares the dead shell's process
  group and looks abandoned. If you run long jobs that way, list them with `--protect-cmd`.
- A tree that still holds a live git process waits until git finishes. That is on purpose.

## What this does not do

It does not save tokens. Context cost comes from tool schemas, which the client sends
whatever the transport. One daemon saves memory, processes, sockets and locks. For tokens,
disable the servers a project does not need.

## License

MIT. Templates are meant to be copied, edited and shipped without asking.

---

Part of the connector kit series: [telegram-mcp-kit](https://github.com/tonydzi/telegram-mcp-kit),
[whatsapp-mcp-kit](https://github.com/tonydzi/whatsapp-mcp-kit), **mcp-daemon-diet**,
[agent-approval-gate](https://github.com/tonydzi/agent-approval-gate) (what the agent does
when it needs a human's OK and nobody is at the terminal).
Broken step, or a gotcha we are missing? Open an issue - we answer within 24h.

---

<!--kits-series:start-->

## 🧰 Connector & Ops Kits

Eight kits, all published 2026-08-10, each lifted out of the same live fleet after it
survived production rather than written as a demo. They are independent: take one, ignore
the rest. All stdlib-only Python, all free.

| kit | what it solves |
|---|---|
| [`telegram-mcp-kit`](https://github.com/tonydzi/telegram-mcp-kit) | Connect your agent to your own Telegram account in ~15 minutes, with the production patches and every gotcha |
| [`whatsapp-mcp-kit`](https://github.com/tonydzi/whatsapp-mcp-kit) | Link WhatsApp, using a live self-refreshing QR page that makes pairing actually work |
| [`mcp-daemon-diet`](https://github.com/tonydzi/mcp-daemon-diet) | One shared MCP daemon per machine instead of a stdio copy in every session, with a watchdog that will not blind your live sessions |
| [`agent-approval-gate`](https://github.com/tonydzi/agent-approval-gate) | Your agent needs a human's OK and nobody is at the terminal: the ask goes to a messenger, the answer comes back into the run |
| [`fleet-deploy`](https://github.com/tonydzi/fleet-deploy) | Roll a fix to N machines and prove it landed on each one: canary waves and a verify that must read a fact back |
| [`secondop-panel`](https://github.com/tonydzi/secondop-panel) | Nobody reviews themselves, and one reviewer model is one blind spot: fan a change out to several model families with quorum and honest skips |
| [`oss-publish`](https://github.com/tonydzi/oss-publish) | Open up internal work without leaking it: plausible substitutions of the same shape, then a fail-closed gate over the whole tree |
| [`llm-spend-audit`](https://github.com/tonydzi/llm-spend-audit) | What your own wiring charges on every session, and which paid subscriptions are going undrawn |

<!--kits-series:end-->

<!--ecosystem-map:start-->

## 🧩 One piece of a working system

This repository is one piece lifted out of a live operation: one engineer running a fleet of
machines, built with Claude as implementation collaborator; the machines reach consensus with
each other and wake the human only for money or the irreversible. It was extracted after it
survived production, not written as a demo — and it runs on its own: nothing here phones home
to the rest.

**See how the whole thing fits together → [SYSTEM.md](https://github.com/tonydzi/tonydzi/blob/main/SYSTEM.md)**

Its closest neighbours in the **connectors** layer: [`telegram-mcp-kit`](https://github.com/tonydzi/telegram-mcp-kit) · [`whatsapp-mcp-kit`](https://github.com/tonydzi/whatsapp-mcp-kit)

<!--ecosystem-map:end-->

## AI contributors

This project is built by a human + AI team, and the git log says so: Claude writes most of
the code, Codex and Grok review it, Gemini feeds the research. Each is credited on a commit
**only if its output changed that commit's content** — no decorative credits. Lab-wide
policy, one source for every repo: [AI-CONTRIBUTORS.md](https://github.com/tonydzi/.github/blob/main/AI-CONTRIBUTORS.md).

One finding in this repo came from exactly that: an external reviewer spotted that a
marker built from an interpreter name (`"command": "node"`) made `mcp_diet_measure.py`
count every unrelated node process as a duplicate of that server. Fixed before the first
release, and the regression is locked into the test suite.

<!-- READ-WITH-AI:START (generated by read_with_ai.py - do not hand-edit) -->

### READ THIS WITH AI

One click and an agent reads the repo, pulls out the patterns and helps you apply them to your own work.

<a href="https://chatgpt.com/codex?prompt=Read%20this%20repo%3A%20https%3A%2F%2Fgithub.com%2Ftonydzi%2Fmcp-daemon-diet%20%28%E2%80%9Cmcp-daemon-diet%E2%80%9D%20-%20One%20shared%20MCP%20daemon%20per%20machine%20instead%20of%20a%20stdio%20copy%20in%20every%20agent%20session%3A%20recipe%2C%20autostart%20templates%20for%20Windows%2FmacOS%2FLinux%2C%20a%20watchdog%20that%20will%20not%20blind%20your%20live%20sessions%2C%20and%20the...%29.%20Work%20out%20what%20problem%20it%20actually%20solves%2C%20pull%20out%20the%20reusable%20patterns%20and%20help%20me%20apply%20them%20to%20my%20own%20setup.%20Start%20by%20asking%20what%20I%20am%20working%20on."><img alt="Codex - open" src="https://img.shields.io/badge/Codex-open-000000?style=for-the-badge&logo=openai&logoColor=white"></a> <a href="https://chatgpt.com/?q=Read%20this%20repo%3A%20https%3A%2F%2Fgithub.com%2Ftonydzi%2Fmcp-daemon-diet%20%28%E2%80%9Cmcp-daemon-diet%E2%80%9D%20-%20One%20shared%20MCP%20daemon%20per%20machine%20instead%20of%20a%20stdio%20copy%20in%20every%20agent%20session%3A%20recipe%2C%20autostart%20templates%20for%20Windows%2FmacOS%2FLinux%2C%20a%20watchdog%20that%20will%20not%20blind%20your%20live%20sessions%2C%20and%20the...%29.%20Work%20out%20what%20problem%20it%20actually%20solves%2C%20pull%20out%20the%20reusable%20patterns%20and%20help%20me%20apply%20them%20to%20my%20own%20setup.%20Start%20by%20asking%20what%20I%20am%20working%20on."><img alt="ChatGPT - open" src="https://img.shields.io/badge/ChatGPT-open-10a37f?style=for-the-badge&logo=openai&logoColor=white"></a> <a href="https://claude.ai/new?q=Read%20this%20repo%3A%20https%3A%2F%2Fgithub.com%2Ftonydzi%2Fmcp-daemon-diet%20%28%E2%80%9Cmcp-daemon-diet%E2%80%9D%20-%20One%20shared%20MCP%20daemon%20per%20machine%20instead%20of%20a%20stdio%20copy%20in%20every%20agent%20session%3A%20recipe%2C%20autostart%20templates%20for%20Windows%2FmacOS%2FLinux%2C%20a%20watchdog%20that%20will%20not%20blind%20your%20live%20sessions%2C%20and%20the...%29.%20Work%20out%20what%20problem%20it%20actually%20solves%2C%20pull%20out%20the%20reusable%20patterns%20and%20help%20me%20apply%20them%20to%20my%20own%20setup.%20Start%20by%20asking%20what%20I%20am%20working%20on."><img alt="Claude - open" src="https://img.shields.io/badge/Claude-open-d97757?style=for-the-badge&logo=anthropic&logoColor=white"></a>

<details>
<summary>Copy the prompt (works in any agent: Gemini, Grok, a local model, your own CLI)</summary>

```text
Read this repo: https://github.com/tonydzi/mcp-daemon-diet (“mcp-daemon-diet” - One shared MCP daemon per machine instead of a stdio copy in every agent session: recipe, autostart templates for Windows/macOS/Linux, a watchdog that will not blind your live sessions, and the...). Work out what problem it actually solves, pull out the reusable patterns and help me apply them to my own setup. Start by asking what I am working on.
```

</details>

<sub>— TonyDzi, Palo Alto AI Research Lab · second brain, agent coordination, persistent memory: github.com/tonydzi</sub>

<!-- READ-WITH-AI:END -->
