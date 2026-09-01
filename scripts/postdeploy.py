#!/usr/bin/env python3
"""After a deploy: gate on health, prove the markup, then tell the engines.

Every step here was already possible, already documented, and still routinely
never ran - or ran as a per-project shell script that reached into a DIFFERENT
skill for its Google half. This is that sequence, in one place, portable to any
site this skill manages.

  1. HEALTH GATE   `/`, `/sitemap.xml`, `/robots.txt` must all be 200 before
                   anything is announced. A deploy that swaps a release
                   directory can 404 the entire site for minutes, and a submit
                   inside that window points every engine at a dead site. It
                   has really happened: a sitemap submit landed in the window,
                   came back with errors, and the CDN cached the /robots.txt
                   404 for four hours afterwards.
  2. CONTRACT      `contract.py check` - did the deploy silently ship a
                   noindex, drop a schema block or rewrite a canonical? Run
                   BEFORE the submit, so a recrawl is never invited onto
                   markup that just regressed.
  3. INDEXNOW      Bing, Yandex, Seznam, Naver, DuckDuckGo-via-Bing. Free,
                   keyless, no Google.
  4. GOOGLE        `gsc.py sitemap-submit` - the only programmatic nudge
                   Google offers, and it asks for a FEED re-read rather than
                   for a URL to be indexed. There is no force-index API for
                   ordinary pages; anything claiming otherwise is describing
                   the JobPosting/BroadcastEvent Indexing API, which does not
                   apply and is against its terms here.
  5. RECEIPT       every run appends to `.seo/postdeploy.jsonl`.

Step 5 exists because of a question that could not be answered on 2026-09-01.
A deploy had rewritten titles across six pages, and "was IndexNow pinged for
it?" turned out to be UNANSWERABLE: Google records `lastSubmitted` so the
sitemap side was provable, Bing's URL-submission quota is a different channel
that says nothing about IndexNow, and IndexNow itself returns 200 and keeps no
history you can query. An action with no receipt cannot be audited, so it grows
a receipt here.

SUBMISSIONS ARE A DRY RUN UNTIL `--yes`. The checks always run.

    postdeploy.py --root . --base https://example.com          # check only
    postdeploy.py --root . --base https://example.com --yes    # and announce

Stdlib only.
"""

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = "seo-manager/1.0 (+postdeploy)"

# A deploy that is mid-swap fails these. They are cheap, and they are the three
# URLs whose absence is most expensive: the site itself, the feed every engine
# is about to be pointed at, and the file that governs whether it may crawl.
GATE_PATHS = ("/", "/sitemap.xml", "/robots.txt")


def fetch_status(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, None
    except urllib.error.HTTPError as ex:
        return ex.code, None
    except urllib.error.URLError as ex:
        return None, str(ex.reason)


def resolve_base(explicit, cfg):
    """`--base` wins, then an explicit config key, then the domain.

    Hoisted out of main() so a control can read the REAL precedence. A control
    that checks a copy of this logic proves only that the copy agrees with
    itself - the same defect `bing.py`'s `--days` refusal set had.
    """
    return (explicit or cfg.get("base") or cfg.get("site")
            or (f"https://{cfg['domain']}" if cfg.get("domain") else "")).rstrip("/")


def gate_verdict(gate: dict):
    """(healthy, offenders). Anything that is not exactly 200 blocks the announce."""
    bad = [p for p, v in gate.items() if v.get("status") != 200]
    return (not bad), bad


def run(argv, timeout=900):
    """Run a sibling script and parse its JSON. A non-JSON body is reported as
    itself rather than swallowed - a traceback that becomes `{}` reads as a
    clean pass."""
    p = subprocess.run([sys.executable, str(HERE / argv[0])] + argv[1:],
                       capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(p.stdout), p.returncode, None
    except json.JSONDecodeError:
        return None, p.returncode, (p.stdout or p.stderr or "")[-1500:]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="repo root holding .seo/")
    ap.add_argument("--base", help="site origin, e.g. https://example.com "
                                   "(default: `base` in .seo/config.json)")
    ap.add_argument("--property", help="GSC property (default: .seo/config.json)")
    ap.add_argument("--sitemap", help="absolute sitemap URL (default: <base>/sitemap.xml)")
    ap.add_argument("--contract-name", default="prod")
    ap.add_argument("--yes", action="store_true",
                    help="actually submit. Without it the checks run and nothing is announced")
    ap.add_argument("--skip-indexnow", action="store_true")
    ap.add_argument("--skip-google", action="store_true")
    ap.add_argument("--timeout", type=int, default=900)
    # Flag-style, like serp/serpd/seodoctor/rankcheck - this script has no
    # subparsers, and `controls.py audit` recognises exactly these two shapes.
    ap.add_argument("--control", action="store_true",
                    help="prove the health gate and the base resolution, offline")
    a = ap.parse_args()
    if a.control:
        run_control()

    root = Path(a.root).resolve()
    cfg = {}
    cfg_path = root / ".seo" / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cfg = {}
    # `domain` is what seostate.py writes into a fresh config, so accept it too -
    # otherwise every site this skill sets up needs a hand-added `base` key.
    base = resolve_base(a.base, cfg)
    if not base:
        print(json.dumps({"ok": False, "check": "postdeploy",
                          "error": "no --base and no `base` in .seo/config.json"}, indent=2))
        sys.exit(2)
    sitemap = a.sitemap or f"{base}/sitemap.xml"
    steps, problems = {}, []

    # ---- 1. health gate ---------------------------------------------------
    gate = {}
    for path in GATE_PATHS:
        code, err = fetch_status(base + path)
        gate[path] = {"status": code, "error": err}
    healthy, offenders = gate_verdict(gate)
    steps["health"] = {"ok": healthy, "urls": gate,
                       "means": ("all three must be 200 BEFORE anything is announced. "
                                 "A release swap can 404 the whole site for minutes.")}
    if not healthy:
        bad = offenders
        out = {"ok": False, "check": "postdeploy", "base": base, "state": "aborted",
               "aborted_at": "health",
               "reason": f"not announcing: {bad} did not return 200",
               "what_to_do": ("if the deploy is still running, wait and re-run. If the origin "
                              "is 200 and the public URL is not, the CDN has cached the error "
                              "- purge it, because a query string cannot evict a cached 404."),
               "steps": steps}
        _receipt(root, out)
        print(json.dumps(out, indent=2))
        sys.exit(2)

    # ---- 2. contract ------------------------------------------------------
    doc, rc, raw = run(["contract.py", "--state-dir", str(root / ".seo" / "contract"),
                        "check", "--name", a.contract_name], timeout=a.timeout)
    if doc is None:
        steps["contract"] = {"ok": False, "state": "unreadable", "output": raw, "exit": rc}
        problems.append("contract check did not return JSON")
    else:
        warnings = [f for f in doc.get("still_open") or []
                    if f.get("severity") in ("warning", "error")]
        steps["contract"] = {"ok": doc.get("ok", False), "verdict": doc.get("verdict"),
                             "urls": doc.get("urls"), "counts": doc.get("counts"),
                             "warnings": warnings[:20],
                             "means": ("info-level `content_changed` on live pages is expected. "
                                       "A warning is an intentional markup change or a "
                                       "regression, and only you can say which - read it "
                                       "before re-baselining, never re-baseline to clear it.")}
        if warnings:
            problems.append(f"{len(warnings)} contract warning(s) open")

    # ---- 3. IndexNow ------------------------------------------------------
    if a.skip_indexnow:
        steps["indexnow"] = {"ok": True, "state": "skipped"}
    else:
        # SITEMAP, not --pending. `--pending` reads the content queue, which on
        # a generated site is empty by construction: measured on a 5,388-URL
        # silo it resolved to ZERO and reported ok, i.e. a post-deploy step that
        # announces nothing while looking like it worked. The sitemap is the
        # only complete statement of what the site publishes.
        argv = ["indexnow.py", "--root", str(root), "ping", "--sitemap", sitemap]
        if not a.yes:
            argv.append("--dry-run")
        doc, rc, raw = run(argv, timeout=a.timeout)
        steps["indexnow"] = ({"ok": rc == 0, "state": "unreadable", "output": raw, "exit": rc}
                             if doc is None else
                             {"ok": doc.get("ok", rc == 0),
                              "state": "submitted" if a.yes else "dry_run",
                              "result": doc,
                              "source": sitemap,
                              "submits": ("every URL in the sitemap, not only what changed - a "
                                          "generated build cannot say which pages moved, and "
                                          "IndexNow has no cost per URL worth rationing"),
                              "reaches": "Bing, Yandex, Seznam, Naver, DuckDuckGo (via Bing)",
                              "does_not_reach": "Google - it has never joined IndexNow"})
        if not steps["indexnow"]["ok"]:
            problems.append("IndexNow ping failed")

    # ---- 4. Google --------------------------------------------------------
    if a.skip_google:
        steps["google"] = {"ok": True, "state": "skipped"}
    else:
        argv = ["gsc.py", "--root", str(root)]
        if a.property or cfg.get("gsc_property"):
            argv += ["--property", a.property or cfg["gsc_property"]]
        argv += ["sitemap-submit", "--sitemap", sitemap]
        if a.yes:
            argv.append("--yes")
        doc, rc, raw = run(argv, timeout=a.timeout)
        steps["google"] = ({"ok": False, "state": "unreadable", "output": raw, "exit": rc}
                           if doc is None else
                           {"ok": doc.get("ok", False),
                            "state": doc.get("state", "submitted" if a.yes else "dry_run"),
                            "result": doc,
                            "no_force_index": ("there is no API that indexes an ordinary page. "
                                               "This asks Google to re-read the FEED. "
                                               "'Request indexing' is a human clicking a "
                                               "button, and 'Discovered - currently not "
                                               "indexed' is normal on a large silo.")})
        if not steps["google"]["ok"]:
            problems.append("Google sitemap submit failed")

    out = {"ok": not problems, "check": "postdeploy", "base": base, "sitemap": sitemap,
           "submitted": bool(a.yes), "steps": steps, "problems": problems,
           "reading": ("`submitted: false` means the checks ran and NOTHING was announced. "
                       "Re-run with --yes once the contract findings are understood.")}
    _receipt(root, out)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    sys.exit(0 if not problems else 1)


def run_control():
    sys.path.insert(0, str(HERE))
    from controls import Controls          # noqa: PLC0415
    c = Controls("postdeploy-control")

    ok, bad = gate_verdict({"/": {"status": 200}, "/sitemap.xml": {"status": 200},
                            "/robots.txt": {"status": 200}})
    c.check("CONTROL_an_all_200_gate_permits_the_announce", ok and not bad,
            "if the gate never passed, nothing would ever be submitted and the "
            "failure would look like a quiet no-op")
    ok, bad = gate_verdict({"/": {"status": 200}, "/sitemap.xml": {"status": 404},
                            "/robots.txt": {"status": 200}})
    c.check("a_single_404_blocks_the_announce", (not ok) and bad == ["/sitemap.xml"],
            "this is the release-swap window: submitting inside it points every "
            "engine at a dead site")
    ok, _ = gate_verdict({"/": {"status": None, "error": "timeout"}})
    c.check("an_unreachable_url_blocks_too_rather_than_being_skipped", not ok,
            "None is not 200, and a gate that ignores it is a gate that opens "
            "when the site is down")
    ok, _ = gate_verdict({"/": {"status": 301}})
    c.check("a_redirect_is_not_healthy", not ok,
            "a 301 on / during a swap is not the site being up")

    c.check("base_prefers_the_explicit_flag",
            resolve_base("https://a.test", {"base": "https://b.test"}) == "https://a.test")
    c.check("base_falls_back_to_the_domain_key",
            resolve_base(None, {"domain": "c.test"}) == "https://c.test",
            "this is the key seostate.py writes into a fresh config")
    c.check("CONTROL_no_base_anywhere_is_empty_not_a_guess",
            resolve_base(None, {}) == "",
            "a guessed origin would announce someone else's site")
    c.check("a_trailing_slash_is_stripped_so_paths_do_not_double_up",
            resolve_base("https://d.test/", {}) == "https://d.test")

    # A sibling script that dies must not read as a clean pass. Exercised
    # through the REAL `run()` against a real sibling invoked wrongly - an
    # argparse usage error goes to stderr and leaves stdout empty, which is
    # precisely the shape that `json.loads("")` would turn into a pass.
    doc, rc, raw = run(["gsc.py"], timeout=60)
    c.check("a_non_json_body_is_surfaced_not_swallowed",
            doc is None and bool(raw) and rc != 0,
            f"doc={doc!r} rc={rc} raw={(raw or '')[:80]!r} - a traceback or a usage "
            "message parsed as {} would report the step as ok")
    doc, rc, raw = run(["gsc.py", "control"], timeout=120)
    c.check("CONTROL_a_sibling_that_works_is_parsed_normally",
            doc is not None and doc.get("ok") is True and rc == 0,
            "without this the check above would pass on a runner that never "
            "parses anything")

    res = c.verdict()
    print(json.dumps(res, indent=2))
    sys.exit(0 if res.get("ok") else 2)


def _receipt(root: Path, payload: dict) -> None:
    """Append one line per run.

    IndexNow returns 200 and keeps no queryable history, so without this the
    question "did we announce that deploy?" has no answer anywhere - which is
    exactly the state that produced a confident, unverifiable claim that a ping
    had never been sent.
    """
    d = root / ".seo"
    try:
        d.mkdir(parents=True, exist_ok=True)
        row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "base": payload.get("base"), "ok": payload.get("ok"),
               "submitted": payload.get("submitted", False),
               "aborted_at": payload.get("aborted_at"),
               "problems": payload.get("problems") or ([payload["reason"]]
                                                       if payload.get("reason") else [])}
        with (d / "postdeploy.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass          # a receipt that cannot be written must not fail the run


if __name__ == "__main__":
    main()
