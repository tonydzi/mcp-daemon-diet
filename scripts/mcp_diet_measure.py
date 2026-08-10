# -*- coding: utf-8 -*-
"""mcp_diet_measure.py -- how many duplicate MCP server copies is this machine running?

    python mcp_diet_measure.py            # table for humans
    python mcp_diet_measure.py --json     # same numbers, machine-readable
    python mcp_diet_measure.py --all      # also list every matched process

This is the BEFORE picture of the diet: MCP servers registered as `stdio` are spawned
once PER CLIENT SESSION. Ten parallel agent sessions = ten copies of the same server,
each with its own memory, its own connection and its own lock on whatever it talks to.

HOW IT MEASURES
  Every live process whose command line mentions "mcp" is grouped by a server key
  (npm package, script directory, or executable name). For each group: how many copies,
  the sum of their resident memory, the largest single copy, and `redundant` = sum minus
  largest -- an UPPER BOUND on what one shared daemon would give back.

HONEST CAVEAT (read before quoting any number)
  Summing RSS over-counts. Copies of the same interpreter share code pages, so the
  operating system is not actually holding `sum` bytes of distinct memory, and freeing
  N-1 copies does not hand back exactly `redundant` bytes. What IS exact: the copy count,
  and the fact that each copy is an independent client of the upstream service. Treat the
  byte columns as an order of magnitude, and the count column as the finding.

WHAT IT CANNOT SEE (stated, not hidden)
  A server is found by its command line, by a marker from your client config, or by the
  port it listens on. A daemon that matches none of the three -- no "mcp" anywhere in its
  command line, not in a config this script reads, and no `lsof` available to name its
  port -- is invisible here. Every such hole prints a warning to stderr rather than
  quietly lowering the count. Use `--all` to see exactly what was matched.

STDLIB ONLY, read-only, no network. Windows via CIM, macOS/Linux via `ps`.
Exit 0 = measured (even if nothing found), 4 = the script itself crashed.
"""
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

# Wrapper processes are not servers: they are the launcher shim that spawned one
# (`npx`, `uv run`, `cmd /c ...`). They are counted separately because they are real
# memory too -- we once found ~60 of them at ~87 MB each -- but killing a wrapper is
# not the same finding as running one server twice.
WRAPPER_NAMES = {"cmd.exe", "npx", "npx.cmd", "npm", "npm.cmd", "uv", "uv.exe",
                 "uvx", "uvx.exe", "sh", "bash", "conhost.exe"}
SKIP_SELF = re.compile(r"mcp_diet_(?:measure|verify)", re.I)
# MCP CLIENTS also say "mcp" all over their command line -- they pass tool allow-lists
# (`mcp__server__tool`) and config flags. Counting them as servers turned a first run of
# this script into "17 copies of mcp__ccd_session_mgmt__search_session_transcripts".
# Match only shapes no server command line has, so a daemon living under a path that
# happens to contain "claude" is not mistaken for the client.
CLIENT_RE = re.compile(r"--strict-mcp-config|--mcp-config\b|mcp__|mcpPersistent"
                       r"|claude-code[\\/]|@anthropic-ai[\\/]claude-code"
                       r"|--permission-prompt-tool", re.I)
# `@scope/pkg-mcp`, `pkg-mcp-server`, `mcp-server-pkg` ... the token that names the server.
PKG_RE = re.compile(r"(?:@[\w.-]+/)?[\w.-]*mcp[\w.-]*", re.I)
NOISE_TOKENS = {"mcp", "mcp.json", "mcpservers", ".mcp", "mcp.py", "mcp.js",
                "mcp-config", "strict-mcp-config", "mcp_diet_measure"}
MAX_TOKEN = 40          # anything longer is a tool-name list or a JSON blob, not a package


def _win_processes():
    """[(pid, name, rss_bytes, cmdline)] on Windows, via one CIM query.

    The two encoding clauses are load-bearing. Command lines routinely contain paths in
    the machine's ANSI code page, and Windows PowerShell pipes them out in that code page,
    not UTF-8: without `[Console]::OutputEncoding` the JSON arrives with bytes Python
    cannot decode, and without errors="replace" one such byte kills the whole measurement.
    """
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "Get-CimInstance Win32_Process | "
          "Select-Object ProcessId,Name,WorkingSetSize,CommandLine | "
          "ConvertTo-Json -Compress -Depth 2")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                         capture_output=True, text=True, timeout=180,
                         encoding="utf-8", errors="replace")
    if out.returncode != 0 or not (out.stdout or "").strip():
        raise RuntimeError("CIM query failed: %s" % (out.stderr or "")[:200])
    rows = json.loads(out.stdout)
    if isinstance(rows, dict):          # a single process comes back unwrapped
        rows = [rows]
    return [(r.get("ProcessId") or 0, r.get("Name") or "", r.get("WorkingSetSize") or 0,
             r.get("CommandLine") or "") for r in rows]


def _posix_processes():
    """[(pid, name, rss_bytes, cmdline)] on macOS/Linux. `ps` reports RSS in KiB."""
    out = subprocess.run(["ps", "-axo", "pid=,rss=,comm=,args="],
                         capture_output=True, text=True, timeout=60)
    procs = []
    for line in out.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid, rss, comm, args = parts
        if not pid.isdigit() or not rss.isdigit():
            continue
        procs.append((int(pid), os.path.basename(comm), int(rss) * 1024, args))
    return procs


def list_processes():
    return _win_processes() if os.name == "nt" else _posix_processes()


def config_markers(paths=None):
    """{marker_substring: server_name} taken from the client's own MCP registrations.

    Anchoring on the config is what makes the output say `telegram` instead of guessing
    at a package name, and it is the only way to see a server whose command line does not
    contain the string "mcp" at all (plenty do not).
    """
    home = os.path.expanduser("~")
    candidates = paths or [
        os.path.join(home, ".claude.json"),
        os.path.join(home, "AppData", "Roaming", "Claude", "claude_desktop_config.json"),
        os.path.join(home, "Library", "Application Support", "Claude",
                     "claude_desktop_config.json"),
        os.path.join(home, ".config", "Claude", "claude_desktop_config.json"),
    ]
    markers, unreadable = {}, []
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            cfg = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Not fatal -- the heuristic still works -- but say so. A config we could not
            # read is a hole in the measurement, and a hole nobody mentions reads as zero.
            unreadable.append("%s (%s)" % (path, type(exc).__name__))
            continue
        for server, entry in (cfg.get("mcpServers") or {}).items():
            if not isinstance(entry, dict) or (entry.get("type") or "stdio") != "stdio":
                continue                # http/sse entries have no process of their own here
            for arg in list(entry.get("args") or []) + [entry.get("command") or ""]:
                marker = specific_marker(str(arg))
                if marker:
                    markers.setdefault(marker, server)
    if unreadable:
        print("warning: could not read %s -- servers registered there are matched by "
              "heuristic only" % "; ".join(unreadable), file=sys.stderr)
    return markers


def specific_marker(arg):
    """A config argument reduced to a marker, or None if it identifies nothing.

    The filter matters more than it looks. An ordinary registration is
    `{"command": "node", "args": ["/opt/weather/server.js"]}`, and taking `command`
    literally makes "node" a marker for that server -- after which EVERY node process on
    the machine is counted as another copy of it, and the tool cheerfully reports
    duplicates that do not exist. An interpreter name identifies nothing; only the script
    or package does. (Found by an external review panel, 2026-08-10.)
    """
    generic = {"node", "node.exe", "python", "python.exe", "python3", "py", "pythonw.exe",
               "npx", "npm", "uv", "uvx", "bun", "deno", "ruby", "perl", "java", "dotnet",
               "sh", "bash", "zsh", "cmd", "cmd.exe", "powershell", "pwsh", "docker",
               "run", "start", "index.js", "main.py", "server.js", "app.py", "cli.js"}
    if arg.startswith("-"):
        return None
    parts = [p for p in arg.replace("\\", "/").rstrip("/").split("/") if p]
    if not parts:
        return None
    tail = parts[-1].strip().lower()
    # `/opt/weather/server.js`: the file name says nothing, the directory names the server.
    if tail in generic and len(parts) > 1:
        tail = parts[-2].strip().lower()
    if not tail or len(tail) < 5 or len(tail) > MAX_TOKEN or tail in generic:
        return None
    # Must look like a package, script or install directory, not a bare common word.
    if not any(ch in tail for ch in ".-@_") and len(parts) < 2:
        return None
    return tail


def config_ports(paths=None):
    """{port: server_name} for servers already registered as a shared http/sse daemon."""
    home = os.path.expanduser("~")
    candidates = paths or [
        os.path.join(home, ".claude.json"),
        os.path.join(home, "AppData", "Roaming", "Claude", "claude_desktop_config.json"),
        os.path.join(home, "Library", "Application Support", "Claude",
                     "claude_desktop_config.json"),
        os.path.join(home, ".config", "Claude", "claude_desktop_config.json"),
    ]
    ports = {}
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            cfg = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for server, entry in (cfg.get("mcpServers") or {}).items():
            if not isinstance(entry, dict):
                continue
            url = entry.get("url") or ""
            host = urlparse(url).hostname or ""
            if host in ("127.0.0.1", "localhost", "::1") and urlparse(url).port:
                ports[urlparse(url).port] = server
    return ports


def port_owners(ports):
    """{pid: server_name} by asking the OS who is listening on each configured port.

    This exists because of a Windows trap: `Win32_Process.CommandLine` comes back EMPTY
    for a process started at a different elevation than the query, so a daemon can be
    running, serving, and completely invisible to command-line matching. Measured on a
    live hub: the telegram daemon on 8765 was listening the whole time and the first
    version of this script reported it as absent. A prober that silently omits the very
    thing it is measuring is worse than no prober.
    """
    if not ports:
        return {}
    owners = {}
    try:
        if os.name == "nt":
            cmd = ("Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
                   "Select-Object LocalPort,OwningProcess | ConvertTo-Json -Compress")
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                                  "-Command", cmd], capture_output=True, text=True,
                                 timeout=60, encoding="utf-8", errors="replace")
            rows = json.loads(out.stdout or "[]")
            if isinstance(rows, dict):
                rows = [rows]
            for row in rows:
                srv = ports.get(row.get("LocalPort"))
                if srv:
                    owners[row.get("OwningProcess")] = srv
        else:
            out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                                 capture_output=True, text=True, timeout=60)
            for line in out.stdout.splitlines()[1:]:
                cols = line.split()
                if len(cols) < 9 or not cols[1].isdigit():
                    continue
                tail = cols[8].rsplit(":", 1)
                if len(tail) == 2 and tail[1].isdigit():
                    srv = ports.get(int(tail[1]))
                    if srv:
                        owners[int(cols[1])] = srv
    except FileNotFoundError:
        # No `lsof` (common on minimal Linux images). Say it: without the port map, a
        # daemon whose command line does not contain "mcp" is invisible to this tool.
        print("warning: `lsof` not found -- daemons can only be matched by command line; "
              "install lsof for a complete picture", file=sys.stderr)
        return owners
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("warning: could not read listening ports (%s) -- falling back to command-line "
              "matching only" % type(exc).__name__, file=sys.stderr)
        return owners
    return owners


def server_key(name, cmdline):
    """Best-effort identity of the MCP server behind a command line.

    Prefers a package-ish token containing 'mcp' (`@scope/whatsapp-mcp`, `n8n-mcp`,
    `mongodb-mcp-server`). Falls back to the containing directory of the entry script,
    which is what a git-cloned server looks like (`.../telegram-mcp/main.py`).
    """
    best = None
    for raw in PKG_RE.findall(cmdline):
        tok = raw.strip("\"'").rstrip(".,;")
        if tok.startswith("-") or "__" in tok or len(tok) > MAX_TOKEN:
            continue                    # a flag or a tool-name list, never a package
        if tok.lower() in NOISE_TOKENS or tok.lower().endswith((".log", ".json", ".md")):
            continue
        # `.../node_modules/@scope/whatsapp-mcp/dist/index.js` -> keep the package dir
        tok = tok.replace("\\", "/").rstrip("/").split("/")[-1]
        tok = re.sub(r"\.(?:js|mjs|cjs|py|exe|cmd|sh|ps1)$", "", tok)
        if not tok or tok.lower() in NOISE_TOKENS:
            continue
        if best is None or len(tok) > len(best):    # the longer token is the specific one
            best = tok
    if best:
        return best.lower()
    # No 'mcp'-ish package token: name it after the directory of the first script argument.
    for tok in cmdline.replace("\\", "/").split():
        tok = tok.strip("\"'")
        if tok.endswith((".py", ".js", ".mjs")) and "/" in tok:
            return tok.rsplit("/", 2)[-2].lower()
    return (name or "unknown").lower()


def collect(procs, markers=None, owners=None):
    """-> (servers {name: [proc...]}, wrappers [proc...], blind_count)

    `blind_count` is the number of processes whose command line could not be read; it is
    reported, never hidden, because those are exactly the ones a cmdline matcher misses.
    """
    markers = markers if markers is not None else config_markers()
    owners = owners if owners is not None else port_owners(config_ports())
    servers, wrappers, blind = {}, [], 0
    for pid, name, rss, cmdline in procs:
        by_port = owners.get(pid)
        if by_port:                     # the OS says this pid serves a configured daemon
            servers.setdefault(by_port, []).append((pid, name, rss, cmdline))
            continue
        if not cmdline:
            blind += 1
            continue
        low = cmdline.lower()
        if SKIP_SELF.search(cmdline) or CLIENT_RE.search(cmdline):
            continue
        hit = next((srv for mark, srv in markers.items() if mark in low), None)
        if hit is None and "mcp" not in low:
            continue
        if name.lower() in WRAPPER_NAMES:
            wrappers.append((pid, name, rss, cmdline))
            continue
        servers.setdefault(hit or server_key(name, cmdline), []).append(
            (pid, name, rss, cmdline))
    return servers, wrappers, blind


def mb(n):
    return n / 1024.0 / 1024.0


def rows_for(servers):
    rows = []
    for key, procs in servers.items():
        total = sum(p[2] for p in procs)
        largest = max(p[2] for p in procs)
        rows.append({"server": key, "copies": len(procs), "sum_mb": round(mb(total), 1),
                     "largest_mb": round(mb(largest), 1),
                     "redundant_mb": round(mb(total - largest), 1)})
    return sorted(rows, key=lambda r: -r["redundant_mb"])


def report(servers, wrappers, blind=0, show_all=False, out=sys.stdout):
    rows = rows_for(servers)
    wrap_mb = round(mb(sum(w[2] for w in wrappers)), 1)

    print("%-34s %7s %10s %10s %12s" % ("server", "copies", "sum MB", "largest", "redundant"),
          file=out)
    print("-" * 76, file=out)
    for r in rows:
        flag = "  <-- diet candidate" if r["copies"] > 1 else ""
        print("%-34s %7d %10.1f %10.1f %12.1f%s"
              % (r["server"], r["copies"], r["sum_mb"], r["largest_mb"],
                 r["redundant_mb"], flag), file=out)
    print("-" * 76, file=out)
    print("%-34s %7d %10.1f %10s %12.1f"
          % ("TOTAL", sum(r["copies"] for r in rows), sum(r["sum_mb"] for r in rows),
             "-", sum(r["redundant_mb"] for r in rows)), file=out)
    print("%-34s %7d %10.1f" % ("launcher wrappers (npx/uv/cmd)", len(wrappers), wrap_mb),
          file=out)
    print("\nRedundant = sum - largest: an UPPER BOUND on what one shared daemon returns.\n"
          "Copies share code pages, so the real reclaim is lower. The reliable finding is\n"
          "the copy count: every copy is a separate connection, lock and session upstream.",
          file=out)
    if blind:
        print("\n%d process(es) had an unreadable command line and were skipped. On Windows\n"
              "CIM returns an empty CommandLine for processes of another elevation; those\n"
              "are only identifiable by the port they listen on (configured daemons above\n"
              "are matched that way). Run this from the same account that owns them for a\n"
              "complete picture." % blind, file=out)
    if show_all:
        print("\nmatched processes:", file=out)
        for key, procs in sorted(servers.items()):
            for pid, name, rss, cmdline in procs:
                print("  %-24s pid=%-7d %7.1f MB  %s"
                      % (key, pid, mb(rss), cmdline[:110]), file=out)
    return {"servers": rows, "wrappers": {"count": len(wrappers), "sum_mb": wrap_mb}}


def main(argv):
    servers, wrappers, blind = collect(list_processes())
    if "--json" in argv:
        print(json.dumps({"servers": rows_for(servers),
                          "wrappers": {"count": len(wrappers),
                                       "sum_mb": round(mb(sum(w[2] for w in wrappers)), 1)},
                          "unreadable_cmdline": blind}, indent=2))
        return 0
    if not servers and not wrappers:
        print("No MCP server processes found. Either nothing is running, or this machine\n"
              "names them in a way the 'mcp' match misses -- check with --all.")
        return 0
    report(servers, wrappers, blind=blind, show_all="--all" in argv)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException as exc:        # crash-guard: a dead prober must not read as "clean"
        print("CRASH: %r" % (exc,), file=sys.stderr)
        sys.exit(4)
