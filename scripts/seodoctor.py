#!/usr/bin/env python3
"""Self-healing preflight for the SEO manager. Idempotent. Run it before every workflow.

    python3 scripts/seodoctor.py                 # check + repair, print JSON
    python3 scripts/seodoctor.py --check         # report only, repair nothing
    python3 scripts/seodoctor.py --hard          # also force-restart the SERP daemon

Modelled on veikkaus-browser's `api-client.sh`, which is the reference for
self-healing in this setup. The four ideas worth copying, each of which was paid
for in a real incident there or here:

  1. HEALTH IS SEMANTIC, NOT A PORT PROBE. "Something answers on 8791" is not
     health. A daemon whose Chrome died still answers /health while every query
     fails in a confusing way. We require chrome_alive AND a tab pool.
  2. REAP THE WEDGED INSTANCE BEFORE LAUNCHING. A half-dead predecessor holds the
     resource the new one needs, so the launch fails in a way that looks like a
     bug in the launcher. (Here: an orphan Chrome holding the profile made every
     --start time out after 60s, forever. 2026-08-01.)
  3. SERIALIZE THE LAUNCH. Two near-simultaneous callers each spawning a daemon
     is how you get two instances fighting over one port.
  4. NEVER MATCH A PROCESS BY A PATTERN YOUR OWN COMMAND LINE CONTAINS.
     `pkill -f seo-serpd-profile` kills the shell running it. We scan /proc.

Exit 0 when everything needed is up (or was repaired), 1 when something is broken
that this script cannot fix by itself. It NEVER exits non-zero merely because a
project has not been set up - that is the owner's pending step, not a fault.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

API_PORT = int(os.environ.get("SERPD_PORT", "8791"))
PROFILE = os.environ.get("SERPD_PROFILE", "/tmp/seo-serpd-profile")
LOCKFILE = "/tmp/seo-serpd.ensure.lock"
CDP_PORTFILE = "/tmp/seo-serpd-cdp.port"


# --------------------------------------------------------------- process scan


def _cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace").replace("\0", " ")
    except OSError:
        return ""


def _pids_matching(needle: str, exclude_children: bool = True) -> list[int]:
    """/proc scan, never pgrep -f: that matches our own command line."""
    out = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == os.getpid():
            continue
        cmd = _cmdline(pid)
        if needle in cmd and not (exclude_children and "--type=" in cmd):
            out.append(pid)
    return out


# ------------------------------------------------------------------ the daemon


def serpd_health() -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/health", timeout=4) as r:
            return json.loads(r.read())
    except Exception:
        return None


def serpd_usable() -> tuple[bool, str]:
    """Semantic health. A port that answers is NOT the same as a working daemon."""
    h = serpd_health()
    if not h:
        return False, "no server on the API port"
    if not h.get("ok"):
        return False, "server reports not-ok"
    if not h.get("chrome_alive"):
        return False, "server is up but its chrome is dead - every query would fail"
    if not (h.get("tabs") or h.get("tab_pool")):
        return False, "server has no tab pool"
    return True, "healthy"


def reap_serpd() -> list[int]:
    """Kill a wedged server AND any chrome on our profile, then clear the portfile."""
    killed = []
    for pid in _pids_matching("serpd.py"):
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except OSError:
            pass
    for pid in _pids_matching(f"--user-data-dir={PROFILE}"):
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except OSError:
            pass
    for f in (CDP_PORTFILE,):
        try:
            os.unlink(f)
        except OSError:
            pass
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.unlink(os.path.join(PROFILE, lock))
        except OSError:
            pass
    if killed:
        time.sleep(2)
    return killed


def ensure_serpd(hard: bool = False, repair: bool = True) -> dict:
    ok, why = serpd_usable()
    if ok and not hard:
        return {"state": "already_healthy", "detail": why, **(serpd_health() or {})}
    if not repair:
        return {"state": "unhealthy", "detail": why, "repaired": False}

    # Serialize: two callers must not each spawn a daemon.
    lock = open(LOCKFILE, "w")
    try:
        try:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        except Exception:
            pass
        # Re-check under the lock - another caller may have just fixed it.
        ok, why = serpd_usable()
        if ok and not hard:
            return {"state": "healthy_after_wait", "detail": why, **(serpd_health() or {})}

        killed = []
        # A server that is present but NOT usable is worse than none: it holds the
        # port so a fresh one cannot bind, and the failure surfaces as a query
        # error rather than a startup error.
        if hard or serpd_health() is not None:
            killed = reap_serpd()

        proc = subprocess.run(
            [sys.executable, str(HERE / "serpd.py"), "--start"],
            capture_output=True, text=True, timeout=240,
        )
        ok, why = serpd_usable()
        return {
            "state": "repaired" if ok else "repair_failed",
            "detail": why,
            "killed_pids": killed,
            "start_stdout": (proc.stdout or "")[-400:],
            "start_stderr": (proc.stderr or "")[-400:],
            **(serpd_health() or {}),
        }
    finally:
        lock.close()


# ------------------------------------------------------------------- the rest


def check_deps() -> dict:
    out = {}
    for mod in ("websockets", "cryptography"):
        try:
            __import__(mod)
            out[mod] = True
        except ImportError:
            out[mod] = False
    out["chrome"] = any(
        os.path.exists(p) or subprocess.run(["which", n], capture_output=True).returncode == 0
        for n, p in (("google-chrome", "/usr/bin/google-chrome"),
                     ("chromium", "/usr/bin/chromium"))
    )
    return out


def check_providers(live: bool) -> dict:
    """What data sources are actually available.

    Two modes on purpose. The default is CHEAP - it reports which credentials
    exist without spending a single network call, because the preflight runs
    before every workflow and must stay a ~2s no-op on a healthy setup. `live`
    probes every source for real, which is what you want when a data call has
    started behaving oddly and you need to know whether the source or the code
    is at fault.
    """
    try:
        import providers as P
    except Exception as e:
        return {"ok": False, "error": f"provider registry unavailable: {e}"}
    if live:
        rows = P.probe_all()
        by_state: dict[str, list[str]] = {}
        for r in rows:
            by_state.setdefault(r["state"], []).append(r["provider"])
        return {"ok": True, "mode": "live", "by_state": by_state, "providers": rows}
    configured, missing = [], []
    for name, _cat, _cost, needs_key, _fn, _note in P.PROVIDERS:
        if not needs_key:
            continue
        env = {"openpagerank": ("OPENPAGERANK_API_KEY", "~/.openpagerank_key"),
               "cloudflare-radar": ("CLOUDFLARE_API_TOKEN", "~/.cloudflare_token"),
               "bing-webmaster": ("BING_WEBMASTER_API_KEY", "~/.bing_webmaster_key"),
               "serper": ("SERPER_API_KEY", "~/.serper_key"),
               "serpapi": ("SERPAPI_KEY", "~/.serpapi_key"),
               "pagespeed": ("GOOGLE_API_KEY", "~/.google_api_key")}.get(name)
        (configured if env and P.read_secret(*env) else missing).append(name)
    keyless = [n for n, _c, _co, k, _f, _no in P.PROVIDERS if not k]
    return {"ok": True, "mode": "cheap", "keyless_always_available": len(keyless),
            "keyed_configured": configured, "keyed_missing": missing,
            "note": "credential presence only - no network was touched. Run with --providers "
                    "for live probes. A source in keyed_missing is an UPGRADE that is not "
                    "installed, never a fault: the pipeline runs end to end on the keyless "
                    f"{len(keyless)}."}


def check_project(root: Path) -> dict:
    seo = root / ".seo"
    if not (seo / "config.json").exists():
        return {"exists": False,
                "note": "no project here - run seostate.py init. NOT a fault; the owner's step."}
    cfg = json.loads((seo / "config.json").read_text())
    return {
        "exists": True,
        "domain": cfg.get("domain"),
        "mode": cfg.get("mode"),
        "dr": cfg.get("dr"),
        "conventions": (seo / "conventions.md").exists(),
        "publish_paths": (seo / "publish-paths").exists(),
    }



def run_control() -> dict:
    """Prove the preflight's own readers discriminate - repairing nothing.

    A preflight that cannot tell a healthy daemon from a dead one is worse than
    absent: it reports green, every later refusal looks like a data problem, and
    the one instrument whose job is to say "the tooling is broken" is the
    instrument that is broken.

    Two specific traps are pinned here. `pgrep -f <pattern>` matches the
    WATCHING shell's own command line, so it reports a finished job as still
    running and `pkill -f` kills the shell that ran it - which is why this
    module scans /proc directly. And a port that answers is not a working
    daemon: serpd can be up with dead chrome, in which case every query fails
    while /health returns 200."""
    from controls import Controls
    c = Controls("seodoctor-control")

    # The /proc scanner must find a process that certainly exists (this one, by
    # a distinctive substring of its own argv) and must NOT invent one.
    me = _cmdline(os.getpid())
    c.check("proc_cmdline_is_readable", bool(me), "cannot read /proc - every pid check is blind")
    c.check("the_scanner_excludes_itself", os.getpid() not in _pids_matching("python"),
            "a scanner that counts itself reports a daemon that is not there")
    c.check("the_scanner_invents_nothing",
            _pids_matching("zzq-no-such-process-9f2b") == [],
            "if this is non-empty the needle is matching everything")
    c.check("a_missing_pid_reads_empty_rather_than_raising", _cmdline(999999) == "")

    # SEMANTIC health. Each of these shapes has to produce a distinct verdict,
    # or "up" silently absorbs "up but useless".
    real = serpd_health
    try:
        for shape, want_ok, why in (
            (None, False, "no server"),
            ({"ok": False}, False, "server says not-ok"),
            ({"ok": True, "chrome_alive": False, "tabs": 4}, False, "chrome dead"),
            ({"ok": True, "chrome_alive": True, "tabs": 0, "tab_pool": 0}, False, "no tabs"),
            ({"ok": True, "chrome_alive": True, "tabs": 4}, True, "healthy"),
        ):
            globals()["serpd_health"] = lambda _s=shape: _s
            got, detail = serpd_usable()
            c.check(f"health_verdict_{why.replace(' ', '_')}", got is want_ok,
                    f"got ok={got} ({detail})")
    finally:
        globals()["serpd_health"] = real

    dep = check_deps()
    c.check("dependency_check_returns_a_per_dep_verdict",
            isinstance(dep, dict) and bool(dep) and all(isinstance(v, bool) for v in dep.values()),
            str(dep)[:160])
    c.check("dependency_check_names_the_daemons_own_deps",
            {"websockets", "chrome"} <= set(dep), str(sorted(dep)))
    return c.verdict(note="nothing was repaired; this proves the READERS. Run "
                          "`seodoctor.py` itself for the repair pass.")

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--control", action="store_true",
                   help="prove the preflight's own readers discriminate; repairs nothing")
    p.add_argument("--check", action="store_true", help="report only, repair nothing")
    p.add_argument("--hard", action="store_true", help="force-restart the daemon even if healthy")
    p.add_argument("--root", default=".", help="repo root holding .seo/")
    p.add_argument("--providers", action="store_true",
                   help="LIVE-probe every data source (network, ~30s) instead of just "
                        "reporting which credentials exist")
    a = p.parse_args()

    if a.control:
        out = run_control()
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if out.get("ok") else 1

    report = {
        "ok": True,
        "deps": check_deps(),
        "project": check_project(Path(a.root).resolve()),
        "providers": check_providers(live=a.providers),
        "serpd": ensure_serpd(hard=a.hard, repair=not a.check),
    }
    hard_fail = []
    if not report["deps"].get("websockets"):
        hard_fail.append("websockets missing - serpd cannot run (pip install websockets)")
    if not report["deps"].get("chrome"):
        hard_fail.append("no chrome binary - serpd and the browser provider cannot run")
    if report["serpd"]["state"] in ("repair_failed", "unhealthy"):
        hard_fail.append(f"serpd not usable: {report['serpd'].get('detail')}")

    report["ok"] = not hard_fail
    report["blocking"] = hard_fail
    # A daemon this script could not fix is NOT the end of a run: ddg and the
    # browser handoff still work, and the quality bar now forbids stopping a run
    # early. Say so explicitly so nobody reads a red preflight as permission.
    report["note"] = ("preflight clean" if not hard_fail else
                      "serpd unavailable - fall back to `serp.py --provider ddg` and the "
                      "`--provider browser` handoff. This is NOT grounds to end a run short.")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
