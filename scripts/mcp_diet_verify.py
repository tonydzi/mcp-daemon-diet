# -*- coding: utf-8 -*-
"""mcp_diet_verify.py -- did the diet actually take, and is the daemon actually up?

    python mcp_diet_verify.py                       # check every server in the config
    python mcp_diet_verify.py --servers telegram,n8n   # only these (fail if still stdio)
    python mcp_diet_verify.py --config path.json    # a specific client config
    python mcp_diet_verify.py --quiet               # exit code only

WHY THIS EXISTS
  "I switched it to a daemon" is an intention. The fact is: (1) the client config no longer
  says stdio, and (2) something is actually listening where the config points. Miss (2) and
  every session silently loses the connector -- the config looks perfect and the tool is
  simply gone. Verify by READING THE FACT, not the intention.

CHECKS
  1. Any server registered as `stdio` is reported as a diet candidate. With `--servers`
     it is a FAILURE (you declared it converted); without, it is informational.
  2. Every http/sse server pointing at a local address must accept a TCP connection.
     A dead daemon behind a live config = FAIL.
  3. Local daemons also get an HTTP liveness probe. A TCP connect only proves bind+listen;
     a process can hold the port and serve nothing. Any HTTP status (404 included) means
     alive -- we deliberately do NOT probe an SSE stream endpoint, because that stream
     never closes and the probe hangs forever (paid for that one on 2026-08-06).

EXIT  0 = clean | 1 = something is wrong (details on stdout) | 4 = this script crashed.
Test: _test_mcp_diet.py
"""
import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
DEFAULT_CONFIGS = [
    os.path.join("~", ".claude.json"),
    os.path.join("~", "AppData", "Roaming", "Claude", "claude_desktop_config.json"),
    os.path.join("~", "Library", "Application Support", "Claude",
                 "claude_desktop_config.json"),
    os.path.join("~", ".config", "Claude", "claude_desktop_config.json"),
]


def load_servers(config=None):
    """-> (servers dict, path used). Raises ValueError with a readable message."""
    paths = [config] if config else [os.path.expanduser(p) for p in DEFAULT_CONFIGS]
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        try:
            cfg = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("cannot read %s: %r" % (path, exc))
        return (cfg.get("mcpServers") or {}), path
    raise ValueError("no MCP client config found (looked in: %s)" % ", ".join(paths))


def tcp_alive(host, port, timeout=4):
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except OSError:
        return False


def http_alive(url, timeout=5):
    """True if something HTTP-shaped answers. 4xx/5xx count: the service replied.

    Probes the ORIGIN, never the configured path -- an SSE endpoint is an open stream and
    a GET against it hangs until the timeout even when the daemon is perfectly healthy.
    """
    parts = urlparse(url)
    origin = "%s://%s" % (parts.scheme or "http", parts.netloc)
    try:
        urllib.request.urlopen(origin, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True                     # answered with a status = alive
    except (urllib.error.URLError, OSError, ValueError):
        return False


def check(servers, required=None):
    """-> (problems, notes). `required` names servers that MUST be off stdio."""
    problems, notes = [], []
    required = {s.strip() for s in (required or []) if s.strip()}
    for name in sorted(required - set(servers)):
        problems.append("%s: not in the config at all -- wrong config file, or renamed?"
                        % name)
    for name, entry in sorted(servers.items()):
        if not isinstance(entry, dict):
            problems.append("%s: malformed entry (%s)" % (name, type(entry).__name__))
            continue
        kind = (entry.get("type") or "stdio").lower()
        if kind == "stdio":
            msg = "%s: still stdio -> one copy per session (see docs/RECIPE.md)" % name
            (problems if name in required else notes).append(msg)
            continue
        url = entry.get("url") or ""
        parts = urlparse(url)
        if not parts.hostname:
            problems.append("%s: type=%s but no usable url (%r)" % (name, kind, url))
            continue
        if parts.hostname not in LOCAL_HOSTS:
            notes.append("%s: remote (%s) -- not this machine's business" % (name,
                                                                            parts.hostname))
            continue
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if not tcp_alive(parts.hostname, port):
            problems.append("%s: daemon DEAD at %s:%s -> start its launcher, check the "
                            "watchdog and the daemon log" % (name, parts.hostname, port))
        elif not http_alive(url):
            problems.append("%s: port %s is open but the service did not answer HTTP -> "
                            "zombie daemon, restart it when no session is live"
                            % (name, port))
    return problems, notes


def main(argv=None):
    ap = argparse.ArgumentParser(description="verify the MCP daemon diet")
    ap.add_argument("--config", help="path to a client config (default: autodetect)")
    ap.add_argument("--servers", help="comma-separated names that MUST be off stdio")
    ap.add_argument("--quiet", action="store_true", help="exit code only")
    args = ap.parse_args(argv)

    try:
        servers, path = load_servers(args.config)
    except ValueError as exc:
        print("FAIL: %s" % exc)
        return 1
    problems, notes = check(servers, (args.servers or "").split(","))
    if args.quiet:
        return 1 if problems else 0
    print("config: %s (%d server(s))" % (path, len(servers)))
    for note in notes:
        print("  note: %s" % note)
    for problem in problems:
        print("  FAIL: %s" % problem)
    print("OK: every shared daemon in this config is alive" if not problems
          else "%d problem(s)" % len(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:        # crash-guard: own death must not read as "applied"
        print("CRASH: %r" % (exc,))
        sys.exit(4)
