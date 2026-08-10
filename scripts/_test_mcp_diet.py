# -*- coding: utf-8 -*-
"""Regression tests for mcp_diet_measure.py and mcp_diet_verify.py.

    python _test_mcp_diet.py          # exit 0 = all green, 1 = something regressed

No pytest, no network, no fixtures directory: every case builds its own config in a temp
file and its own fake process list, so this runs anywhere Python does. A real listening
socket is opened on an ephemeral port for the "daemon alive" cases -- binding 127.0.0.1
is the only OS interaction, and it is the one thing worth testing for real.

The cases are written to FAIL on a broken checker, not merely to pass on a working one:
a stdio server, a dead port and a zombie (listening but not answering HTTP) must each be
reported, and a config with none of those must come back clean.
"""
import json
import os
import socket
import sys
import tempfile
import threading
import http.server

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcp_diet_measure as measure          # noqa: E402
import mcp_diet_verify as verify            # noqa: E402

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s %s" % (label, detail))
        FAILURES.append(label)


def write_config(servers):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": servers}, fh)
    return path


class _Quiet(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                   # noqa: N802
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args):                      # keep the test output clean
        pass


def serving_port():
    """A real HTTP server on an ephemeral port -> (port, shutdown callable)."""
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1], srv.shutdown


def silent_port():
    """A socket that LISTENS but never answers -> the zombie-daemon case."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock.getsockname()[1], sock.close


def dead_port():
    """A port number nothing is listening on."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# --------------------------------------------------------------------- verify
def test_verify():
    print("mcp_diet_verify")
    port, stop = serving_port()
    try:
        cfg = write_config({"good": {"type": "http", "url": "http://127.0.0.1:%d/mcp" % port}})
        servers, _ = verify.load_servers(cfg)
        problems, notes = verify.check(servers)
        check("live daemon passes", problems == [], problems)
        check("live daemon has no notes either", notes == [], notes)
        os.unlink(cfg)

        # stdio: informational by default, a failure when the caller says it must be off
        cfg = write_config({"fat": {"command": "npx", "args": ["-y", "some-mcp"]}})
        servers, _ = verify.load_servers(cfg)
        problems, notes = verify.check(servers)
        check("stdio is a note by default", problems == [] and len(notes) == 1, (problems, notes))
        problems, _ = verify.check(servers, ["fat"])
        check("stdio FAILS when required", len(problems) == 1 and "still stdio" in problems[0],
              problems)
        problems, _ = verify.check(servers, ["typo-name"])
        check("a required server missing from config FAILS",
              any("not in the config" in p for p in problems), problems)
        os.unlink(cfg)

        # dead daemon behind a live-looking config
        cfg = write_config({"ghost": {"type": "sse",
                                      "url": "http://127.0.0.1:%d/sse" % dead_port()}})
        servers, _ = verify.load_servers(cfg)
        problems, _ = verify.check(servers)
        check("dead daemon FAILS", len(problems) == 1 and "DEAD" in problems[0], problems)
        os.unlink(cfg)

        # zombie: the exact case a TCP-only checker declares healthy
        zport, zstop = silent_port()
        cfg = write_config({"zombie": {"type": "http",
                                       "url": "http://127.0.0.1:%d/mcp" % zport}})
        servers, _ = verify.load_servers(cfg)
        problems, _ = verify.check(servers)
        check("listening-but-mute daemon FAILS",
              len(problems) == 1 and "zombie" in problems[0], problems)
        zstop()
        os.unlink(cfg)

        # a remote server is not this machine's problem
        cfg = write_config({"cloud": {"type": "http", "url": "https://mcp.example.com/mcp"}})
        servers, _ = verify.load_servers(cfg)
        problems, notes = verify.check(servers)
        check("remote server is a note, not a failure",
              problems == [] and len(notes) == 1, (problems, notes))
        os.unlink(cfg)

        # malformed input must be reported, never crash
        cfg = write_config({"broken": {"type": "http", "url": "not-a-url"}})
        servers, _ = verify.load_servers(cfg)
        problems, _ = verify.check(servers)
        check("unusable url FAILS", len(problems) == 1, problems)
        os.unlink(cfg)

        cfg = write_config({"weird": "just-a-string"})
        servers, _ = verify.load_servers(cfg)
        problems, _ = verify.check(servers)
        check("malformed entry FAILS", len(problems) == 1, problems)
        os.unlink(cfg)

        try:
            verify.load_servers(os.path.join(tempfile.gettempdir(), "definitely-absent.json"))
            check("missing config raises", False, "no exception")
        except ValueError:
            check("missing config raises", True)
    finally:
        stop()


# -------------------------------------------------------------------- measure
FAKE = [
    # (pid, name, rss, cmdline)
    (1, "python.exe", 300 * 1024 ** 2, r"C:\mcp\telegram-mcp\.venv\python.exe C:\mcp\telegram-mcp\main.py"),
    (2, "python.exe", 300 * 1024 ** 2, r"C:\mcp\telegram-mcp\.venv\python.exe C:\mcp\telegram-mcp\main.py"),
    (3, "python.exe", 400 * 1024 ** 2, r"C:\mcp\telegram-mcp\.venv\python.exe C:\mcp\telegram-mcp\main.py"),
    (4, "node.exe", 200 * 1024 ** 2, r"node C:\npm\node_modules\n8n-mcp\dist\mcp\index.js"),
    (5, "cmd.exe", 8 * 1024 ** 2, r"cmd /c mcp_n8n_http.cmd"),
    (6, "claude.exe", 500 * 1024 ** 2, "claude.exe --allowedTools mcp__telegram__get_me --strict-mcp-config"),
    (7, "node.exe", 90 * 1024 ** 2, "node /opt/whatever/server.js --port 3000"),
    (8, "python.exe", 130 * 1024 ** 2, ""),          # unreadable command line
]


def test_measure():
    print("mcp_diet_measure")
    servers, wrappers, blind = measure.collect(FAKE, markers={}, owners={})
    check("three copies of one server are grouped",
          servers.get("telegram-mcp") and len(servers["telegram-mcp"]) == 3,
          sorted(servers))
    check("the client is NOT counted as a server",
          not any("claude" in k for k in servers), sorted(servers))
    check("no tool-name token leaks in as a server key",
          not any("__" in k for k in servers), sorted(servers))
    check("launcher wrapper is counted separately", len(wrappers) == 1, wrappers)
    check("non-mcp process ignored", all("whatever" not in k for k in servers), sorted(servers))
    check("unreadable command line is counted, not silently dropped", blind == 1, blind)

    rows = {r["server"]: r for r in measure.rows_for(servers)}
    tg = rows["telegram-mcp"]
    check("sum is the sum", tg["sum_mb"] == 1000.0, tg)
    check("redundant excludes the one copy you keep", tg["redundant_mb"] == 600.0, tg)

    # the port map must win over any command-line guess -- that is the whole point of it
    servers, _, blind = measure.collect(FAKE, markers={}, owners={8: "telegram"})
    check("a port-owned process is labelled by the config name",
          servers.get("telegram") and servers["telegram"][0][0] == 8, sorted(servers))
    check("a port-identified process is not also counted as blind", blind == 0, blind)

    # config markers name a server the heuristic would have called something else
    servers, _, _ = measure.collect(FAKE, markers={"index.js": "n8n"}, owners={})
    check("config marker overrides the guessed key", "n8n" in servers, sorted(servers))

    # An interpreter name must never become a marker: with "node" as a marker for server X,
    # every unrelated node process on the machine is counted as another copy of X and the
    # tool invents duplicates. Found by an external review panel, 2026-08-10.
    cfg = write_config({"weather": {"command": "node", "args": ["/opt/weather/server.js"]}})
    markers = measure.config_markers([cfg])
    check("interpreter name is not a marker", "node" not in markers, markers)
    check("generic script name is not a marker", "server.js" not in markers, markers)
    check("the install directory is the marker instead",
          markers.get("weather") == "weather", markers)
    unrelated = [(1, "node.exe", 10 ** 8, "node /opt/other-app/index.js"),
                 (2, "node.exe", 10 ** 8, "node /opt/weather/server.js")]
    servers, _, _ = measure.collect(unrelated, markers=markers, owners={})
    check("an unrelated node app is NOT counted as a copy",
          len(servers.get("weather", [])) == 1, {k: len(v) for k, v in servers.items()})
    os.unlink(cfg)

    for bare in ("node", "python3", "npx", "index.js", "main.py", "run", "sh"):
        check("marker rejected: %s" % bare, measure.specific_marker(bare) is None,
              measure.specific_marker(bare))
    check("a real package IS a marker",
          measure.specific_marker("mongodb-mcp-server") == "mongodb-mcp-server")
    check("a flag is never a marker", measure.specific_marker("--transport") is None)

    cfg = write_config({"tg": {"type": "sse", "url": "http://127.0.0.1:8765/sse"},
                        "cloud": {"type": "http", "url": "https://mcp.example.com/mcp"},
                        "fat": {"command": "npx", "args": ["-y", "mongodb-mcp-server"]}})
    ports = measure.config_ports([cfg])
    check("local daemon port is picked up", ports == {8765: "tg"}, ports)
    markers = measure.config_markers([cfg])
    check("stdio args become markers", markers.get("mongodb-mcp-server") == "fat", markers)
    check("http entries contribute no process markers",
          "tg" not in markers.values(), markers)
    os.unlink(cfg)

    servers, wrappers, blind = measure.collect([], markers={}, owners={})
    check("empty process list is handled", (servers, wrappers, blind) == ({}, [], 0))


if __name__ == "__main__":
    test_verify()
    test_measure()
    print("\n%s" % ("ALL GREEN" if not FAILURES
                    else "%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES))))
    sys.exit(1 if FAILURES else 0)
