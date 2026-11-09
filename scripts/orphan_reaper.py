#!/usr/bin/env python3
"""orphan_reaper.py - kill only PROVABLY abandoned agent-tool process trees (Windows / macOS / Linux).

Agent CLIs (codex, claude, node/bun MCP servers, shells running `grep -r` for them) leave orphans
when their parent dies on a timeout. "The parent is dead, kill the child" sounds right and is not:
PIDs get reused, services legitimately have dead parents, and git/ssh/backup jobs look exactly
like orphans. Every one of those mistakes has cost us something real (a crashed desktop app, a
killed shared MCP daemon, a corrupted git pack, dropped SSH sessions, a broken backup mirror).

The rule this script enforces - all of it, or no kill:
  1. Identity is (pid, create_time), never a bare PID. It is re-checked at the signal itself:
     psutil.Process verifies create_time before sending, so a reused PID raises NoSuchProcess.
  2. Orphanhood is proven.
       Windows: the parent is gone, or the PPID now belongs to a process YOUNGER than the child
                (Windows never rewrites PPIDs, so a stale PPID points at whoever got that number).
                Tree walk follows only true children: PPID matches AND child born after parent.
       Unix:    parent is pid 1 AND the process is not its own group leader (pgid != pid) AND the
                group leader is dead. launchd/systemd services lead their own group; they stay.
  3. Zombies (Z) are never "killed" - they are already dead. Under pid 1 init reaps them; with a
     live parent we name the PARENT that is not calling wait() and leave it alone.
  4. Grace period: look again after --grace seconds; kill only what is still the same process and
     still orphaned. A root that found a parent spares its whole subtree.
  5. Protected, also as descendants: listeners on --protect-port and their ancestors, git / ssh /
     rsync / robocopy / backup tools and their ancestors, --protect-cmd substrings, OS processes.
  6. One automatic reaper per machine. Two patrols racing each other is how this goes wrong.

Default is a dry run. `--apply` kills. Exit: 0 ok, 1 targets found but none killed for lack of
rights, 3 psutil missing (no verdict - which is NOT "zero orphans").

    pip install psutil
    python orphan_reaper.py                       # dry run: what would die and why
    python orphan_reaper.py --apply --protect-port 8765 --protect-cmd nightly_backup
"""
import argparse, json, os, sys, time

try:
    import psutil
except ImportError:  # pragma: no cover
    print("psutil is not installed - no verdict (this is NOT 'no orphans')", file=sys.stderr)
    sys.exit(3)

WIN = os.name == "nt"
# Only these families may be the ROOT of a kill.
SHELLS = {"codex", "node", "node_repl", "bun", "cmd", "bash", "sh", "zsh", "conhost", "scp"}  # python is NOT here: long-lived services run on it
SEARCHERS = {"grep", "find", "rg", "sed", "awk", "sort", "uniq", "wc", "head", "tail", "cat",
             "ls", "tr", "xargs", "sleep", "timeout", "du", "stat", "fd"}
# Never touched, not even as a descendant: BSOD, a dropped session, or corrupted data.
FORBID = {"csrss", "wininit", "winlogon", "services", "lsass", "smss", "svchost", "explorer",
          "system", "registry", "launchd", "systemd", "init", "kernel_task",
          "git", "ssh", "sshd", "sshd-session", "sshd-auth", "rsync", "robocopy", "rclone", "restic", "borg"}
PROTECT_PORTS = set()
PROTECT_CMDLINE = ()


def base(name):
    n = (name or "").lower()
    return n[:-4] if n.endswith(".exe") else n


def ident(p):
    return (p.pid, round(p.create_time(), 2))


def snapshot():
    procs = {}
    for p in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time", "status"]):
        i = p.info
        if i["create_time"] is None:
            continue
        procs[i["pid"]] = dict(i, cmd=" ".join(i["cmdline"] or []), proc=p)
    if not WIN:
        for i in procs.values():
            try:
                i["pgid"] = os.getpgid(i["pid"])
            except Exception:
                i["pgid"] = None
    return procs


def true_child(kid, parent):
    return parent is not None and kid["create_time"] >= parent["create_time"]


def is_orphan(i, procs):
    par = procs.get(i["ppid"])
    if WIN:
        return i["ppid"] != 0 and (par is None or not true_child(i, par))
    if i["ppid"] != 1:
        return False
    pg = i.get("pgid")
    return pg is not None and pg != i["pid"] and pg not in procs  # group leader is dead


def protected_ids(procs):
    prot = {}

    def up(pid, why):
        cur, d = procs.get(pid), 0
        while cur and d < 12 and cur["pid"] not in (0, 1):
            prot.setdefault(cur["pid"], why)
            cur, d = procs.get(cur["ppid"]), d + 1

    if PROTECT_PORTS:
        try:
            for c in psutil.net_connections(kind="inet"):
                if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port in PROTECT_PORTS and c.pid:
                    up(c.pid, f"port {c.laddr.port}")
        except (psutil.AccessDenied, PermissionError):
            pass  # macOS without root cannot see sockets: names and command lines still protect
    for i in procs.values():
        if base(i["name"]) in FORBID or any(s in i["cmd"] for s in PROTECT_CMDLINE):
            up(i["ppid"], f"ancestor of {base(i['name'])} {i['pid']}")
            if base(i["name"]) not in FORBID:
                prot.setdefault(i["pid"], "protected command")
    return prot


def plan(procs, shell_min, searcher_min, only=None, now=None):
    now = now or time.time()
    prot = protected_ids(procs)
    zombies, roots = [], []
    for i in procs.values():
        nm = base(i["name"])
        if i.get("status") == psutil.STATUS_ZOMBIE:
            if i["ppid"] not in (0, 1) and i["ppid"] in procs:
                zombies.append({"pid": i["pid"], "name": nm, "parent": i["ppid"],
                                "parent_name": base(procs[i["ppid"]]["name"])})
            continue
        if nm in FORBID or i["pid"] in prot or i["pid"] == os.getpid():
            continue
        age_min = (now - i["create_time"]) / 60
        limit = shell_min if nm in SHELLS else searcher_min if nm in SEARCHERS else None
        if limit is None or age_min < limit or not is_orphan(i, procs):
            continue
        if only and only not in i["cmd"]:
            continue
        roots.append(i["pid"])
    kids = {}
    for i in procs.values():
        kids.setdefault(i["ppid"], []).append(i)
    kill, root_of, queue = {}, {r: r for r in roots}, list(roots)
    while queue:
        pid = queue.pop()
        i = procs.get(pid)
        if pid in kill or i is None or base(i["name"]) in FORBID or pid in prot:
            continue
        if i.get("status") == psutil.STATUS_ZOMBIE:
            continue  # a signal does not reach a zombie
        kill[pid] = ident(i["proc"])
        for k in kids.get(pid, []):
            if true_child(k, i):
                root_of.setdefault(k["pid"], root_of[pid])
                queue.append(k["pid"])
    return kill, root_of, set(roots), zombies, prot


def main(argv=None):
    global PROTECT_PORTS, PROTECT_CMDLINE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually kill (default: dry run)")
    ap.add_argument("--shell-minutes", type=float, default=60, help="min age of an orphaned shell/runtime")
    ap.add_argument("--searcher-minutes", type=float, default=5, help="min age of an orphaned grep/find/rg")
    ap.add_argument("--grace", type=float, default=20, help="seconds between the two looks")
    ap.add_argument("--protect-port", type=int, action="append", default=[], help="listener port to protect")
    ap.add_argument("--protect-cmd", action="append", default=[], help="command-line substring to protect")
    ap.add_argument("--only", default=None, help="tests: roots must contain this command-line substring")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    PROTECT_PORTS = set(a.protect_port)
    PROTECT_CMDLINE = tuple(a.protect_cmd)

    procs = snapshot()
    kill, root_of, roots, zombies, prot = plan(procs, a.shell_minutes, a.searcher_minutes, a.only)
    rep = {"mode": "apply" if a.apply else "dry", "targets": len(kill), "roots": len(roots),
           "protected": len(prot), "zombies_with_live_parent": zombies, "killed": 0,
           "spared_identity": 0, "spared_grace": 0, "denied": 0, "gone": 0,
           "target_list": [{"pid": p, "name": base(procs[p]["name"]), "cmd": procs[p]["cmd"][:160]} for p in kill]}
    if a.apply and kill:
        if a.grace > 0:
            time.sleep(a.grace)
            procs2 = snapshot()
            for pid in list(kill):
                j = procs2.get(pid)
                same = j is not None and ident(j["proc"]) == kill[pid]
                if not same or (pid in roots and not is_orphan(j, procs2)):
                    del kill[pid]; rep["spared_grace"] += 1
            for pid in list(kill):
                if root_of.get(pid) not in kill:
                    del kill[pid]; rep["spared_grace"] += 1
        live = []
        for pid in kill:
            p = procs[pid]["proc"]
            try:
                if not p.is_running():  # compares create_time: a reused PID returns False
                    rep["spared_identity"] += 1; continue
                p.terminate(); live.append(p); rep["killed"] += 1
            except psutil.NoSuchProcess:
                rep["gone"] += 1
            except psutil.AccessDenied:
                rep["denied"] += 1
        _, alive = psutil.wait_procs(live, timeout=3)
        for p in alive:
            try:
                p.kill()  # also identity-checked by psutil
            except psutil.Error:
                pass
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(f"[{rep['mode']}] roots {rep['roots']} - targets {rep['targets']} - protected {rep['protected']}"
              f" - killed {rep['killed']} - spared: identity {rep['spared_identity']}, grace {rep['spared_grace']}"
              f" - denied {rep['denied']} - already gone {rep['gone']}")
        for z in zombies:
            print(f"  zombie {z['pid']} {z['name']}: parent {z['parent']} {z['parent_name']} is not calling wait() - left alone")
        for t in rep["target_list"][:40]:
            print(f"  target {t['pid']} {t['name']} {t['cmd']}")
    return 1 if (a.apply and rep["targets"] and not rep["killed"] and rep["denied"]) else 0


if __name__ == "__main__":
    sys.exit(main())
