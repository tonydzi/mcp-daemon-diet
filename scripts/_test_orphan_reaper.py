"""Tests for orphan_reaper.py. Exit 0 = green.

Fixtures need no real processes: reused PPID, launchd service vs abandoned group, zombie with a live
parent, git ancestor protection. L1 (Windows only) builds a real orphan tree and kills it.
Each check below has been shown to fail when the rule it guards is removed from the reaper.
"""
import os, subprocess, sys, time, uuid
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import orphan_reaper as R
import psutil

fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  :: {detail}"))
    if not ok:
        fails.append(name)


class FakeProc:
    def __init__(s, pid, ct): s.pid, s._ct = pid, ct
    def create_time(s): return s._ct


def P(pid, ppid, name, ct, cmd="", status="running", pgid=None, sid=1):
    # sid=1: the session leader (pid 1 fixture) is alive, i.e. an agent's terminal/app session
    return pid, {"pid": pid, "ppid": ppid, "name": name, "cmd": cmd, "cmdline": cmd.split(),
                 "create_time": ct, "status": status, "pgid": pgid, "sid": sid, "proc": FakeProc(pid, ct)}


now = time.time()
H = 3600

R.WIN = True
procs = dict([P(10, 999, "cmd.exe", now - 2*H), P(11, 10, "ping.exe", now - H), P(12, 10, "pythonw.exe", now - 3*H)])
kill, *_ = R.plan(procs, 60, 5, now=now)
check("W1 orphan and its true child are targets", 10 in kill and 11 in kill, kill)
check("W1 process older than its 'parent' (reused PPID) is not", 12 not in kill, kill)
procs = dict([P(20, 21, "node.exe", now - 2*H), P(21, 4, "notepad.exe", now - 60)])
check("W2 PPID owned by a younger process = orphan", 20 in R.plan(procs, 60, 5, now=now)[0])
procs = dict([P(30, 999, "bash.exe", now - 2*H), P(31, 30, "git.exe", now - H)])
check("W3 ancestor of a live git is protected", 30 not in R.plan(procs, 60, 5, now=now)[0])

R.WIN = False
procs = dict([P(1, 0, "launchd", now - 9*H, pgid=1),
              P(40, 1, "node", now - 2*H, cmd="node mcp-daemon.js", pgid=40),       # service: own group
              P(41, 1, "bash", now - 2*H, cmd="bash -c long", pgid=777),            # abandoned group
              P(42, 41, "grep", now - 2*H, cmd="grep -r x", pgid=777),
              P(43, 1, "node", now - 2*H, pgid=50), P(50, 1, "zsh", now - 3*H, pgid=50),  # leader alive
              P(60, 1, "python3", now - 5*H, pgid=60),
              P(61, 60, "node", now - 4*H, status=psutil.STATUS_ZOMBIE, pgid=60)])
kill, _, _, zombies, _ = R.plan(procs, 60, 5, now=now)
check("U1 launchd service (pgid == pid) untouched", 40 not in kill, kill)
check("U2 abandoned group and its child are targets", 41 in kill and 42 in kill, kill)
check("U3 live group leader = not an orphan", 43 not in kill, kill)
check("U4 zombie is not a target, its parent is named", 61 not in kill and any(z["parent"] == 60 for z in zombies), zombies)
check("U5 zombie's parent untouched", 60 not in kill, kill)
# double-fork daemon: setsid() + fork, session leader 70 exited on purpose -> sid points to a dead leader
procs = dict([P(1, 0, "launchd", now - 9*H, pgid=1),
              P(71, 1, "node", now - 2*H, cmd="node daemon.js", pgid=70, sid=70)])
check("U6 double-fork daemon (dead session leader) untouched", 71 not in R.plan(procs, 60, 5, now=now)[0])

if os.name == "nt":
    R.WIN = True
    mark = "ORPHPY" + uuid.uuid4().hex[:8]
    ids = subprocess.run(["powershell", "-NoProfile", "-Command",
        f"$a=Start-Process cmd -ArgumentList '/c set T={mark}& ping -n 300 127.0.0.1 >nul' -WindowStyle Hidden -PassThru; $a.Id"],
        capture_output=True, text=True).stdout.split()
    a = psutil.Process(int(ids[0]))
    keep = subprocess.Popen(["cmd", "/c", f"set T={mark}K& ping -n 300 127.0.0.1 >nul"], creationflags=0x08000000)
    keep_p = psutil.Process(keep.pid)
    time.sleep(2)
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.realpath(__file__)), "orphan_reaper.py"),
                        "--apply", "--shell-minutes", "0", "--grace", "2", "--only", mark], capture_output=True, text=True)
    time.sleep(1)
    check("L1 live orphan cmd killed", not a.is_running(), r.stdout + r.stderr)
    check("L1 process with a live parent survives", keep_p.is_running(), r.stdout)
    for p in (a, keep_p):  # cleanup is identity-checked too: never taskkill a bare PID
        try:
            for c in p.children(recursive=True):
                c.kill()
            p.kill()
        except psutil.Error:
            pass

print("result:", "GREEN" if not fails else f"RED {len(fails)}")
sys.exit(1 if fails else 0)
