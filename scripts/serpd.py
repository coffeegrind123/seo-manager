#!/usr/bin/env python3
"""SERP daemon - one curl in, scored JSON out. No DOM in your context.

WHY THIS EXISTS
    A research run spends up to 25 SERP checks. Driving each one through the
    browser MCP costs a `navigate`, a readiness `execute_js`, an extract
    `execute_js` and a scoring round-trip - four tool calls whose responses
    carry raw page text. Twenty-five of those is most of a context window
    spent on plumbing.

    This puts a long-lived headed Chrome behind a tiny HTTP server. One curl
    returns the finished, relevance-guarded, weakness-scored result:

        curl -s 'http://127.0.0.1:8791/serp?q=self+hosted+rank+tracker&depth=20'

    Ported from the veikkaus-browser skill's browser-api-server.py, whose bug
    list was paid for in real debugging rounds. The patterns kept here are the
    ones that cost someone a night: bounded tab locks, health-check-on-use,
    real navigation readiness instead of sleeps, adopt-don't-recreate, a floor
    on tab count, and patient startup.

RELATIONSHIP TO serp.py
    serp.py stays the interface and the single source of truth: the readiness
    script, the extraction script, the relevance guard and the scoring all
    live there and are IMPORTED here. This daemon is a faster transport for
    the browser provider, never a second implementation of it.

    Anything this returns is identical in shape to `serp.py --score-json`.

CHROME
    Its own dedicated headed Chrome on its own profile and CDP port, so it
    never fights the browser MCP or another session for tabs. Launched
    directly rather than through zendriver: a plain `google-chrome
    --remote-debugging-port=N` does bring its port up, it just takes ~15s
    under load, and zendriver's connect retry has been observed giving up
    before the port binds.

USAGE
    serpd.py --start                 # detach and run (idempotent)
    serpd.py --foreground            # run in this terminal (debugging)
    serpd.py --stop
    serpd.py --status

    GET  /health
    GET  /serp?q=...&depth=20&target=example.com&view=full|verdict
    POST /batch     {"queries": [...], "depth": 20, "view": "verdict"|"full"}
    POST /reset     drop and rebuild the tab pool (unwedge)
    POST /shutdown

    /batch defaults to compact verdicts - on a 25-query run that is 1.4KB
    instead of 164KB, which is the whole reason this daemon exists.

    A client that disconnects mid-request does NOT cancel the work: the worker
    thread runs on and keeps its tab lock. If later calls hang, POST /reset.

Needs the `websockets` package (present here: 16.1). Everything else stdlib.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from serp import (  # noqa: E402  - the single source of truth for all four
    BROWSER_EXTRACT, BROWSER_READY, score, shape_ok, verify_relevance,
)

try:
    from websockets.sync.client import connect as ws_connect
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(f"FATAL: needs the `websockets` package (pip install websockets): {exc}\n")
    raise SystemExit(2)

API_PORT = int(os.environ.get("SERPD_PORT", "8791"))
CDP_PORTFILE = "/tmp/seo-serpd-cdp.port"
PIDFILE = "/tmp/seo-serpd.pid"
LOGFILE = "/tmp/seo-serpd.log"
PROFILE = os.environ.get("SERPD_PROFILE", "/tmp/seo-serpd-profile")
UBLOCK = "/opt/zendriver-mcp/extensions/ublock"
TAB_POOL = int(os.environ.get("SERPD_TABS", "3"))

CDP_PORT: int | None = None
STARTED_AT = time.time()


def log(msg: str):
    sys.stderr.write(f"[serpd] {msg}\n")
    sys.stderr.flush()


# --------------------------------------------------------------- chrome


def _chrome_binary() -> str:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    raise RuntimeError("no chrome binary found")


def _cdp_alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            return bool(r.read())
    except Exception:
        return False


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace").replace("\0", " ")
    except OSError:
        return ""


def _chrome_pids_on_profile() -> list[int]:
    """PIDs of BROWSER-process Chromes running against our profile dir.

    A /proc scan, deliberately not `pgrep -f`/`pkill -f`: those match the
    scanning shell's OWN command line whenever the pattern appears in it, so a
    cleanup one-liner kills the shell that is running it. Measured 2026-08-01 -
    `pkill -f seo-serpd-profile` took down two live sessions before the cause
    was obvious, and the same self-match is why `pgrep -f <script>` can never be
    used to poll for that script.

    `--type=` filters out the zygote/gpu/renderer children, which carry the same
    --user-data-dir but are not the process holding the singleton.
    """
    needle = f"--user-data-dir={PROFILE}"
    out = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        cmd = _cmdline(int(entry))
        if needle in cmd and "--type=" not in cmd:
            out.append(int(entry))
    return out


def _reconcile_orphan_chrome(want_proxy: bool) -> int | None:
    """Adopt a live Chrome already sitting on our profile, or clear it out.

    THE BUG THIS EXISTS FOR (measured 2026-08-01, cost a whole research run):
    the portfile is written only AFTER a successful CDP bind, so any crash
    between launch and bind leaves a live Chrome on the profile whose port
    nothing recorded. Chrome's singleton then hands every subsequent launch off
    to that instance and exits immediately, so the freshly-chosen port is never
    bound and ensure_chrome times out after 60s - and does so again on every
    retry, forever, because nothing in the old code ever looked for a running
    Chrome it had not recorded. It presents as "the daemon will not start",
    which sends you hunting in the daemon rather than at the orphan.

    Unlinking the Singleton* files (below) was the previous attempt and cannot
    work: it removes the LOCK while leaving the PROCESS holding the profile.

    Read the proxy state off the live process's own argv rather than the
    portfile stamp - the stamp is exactly the thing that is missing here, and
    argv cannot disagree with how the browser was actually launched.
    """
    for pid in _chrome_pids_on_profile():
        cmd = _cmdline(pid)
        m = re.search(r"--remote-debugging-port=(\d+)", cmd)
        port = int(m.group(1)) if m else None
        had_proxy = "--proxy-server=" in cmd
        if port and had_proxy == want_proxy and _cdp_alive(port):
            log(f"adopted orphan chrome pid {pid} on CDP {port} (proxied={had_proxy}) "
                "- portfile was stale or missing")
            Path(CDP_PORTFILE).write_text(json.dumps({"port": port, "proxied": had_proxy}))
            return port
        log(f"killing orphan chrome pid {pid} on our profile "
            f"(port={port} cdp_alive={bool(port) and _cdp_alive(port)} "
            f"proxied={had_proxy} want_proxy={want_proxy}) - it would hijack the next launch")
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return None



def _proxy_file_url() -> str:
    """Read SEO_PROXY_URL out of ~/.seo-proxy.

    serp.py has always honoured this file; serpd.py did not, and read the
    environment alone. That split is worse than either choice on its own: you
    configure the file, `serp.py --provider ddg` correctly goes out on the
    residential exit, and the DAEMON - the fast path that does the actual
    volume - silently keeps using the datacenter IP. Nothing reports the
    difference, and Google starts serving /sorry to the half you thought was
    protected. Same rule as adoption: never let the caller believe they are
    proxied when they are not.
    """
    try:
        for line in open(os.path.expanduser("~/.seo-proxy"), encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() in ("SEO_PROXY_URL", "SERPD_PROXY"):
                return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def resolve_proxy_url() -> str:
    """Env first, then ~/.seo-proxy. One resolver, used everywhere."""
    return (os.environ.get("SERPD_PROXY")
            or os.environ.get("SEO_PROXY_URL")
            or _proxy_file_url()).strip()


def ensure_chrome() -> int:
    """Start our own headed Chrome, or adopt the one we started earlier.

    Headed is not a preference. In --headless=new a Turnstile challenge is
    unsolvable, while the same page headed often loads with no challenge at
    all. There is an Xvfb display in this environment, so headed is free.
    """
    global CDP_PORT
    want_proxy = bool(resolve_proxy_url())
    # Adoption must not silently change what the caller asked for. A Chrome
    # launched WITHOUT a proxy cannot be reused when a proxy is now configured -
    # you would believe you were on a residential exit while every request went
    # out on the datacenter IP. The stamp records how the live browser was
    # actually launched; a mismatch means relaunch, not adopt.
    if os.path.exists(CDP_PORTFILE):
        try:
            stamp = json.loads(Path(CDP_PORTFILE).read_text())
            port, had_proxy = int(stamp["port"]), bool(stamp.get("proxied"))
            if _cdp_alive(port):
                if had_proxy == want_proxy:
                    CDP_PORT = port
                    log(f"adopted existing chrome on CDP {port} (proxied={had_proxy})")
                    return port
                log(f"existing chrome on CDP {port} has proxied={had_proxy} but proxied={want_proxy} "
                    "was asked for - relaunching instead of lying about the egress")
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/json/close", timeout=3)
                except Exception:
                    pass
                subprocess.run(["pkill", "-f", f"--remote-debugging-port={port}"], check=False)
                time.sleep(2)
        except (ValueError, OSError, KeyError, json.JSONDecodeError):
            pass

    # Any Chrome still on this profile that the portfile did not account for is
    # either reusable or fatal to the next launch - never harmless. Settle it
    # BEFORE clearing the locks, because clearing them while the process lives
    # is precisely the no-op that hid this bug.
    adopted = _reconcile_orphan_chrome(want_proxy)
    if adopted:
        CDP_PORT = adopted
        return adopted

    # A stale profile lock makes Chrome exit instantly with no useful error.
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.unlink(os.path.join(PROFILE, lock))
        except OSError:
            pass

    port = _free_port()
    args = [
        _chrome_binary(),
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={PROFILE}",
        "--window-size=1440,900",
        "--no-first-run", "--no-default-browser-check", "--no-service-autorun",
        "--disable-background-networking", "--disable-component-update",
        "--disable-breakpad", "--disable-infobars", "--password-store=basic",
        "--homepage=about:blank",
        "--no-sandbox",          # required as root in a container
        # /dev/shm is 64MB in this container (measured). Chrome's renderers use
        # it for shared memory, and three tabs rendering Google exhaust it - the
        # renderers die and take the browser process with them, surfacing as
        # ConnectionRefusedError on the CDP port with no crash line in the log.
        # This flag moves that allocation to /tmp and fixes it outright.
        #
        # It is NOT a fingerprinting signal - unlike --disable-gpu, which is in
        # the same "low memory" preset and DOES matter (software WebGL is
        # exactly what fingerprinters read). Take this one, leave that one.
        "--disable-dev-shm-usage",
        # NOTE: deliberately NO --disable-gpu and NO --headless.
    ]
    if os.path.isdir(UBLOCK):
        args.append(f"--load-extension={UBLOCK}")
    proxy = resolve_proxy_url()
    if proxy:
        # Credentials get handled by a local forwarder (see above) because
        # Chrome's --proxy-server cannot carry them itself.
        args.append(f"--proxy-server={start_proxy_forwarder(proxy)}")
        # A proxied browser must not leak the real IP over WebRTC.
        args += ["--webrtc-ip-handling-policy=disable_non_proxied_udp",
                 "--force-webrtc-ip-handling-policy"]

    log(f"launching chrome on CDP {port} (profile {PROFILE})")
    with open(LOGFILE, "ab") as out:
        subprocess.Popen(args, stdout=out, stderr=out, start_new_session=True)

    # Patient: a cold start under load genuinely takes 15s+, and giving up
    # early is how you get a false "chrome failed" while it is still coming up.
    for _ in range(120):
        if _cdp_alive(port):
            Path(CDP_PORTFILE).write_text(json.dumps({"port": port, "proxied": bool(proxy)}))
            CDP_PORT = port
            log(f"chrome up on CDP {port}")
            return port
        time.sleep(0.5)
    survivors = _chrome_pids_on_profile()
    raise RuntimeError(
        f"chrome did not bind CDP port {port} within 60s - see {LOGFILE}. "
        + (f"NOTE: {len(survivors)} chrome process(es) are still on {PROFILE} (pids {survivors}) "
           "after reconciliation - a launch handed off to one of them instead of binding. "
           f"Clear it with: python3 {Path(__file__).name} --stop --force"
           if survivors else
           "No chrome is on the profile, so this is a genuine launch failure - "
           "check the log for a crash line, and confirm a display is available (headed chrome).")
    )



# ------------------------------------------------------- proxy forwarder


def start_proxy_forwarder(upstream: str) -> str:
    """Turn an AUTHENTICATED upstream proxy into one Chrome can actually use.

    Chrome's --proxy-server accepts no inline credentials, and it has no
    headless way to answer a proxy auth prompt. The standard fix is a local
    unauthenticated CONNECT forwarder that injects Proxy-Authorization on the
    way out - that is all this is.

    Needed because Google rate-limits a datacenter IP after a burst of SERPs
    (measured: consecutive queries returning /sorry after ~45 checks), and a
    residential exit is the thing that actually clears it.

    Returns the local "127.0.0.1:PORT" to hand to --proxy-server.
    """
    import base64 as _b64
    import socket as _socket

    parsed = urllib.parse.urlparse(upstream)
    if not parsed.hostname or not parsed.port:
        raise RuntimeError(f"SERPD_PROXY must be http://[user:pass@]host:port, got {parsed.scheme}://…")
    up_host, up_port = parsed.hostname, parsed.port
    auth_hdr = b""
    if parsed.username:
        raw = f"{urllib.parse.unquote(parsed.username)}:{urllib.parse.unquote(parsed.password or '')}"
        auth_hdr = b"Proxy-Authorization: Basic " + _b64.b64encode(raw.encode()) + b"\r\n"

    listener = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    listener.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)
    local_port = listener.getsockname()[1]

    def pipe(a, b):
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except OSError:
            pass
        finally:
            for sck in (a, b):
                try:
                    sck.shutdown(_socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sck.close()
                except OSError:
                    pass

    def handle(client):
        up = None
        try:
            client.settimeout(30)
            req = b""
            while b"\r\n\r\n" not in req:
                chunk = client.recv(4096)
                if not chunk:
                    return
                req += chunk
                if len(req) > 32768:
                    return
            line = req.split(b"\r\n", 1)[0].decode("latin-1")
            parts = line.split()
            if len(parts) < 2:
                return
            method, target = parts[0], parts[1]
            up = _socket.create_connection((up_host, up_port), timeout=30)
            if method.upper() == "CONNECT":
                up.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n".encode()
                           + auth_hdr + b"\r\n")
                resp = b""
                while b"\r\n\r\n" not in resp:
                    chunk = up.recv(4096)
                    if not chunk:
                        return
                    resp += chunk
                client.sendall(resp)
                if b" 200" not in resp.split(b"\r\n", 1)[0]:
                    return
            else:
                # Plain HTTP: forward the request with auth spliced in.
                head, _, rest = req.partition(b"\r\n")
                up.sendall(head + b"\r\n" + auth_hdr + rest)
            t = threading.Thread(target=pipe, args=(up, client), daemon=True)
            t.start()
            pipe(client, up)
        except OSError:
            pass
        finally:
            for sck in (client, up):
                if sck:
                    try:
                        sck.close()
                    except OSError:
                        pass

    def accept_loop():
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(client,), daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()
    log(f"proxy forwarder on 127.0.0.1:{local_port} -> {up_host}:{up_port} "
        f"({'authenticated' if auth_hdr else 'no auth'})")
    return f"127.0.0.1:{local_port}"


# ------------------------------------------------------------------ CDP


_WS_URL: str | None = None
_WS_URL_LOCK = threading.Lock()


def browser_ws_url() -> str:
    """Discover the browser websocket ONCE and cache it.

    /json/version is the only reliable DevTools HTTP call, and it is still
    single-threaded: creating a pool of tabs in a burst hammered it hard enough
    that it stalled and tab creation failed with "cannot read /json/version"
    while Chrome itself was perfectly healthy (measured - 4 of 6 batch items
    died this way). The URL is fixed for the life of the process, so there is
    no reason to ask more than once.
    """
    global _WS_URL
    with _WS_URL_LOCK:
        if _WS_URL:
            return _WS_URL
    last = None
    for _ in range(10):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=4) as r:
                data = r.read()
            if data:
                url = json.loads(data)["webSocketDebuggerUrl"]
                with _WS_URL_LOCK:
                    _WS_URL = url
                return url
        except Exception as exc:
            last = exc
        time.sleep(0.4)
    raise RuntimeError(f"cannot read /json/version on CDP port {CDP_PORT}: {last}")


class Tab:
    """A page target on its own browser-level websocket via a flatten session.

    One socket per tab gives true parallelism for the batch fan-out and keeps a
    failure contained to a single tab. `/json/list` and `/json/new` are
    deliberately unused - under contention from other CDP clients they
    intermittently hang or return empty bodies. `/json/version` is the one
    reliable HTTP call and is used only to discover the browser websocket.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.mid = 0
        # ping_interval=None is load-bearing. The websockets library sends
        # keepalive pings every 20s and CLOSES the connection on a missed pong.
        # A tab waiting behind the batch stagger sits idle long enough to miss
        # one, and the symptom is a mid-flow "ConnectionClosedError: no close
        # frame received or sent" that looks like Chrome dying. CDP needs no
        # application-level keepalive - the TCP connection is the liveness
        # signal, and get_tab()'s health check covers a genuinely dead tab.
        self.ws = ws_connect(browser_ws_url(), max_size=None, open_timeout=10,
                             ping_interval=None, close_timeout=5)
        self.target_id = self._browser("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.session_id = self._browser(
            "Target.attachToTarget", {"targetId": self.target_id, "flatten": True})["sessionId"]

    def _xfer(self, method, params, session_id, timeout):
        with self.lock:
            self.mid += 1
            mid = self.mid
            msg = {"id": mid, "method": method, "params": params or {}}
            if session_id:
                msg["sessionId"] = session_id
            self.ws.send(json.dumps(msg))
            deadline = time.time() + timeout
            while True:
                rem = deadline - time.time()
                if rem <= 0:
                    raise TimeoutError(f"CDP timeout: {method}")
                resp = json.loads(self.ws.recv(timeout=rem))
                if resp.get("id") == mid:
                    if "error" in resp:
                        err = resp["error"]
                        raise RuntimeError(f"CDP error in {method}: {err.get('message', err)}")
                    return resp.get("result", {})
                # events and other-session replies: ignore

    def _browser(self, method, params=None, timeout=20):
        return self._xfer(method, params, None, timeout)

    def send(self, method, params=None, timeout=30):
        return self._xfer(method, params, self.session_id, timeout)

    def close(self):
        try:
            self._browser("Target.closeTarget", {"targetId": self.target_id}, timeout=5)
        except Exception:
            pass
        try:
            self.ws.close()
        except Exception:
            pass


_TABS: dict[str, Tab] = {}
_REG_LOCK = threading.Lock()
_OP_LOCKS: dict[str, threading.Lock] = {}
# 75s, not 180: a wedge has to surface FASTER than a caller's own timeout, or
# the caller gives up first and the operator sees an unexplained hang with a
# healthy /health behind it. Measured the hard way - a killed batch left its
# worker threads running and holding tab locks, and every later /serp blocked
# silently because BaseHTTPRequestHandler only logs on RESPONSE, so the wedged
# requests never even appeared in the log.
OP_LOCK_TIMEOUT = 75


class BoundedLock:
    """Refuses to wait forever for a tab lock.

    Tab locks are held for a whole flow. When ONE flow wedges it would
    otherwise hold its lock forever and every later request queues behind it
    indefinitely - and the visible symptom is a completely unrelated timeout.
    """

    def __init__(self, lock: threading.Lock, name: str, timeout: int = OP_LOCK_TIMEOUT):
        self.lock, self.name, self.timeout = lock, name, timeout
        self.held = False

    def __enter__(self):
        if not self.lock.acquire(timeout=self.timeout):
            raise RuntimeError(
                f"tab {self.name!r} busy for over {self.timeout}s - a previous request is wedged. "
                f"Restart with: serpd.py --stop && serpd.py --start"
            )
        self.held = True
        return self

    def __exit__(self, *exc):
        if self.held:
            self.lock.release()


def get_tab(name: str) -> Tab:
    """Health-check on use: a cached tab that has died is replaced, not used."""
    with _REG_LOCK:
        tab = _TABS.get(name)
    if tab is not None:
        try:
            tab.send("Runtime.evaluate", {"expression": "1", "returnByValue": True}, timeout=5)
            return tab
        except Exception:
            try:
                tab.close()
            except Exception:
                pass
            with _REG_LOCK:
                _TABS.pop(name, None)
    tab = Tab()
    with _REG_LOCK:
        _TABS[name] = tab
    return tab


def op_lock(name: str) -> threading.Lock:
    with _REG_LOCK:
        return _OP_LOCKS.setdefault(name, threading.Lock())


def evaluate(tab: Tab, expression: str, timeout: int = 15):
    res = tab.send("Runtime.evaluate",
                   {"expression": expression, "returnByValue": True, "awaitPromise": True},
                   timeout=timeout)
    if res.get("exceptionDetails"):
        raise RuntimeError(f"JS exception: {res['exceptionDetails'].get('text')}")
    return res.get("result", {}).get("value")


def _host_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return ""


def navigate(tab: Tab, url: str, load_timeout: int = 25):
    """Navigate and wait for REAL readiness instead of a blind sleep.

    The document is committed when all hold: it actually SWAPPED (a sentinel on
    the outgoing doc is gone, OR location.href changed - the OR makes it correct
    for a fresh about:blank tab, a same-host different page, and a same-URL
    reload alike), we are on the target host (survives path redirects), and
    readyState is complete.
    """
    host = _host_of(url)
    try:
        before = evaluate(tab, "location.href", timeout=5) or ""
    except Exception:
        before = ""
    try:
        evaluate(tab, "window.__navSent=1", timeout=5)
    except Exception:
        pass
    tab.send("Page.navigate", {"url": url}, timeout=20)
    deadline = time.time() + load_timeout
    while time.time() < deadline:
        try:
            st = evaluate(
                tab,
                "(typeof window.__navSent==='undefined')+'\\t'+location.href+'\\t'+document.readyState",
                timeout=5)
            if isinstance(st, str) and st.count("\t") >= 2:
                sent_gone, href, rs = st.split("\t", 2)
                swapped = (sent_gone == "true") or (href != before)
                if swapped and (not host or host in href) and rs == "complete":
                    return
        except Exception:
            pass
        time.sleep(0.25)


# ------------------------------------------------------------- the flow


def serp_one(tab: Tab, query: str, depth: int, target: str | None,
             gl: str = "us", hl: str = "en") -> dict:
    """Navigate -> readiness ladder -> extract -> guard -> score."""
    url = "https://www.google.com/search?" + urllib.parse.urlencode(
        {"q": query, "num": min(depth, 30), "hl": hl, "gl": gl, "pws": "0"})
    navigate(tab, url)

    # The readiness ladder, straight out of the browser recipe: poll, then ONE
    # reload, then poll again before declaring anything blocked. Giving up early
    # here is a documented way to manufacture false "blocked" verdicts.
    verdict, reloaded = None, False
    for attempt in range(7):
        # 30s, not 15: with the whole pool rendering Google at once a single
        # Runtime.evaluate genuinely exceeded 15s and surfaced as a TimeoutError
        # on a query that succeeded instantly when run alone. Slow under load is
        # not an error condition.
        raw = evaluate(tab, BROWSER_READY, timeout=30)
        try:
            verdict = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            verdict = {"status": "short", "note": "readiness script returned no JSON"}
        status = (verdict or {}).get("status")
        if status == "ready":
            break
        if status == "hard-fail":
            # The fingerprint was rejected. Clicking cannot fix it, so stop.
            return {"ok": False, "query": query, "error": "hard-fail",
                    "readiness": verdict, "retryable": False,
                    "hint": "fingerprint rejected - rotate the profile "
                            "(rm -rf /tmp/seo-serpd-profile) or use a different provider"}
        if status == "rate-limited":
            # Transient. Back off inside this call rather than burning the
            # caller's retry: a burst trips it, a pause clears it.
            if attempt < 4:
                time.sleep(6 * (attempt + 1))
                navigate(tab, url)
                continue
            return {"ok": False, "query": query, "error": "rate-limited",
                    "readiness": verdict, "retryable": True,
                    "hint": "Google rate-limited this IP after a burst. Lower SERPD_TABS, "
                            "raise the batch stagger, or retry in a minute."}
        if attempt == 3 and not reloaded:
            tab.send("Page.reload", {}, timeout=20)
            reloaded = True
            time.sleep(3)
            continue
        time.sleep(4)
    if (verdict or {}).get("status") != "ready":
        return {"ok": False, "query": query, "error": "never became ready",
                "readiness": verdict, "reloaded": reloaded}

    raw = evaluate(tab, BROWSER_EXTRACT, timeout=30)
    data = json.loads(raw) if isinstance(raw, str) else raw
    results = data.get("results", [])

    if not shape_ok(results):
        return {"ok": False, "query": query,
                "error": "no parseable results - a failed read, NOT an empty page 1",
                "readiness": verdict}
    rel = verify_relevance(query, results)
    payload = {
        "ok": rel["pass"],
        "query": query,
        "provider": "serpd-google",
        "ai_overview": data.get("ai_overview"),
        "people_also_ask": data.get("people_also_ask", []),
        "results": results,
        "relevance": rel,
        "reloaded": reloaded,
    }
    if not rel["pass"]:
        payload["error"] = "wrong-query results - refusing to score"
        payload["observed_titles"] = [r.get("title") for r in results[:10]]
        return payload
    payload["scoring"] = score(results, target)
    return payload


def to_verdict(r: dict) -> dict:
    """The decision data only - what the quality bar actually reads.

    Measured on a 25-query batch: the full payload is 164KB, these verdicts are
    1.4KB. Pulling 164KB of titles and snippets into context to decide 25
    authority counts defeats the entire point of the daemon, so batch defaults
    to this and you ask for `view=full` when you genuinely need the results.
    """
    if not r.get("ok"):
        return {k: r.get(k) for k in ("query", "ok", "error", "retryable", "relevance") if k in r}
    s_, rel = r.get("scoring", {}), r.get("relevance", {})
    return {
        "query": r.get("query"),
        "ok": True,
        "authority_candidate_count": s_.get("authority_candidate_count"),
        "weakness_count": s_.get("weakness_count"),
        "strong_serp_weakness": s_.get("strong_serp_weakness"),
        "weakness_signals": s_.get("weakness_signals"),
        "distinct_domains_top10": s_.get("distinct_domains_top10"),
        "target_position": s_.get("target_position"),
        "ai_overview": bool((r.get("ai_overview") or {}).get("present")),
        "relevance_coverage": rel.get("coverage"),
        "results_seen": s_.get("results_seen"),
        # Enough to sanity-check the read by eye without carrying the whole SERP.
        "top3": [{"position": x.get("position"), "domain": x.get("domain"),
                  "title": (x.get("title") or "")[:70]} for x in (r.get("results") or [])[:3]],
    }


def serp_batch(queries: list[str], depth: int, target: str | None,
               gl: str = "us", hl: str = "en") -> list[dict]:
    """Fan out across the tab pool. Each item is independent; one failing does
    not take the batch down."""
    out: list[dict | None] = [None] * len(queries)
    sem = threading.Semaphore(TAB_POOL)
    # Google rate-limits a simultaneous burst from one IP (measured: 2 of 6
    # parallel queries got /sorry; the same queries alone were fine). Staggering
    # the starts costs a few seconds and avoids tripping it at all.
    stagger = float(os.environ.get("SERPD_STAGGER", "1.5"))

    def work(i: int, q: str):
        name = f"serp-{i % TAB_POOL + 1}"
        time.sleep(stagger * i)
        with sem:
            last = None
            # A dropped websocket is transient and self-healing: get_tab's
            # health check rebuilds the tab, so retry once before giving up.
            for attempt in range(2):
                try:
                    with BoundedLock(op_lock(name), name):
                        out[i] = serp_one(get_tab(name), q, depth, target, gl, hl)
                    return
                except Exception as exc:
                    last = exc
                    time.sleep(1.5 * (attempt + 1))
            out[i] = {"ok": False, "query": q,
                      "error": f"{type(last).__name__}: {last}", "attempts": 2}

    threads = [threading.Thread(target=work, args=(i, q), daemon=True)
               for i, q in enumerate(queries)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=OP_LOCK_TIMEOUT + 60)
    return [o or {"ok": False, "error": "worker did not finish"} for o in out]


# ------------------------------------------------------------------ HTTP


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter than the default
        log(fmt % args)

    def _send(self, obj, code=200):
        body = json.dumps(obj, indent=2, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        one = lambda k, d=None: (qs.get(k) or [d])[0]  # noqa: E731

        if parsed.path == "/health":
            with _REG_LOCK:
                tabs = sorted(_TABS)
            self._send({"ok": True, "cdpPort": CDP_PORT, "apiPort": API_PORT,
                        "uptime_s": round(time.time() - STARTED_AT, 1),
                        "tabs": tabs, "tab_pool": TAB_POOL, "profile": PROFILE,
                        "chrome_alive": _cdp_alive(CDP_PORT) if CDP_PORT else False})
            return

        if parsed.path == "/serp":
            q = one("q")
            if not q:
                self._send({"ok": False, "error": "missing ?q="}, 400)
                return
            name = "serp-1"
            try:
                with BoundedLock(op_lock(name), name):
                    res = serp_one(get_tab(name), q, int(one("depth", "20")),
                                   one("target"), one("gl", "us"), one("hl", "en"))
            except Exception as exc:
                self._send({"ok": False, "query": q, "error": f"{type(exc).__name__}: {exc}"}, 500)
                return
            if one("view", "full") == "verdict":
                res = to_verdict(res)
            self._send(res, 200 if res.get("ok") else 502)
            return

        self._send({"ok": False, "error": f"unknown path {parsed.path}"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send({"ok": False, "error": "body must be JSON"}, 400)
            return

        if parsed.path == "/reset":
            # Drop every tab and rebuild the pool. The escape hatch for the
            # abandoned-work wedge: a client that disconnects mid-batch does NOT
            # stop the worker threads, so its tabs can stay locked for minutes.
            # This is cheaper and far less disruptive than restarting Chrome.
            with _REG_LOCK:
                names = list(_TABS)
                for n in names:
                    try:
                        _TABS[n].close()
                    except Exception:
                        pass
                _TABS.clear()
                _OP_LOCKS.clear()
            warm_pool()
            with _REG_LOCK:
                tabs = sorted(_TABS)
            self._send({"ok": True, "reset": names, "tabs": tabs})
            return

        if parsed.path == "/shutdown":
            self._send({"ok": True, "stopping": True})
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
            return

        if parsed.path == "/batch":
            queries = body.get("queries") or []
            if not isinstance(queries, list) or not queries:
                self._send({"ok": False, "error": "body needs a non-empty 'queries' list"}, 400)
                return
            results = serp_batch(queries, int(body.get("depth", 20)), body.get("target"),
                                 body.get("gl", "us"), body.get("hl", "en"))
            ok = sum(1 for r in results if r.get("ok"))
            view = body.get("view", "verdict")   # batch defaults to compact
            payload = results if view == "full" else [to_verdict(r) for r in results]
            self._send({"ok": ok == len(results), "requested": len(queries),
                        "succeeded": ok, "failed": len(results) - ok,
                        "view": view, "results": payload,
                        "note": ("compact verdicts - pass \"view\":\"full\" for titles, "
                                 "URLs and snippets" if view != "full" else None)})
            return

        self._send({"ok": False, "error": f"unknown path {parsed.path}"}, 404)


def reap_orphan_tabs():
    """Close leftover about:blank targets - but NEVER drop to zero.

    Chrome exits when its last window closes, which is the real cause of
    'the browser dies on restart'.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=5) as r:
            targets = json.loads(r.read())
    except Exception:
        return
    pages = [t for t in targets if t.get("type") == "page"]
    blanks = [t for t in pages if t.get("url") in ("about:blank", "")]
    if len(pages) - len(blanks) < 1:
        blanks = blanks[1:]  # keep one alive
    for t in blanks:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/close/{t['id']}", timeout=5)
        except Exception:
            pass


def warm_pool():
    """Create the tab pool serially at startup.

    Tabs created on demand meant a batch's first moments were N threads all
    doing Target.createTarget at once. Serial pre-warm removes that burst, and
    get_tab()'s health check still replaces any tab that dies later.
    """
    for i in range(TAB_POOL):
        name = f"serp-{i + 1}"
        try:
            get_tab(name)
            log(f"warmed {name}")
        except Exception as exc:
            log(f"could not warm {name}: {exc}")


def serve():
    ensure_chrome()
    reap_orphan_tabs()
    warm_pool()
    srv = ThreadingHTTPServer(("127.0.0.1", API_PORT), Handler)
    Path(PIDFILE).write_text(str(os.getpid()))
    log(f"listening on http://127.0.0.1:{API_PORT} (chrome CDP {CDP_PORT})")
    try:
        srv.serve_forever()
    finally:
        for name in list(_TABS):
            try:
                _TABS[name].close()
            except Exception:
                pass


# ------------------------------------------------------------------ main


def api_alive() -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/health", timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--start", action="store_true", help="detach and run (idempotent)")
    g.add_argument("--foreground", action="store_true", help="run here (debugging)")
    g.add_argument("--stop", action="store_true")
    g.add_argument("--status", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="with --stop: also SIGKILL any chrome on the profile and clear the portfile")
    a = p.parse_args()

    if a.status:
        health = api_alive()
        # Report chrome-on-profile even when the API is down: "server dead but
        # chrome alive" is the wedged state, and it is invisible in a bare
        # health check - which is why it went undiagnosed for a whole run.
        orphans = [{"pid": pid,
                    "port": (lambda m: int(m.group(1)) if m else None)(
                        re.search(r"--remote-debugging-port=(\d+)", _cmdline(pid)))}
                   for pid in _chrome_pids_on_profile()]

        # Proxy state, read off the LIVE process argv rather than the config.
        # A config value says what was intended; argv says what is. The gap
        # between those two is exactly the failure this daemon already guards
        # against on adoption - believing you are on a residential exit while
        # every request leaves on the datacenter IP - and `--status` was silent
        # about it in both directions.
        configured = bool(resolve_proxy_url())
        live = [pid for pid in _chrome_pids_on_profile()
                if "--proxy-server=" in _cmdline(pid)]
        proxy_state = {
            "configured": configured,
            "source": ("env" if (os.environ.get("SERPD_PROXY") or os.environ.get("SEO_PROXY_URL"))
                       else "~/.seo-proxy" if configured else None),
            "chrome_launched_with_proxy": bool(live),
        }
        if configured and not live and _chrome_pids_on_profile():
            proxy_state["warning"] = ("a proxy IS configured but the running chrome was launched "
                                      "WITHOUT one - restart the daemon (--stop --force, then "
                                      "--start) or every SERP goes out on the datacenter IP")
        elif live and not configured:
            proxy_state["warning"] = ("chrome is proxied but nothing is configured now - it is "
                                      "running on an older config; restart to make them agree")

        if health:
            print(json.dumps({**health, "proxy": proxy_state,
                              "chrome_on_profile": orphans}, indent=2))
            return 0
        print(json.dumps({
            "ok": False,
            "error": "not running",
            "chrome_on_profile": orphans,
            "hint": ("a chrome is on the profile with no server - the next --start will adopt "
                     "or clear it automatically; --stop --force kills it now" if orphans else
                     "clean state - --start should come up in a couple of seconds"),
        }, indent=2))
        return 1

    if a.stop:
        health = api_alive()
        if health:
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{API_PORT}/shutdown",
                                             data=b"{}", method="POST")
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                pass
        # Chrome outlives the server on purpose (restarting it is expensive and
        # loses the warmed profile) - and that is SAFE now only because
        # ensure_chrome reconciles a leftover Chrome on the next start instead
        # of launching into it. --force kills it outright, which is the escape
        # hatch the ensure_chrome error message points at.
        killed = []
        if a.force:
            for pid in _chrome_pids_on_profile():
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
        for f in (PIDFILE,):
            try:
                os.unlink(f)
            except OSError:
                pass
        print(json.dumps({"ok": True, "stopped": bool(health), "chrome_killed": killed,
                          "note": ("chrome and the portfile were cleared" if a.force else
                                   "chrome left running on purpose - the next --start adopts or "
                                   "clears it. Use --stop --force to kill it now")}, indent=2))
        return 0

    if a.foreground:
        serve()
        return 0

    # --start: idempotent
    health = api_alive()
    if health:
        print(json.dumps({"ok": True, "already_running": True, **health}, indent=2))
        return 0
    with open(LOGFILE, "ab") as out:
        subprocess.Popen([sys.executable, str(HERE / "serpd.py"), "--foreground"],
                         stdout=out, stderr=out, start_new_session=True)
    for _ in range(180):  # chrome cold start is genuinely slow; be patient
        time.sleep(0.5)
        health = api_alive()
        if health:
            print(json.dumps({"ok": True, "started": True, **health}, indent=2))
            return 0
    print(json.dumps({"ok": False, "error": f"daemon did not come up - see {LOGFILE}"}, indent=2))
    return 1


if __name__ == "__main__":
    sys.exit(main())
