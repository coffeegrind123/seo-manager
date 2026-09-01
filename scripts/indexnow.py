#!/usr/bin/env python3
"""Get freshly published pages indexed - the honest, free half of it.

WHAT IS AND IS NOT POSSIBLE (this matters, because the internet lies about it):

  IndexNow            FREE, keyless, instant. Bing, Yandex, Seznam, Naver and
                      DuckDuckGo (via Bing) accept a ping and crawl within
                      minutes. NOT Google - Google has never joined IndexNow.
  Google Indexing API Restricted to JobPosting and BroadcastEvent pages. Using
                      it for ordinary content is against its terms and simply
                      does not work. This script will not pretend otherwise.
  Sitemap submit      The ONE programmatic nudge Google offers, and it asks for
                      a FEED re-read rather than for a URL to be indexed.
                      `gsc.py sitemap-submit` does it; `postdeploy.py` runs it
                      in sequence behind a health gate.
  URL Inspection API  READ-only. It can tell you whether Google has a page; it
                      cannot ask Google to take one. (`gsc.py inspect`.)
  "Request indexing"  The fastest legitimate accelerator for Google, and it is
                      a HUMAN clicking a button in Search Console. This script
                      batches the pending list into one paste-ready set of
                      steps rather than pretending it can be automated.

So: ping IndexNow automatically, and hand the operator ONE batched Google
follow-up instead of one per page. The Search Console quota is per property per
day, so batching costs nothing and saves a session per page.

    indexnow.py key --domain example.com          # generate + placement steps
    indexnow.py ping --url https://example.com/a  # ping one or more URLs
    indexnow.py ping --pending                    # every page not yet submitted
    indexnow.py google-steps                      # the batched manual follow-up

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

UA = "seo-manager/1.0 (+indexnow)"

HERE = Path(__file__).resolve().parent
ENDPOINT = "https://api.indexnow.org/IndexNow"


sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls  # noqa: E402


def state(root, *args) -> dict:
    cmd = [sys.executable, str(HERE / "seostate.py")]
    if root:
        cmd += ["--root", root]
    proc = subprocess.run(cmd + list(args), capture_output=True, text=True)
    if not proc.stdout.strip():
        return {"ok": False, "error": (proc.stderr or "no output").strip()[:200]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": proc.stdout[:200]}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cmd_key(a):
    cfg = state(a.root, "config").get("config", {})
    domain = a.domain or cfg.get("domain")
    if not domain:
        print(json.dumps({"ok": False, "error": "no --domain and no project configured"}, indent=2))
        return 1
    key = cfg.get("indexnow_key") or uuid.uuid4().hex
    if not cfg.get("indexnow_key"):
        state(a.root, "config", "--set", f"indexnow_key={key}")
    print(json.dumps({
        "ok": True,
        "domain": domain,
        "key": key,
        "keyfile": f"{key}.txt",
        "must_be_served_at": f"https://{domain}/{key}.txt",
        "file_contents": key,
        "steps": [
            f"Create a file named `{key}.txt` at the site's PUBLIC web root, containing exactly "
            f"the key `{key}` and nothing else.",
            "Commit and deploy it. IndexNow verifies ownership by fetching that file, so a ping "
            "before it is live is rejected.",
            f"Verify like a stranger:  curl -s https://{domain}/{key}.txt   -> must print the key.",
            "Then: indexnow.py ping --pending",
        ],
        "note": "The key is stored in .seo/config.json as indexnow_key. It is not a secret - it is "
                "a public ownership proof and is meant to be served publicly.",
    }, indent=2))
    return 0


def submit(domain: str, key: str, urls: list[str]) -> dict:
    body = json.dumps({
        "host": domain,
        "key": key,
        "keyLocation": f"https://{domain}/{key}.txt",
        "urlList": urls,
    }).encode()
    req = urllib.request.Request(ENDPOINT, data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8",
                                          "User-Agent": "seo-manager/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"status": resp.status, "body": resp.read().decode("utf-8", "replace")[:300]}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": exc.read().decode("utf-8", "replace")[:300]}
    except Exception as exc:
        return {"status": 0, "body": f"{type(exc).__name__}: {exc}"}


# IndexNow's documented status meanings - a 200 is not the only success, and a
# 403 means exactly one thing, so say which rather than dumping a number.
STATUS_MEANING = {
    200: "accepted - the engines will crawl shortly",
    202: "accepted, key validation pending",
    400: "bad request - malformed URL list",
    403: "key not valid: the key file is not being served at keyLocation, or its contents differ",
    422: "URLs do not belong to the host, or the key does not match",
    429: "too many requests - slow down",
}


def run_control() -> dict:
    """Prove the status reader discriminates - offline, submitting nothing.

    A submitter that reports success on every code is worse than none: it turns
    "the key file is not being served" into "5,388 URLs submitted", and nobody
    looks again for weeks."""
    c = Controls("indexnow-control")
    c.check("200_is_accepted", "accepted" in STATUS_MEANING.get(200, ""))
    c.check("202_is_also_a_success",
            "accepted" in STATUS_MEANING.get(202, ""),
            "a 202 is a success; treating only 200 as one under-reports every submit")
    c.check("403_names_the_key_file_specifically",
            "key" in STATUS_MEANING.get(403, "").lower(),
            "403 means exactly one thing - say which, do not print a number")
    c.check("422_is_distinguished_from_403", STATUS_MEANING.get(422) != STATUS_MEANING.get(403))
    c.check("429_is_rate_limiting_not_failure", "429" in str(sorted(STATUS_MEANING)))
    c.check("an_unknown_status_is_not_silently_a_success",
            STATUS_MEANING.get(500) is None,
            "unmapped codes must fall through to 'unexpected status', never to accepted")
    c.check("the_endpoint_is_the_shared_indexnow_one",
            ENDPOINT.startswith("https://") and "indexnow" in ENDPOINT, ENDPOINT)
    c.check("success_and_failure_are_not_the_same_string",
            STATUS_MEANING[200] != STATUS_MEANING[400])

    # The sitemap reader. `--pending` on a generated site resolves to zero and
    # reports ok, so this is the source that actually announces anything - and
    # a reader that silently returns [] would recreate the same no-op with a
    # different name.
    import tempfile                          # noqa: PLC0415 - control-only
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "child.xml").write_text(
            '<?xml version="1.0"?><urlset><url><loc>https://x.test/a</loc></url>'
            '<url><loc>https://x.test/b</loc></url>'
            '<url><loc>https://x.test/a</loc></url></urlset>', encoding="utf-8")
        (d / "index.xml").write_text(
            f'<?xml version="1.0"?><sitemapindex><sitemap><loc>{d / "child.xml"}</loc>'
            '</sitemap></sitemapindex>', encoding="utf-8")
        urls, err = read_sitemap(str(d / "child.xml"))
        c.check("a_flat_sitemap_yields_its_locs", urls == ["https://x.test/a",
                                                          "https://x.test/b"] and not err,
                f"{urls} {err}")
        c.check("duplicate_locs_are_collapsed", len(urls) == 2,
                "submitting the same URL twice in one batch wastes the batch")
        idx, err = read_sitemap(str(d / "index.xml"))
        c.check("a_sitemap_INDEX_is_followed_one_level", idx == urls and not err,
                f"{idx} {err}")
        (d / "empty.xml").write_text("<urlset></urlset>", encoding="utf-8")
        urls, err = read_sitemap(str(d / "empty.xml"))
        c.check("an_empty_sitemap_is_an_ERROR_not_an_empty_submit",
                urls == [] and bool(err),
                "an empty list would ping nothing and report success")
        urls, err = read_sitemap(str(d / "does-not-exist.xml"))
        c.check("an_unreadable_sitemap_is_an_ERROR_not_an_empty_submit",
                urls == [] and bool(err),
                "this is the failure that turns a broken feed into '0 URLs submitted, ok'")
        urls, _e = read_sitemap(str(d / "child.xml"), limit=1)
        c.check("CONTROL_the_limit_is_honoured", urls == ["https://x.test/a"])

    return c.verdict(known_statuses=sorted(STATUS_MEANING),
                     note="nothing was submitted; this proves the READER, not the account")


def read_sitemap(src, limit=50000):
    """Every <loc> in a sitemap or sitemap index. (urls, error).

    Follows one level of index, which is all the spec allows. A sitemap that
    cannot be read is an ERROR, never an empty list: submitting "no URLs" after
    a failed fetch reads as a successful ping of nothing.
    """
    seen, out_urls = set(), []

    def fetch(u):
        if re.match(r"^https?://", u):
            req = urllib.request.Request(u, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
        else:
            raw = Path(u).read_bytes()
        if raw[:2] == b"\x1f\x8b":
            import gzip                       # noqa: PLC0415 - only for .gz feeds
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")

    try:
        body = fetch(src)
    except Exception as exc:                  # noqa: BLE001 - reported, not raised
        return [], f"could not read {src}: {exc}"
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body)
    if "<sitemapindex" in body[:2000]:
        for child in locs:
            if len(out_urls) >= limit:
                break
            try:
                sub = fetch(child)
            except Exception as exc:          # noqa: BLE001
                return [], f"could not read child sitemap {child}: {exc}"
            for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sub):
                if u not in seen:
                    seen.add(u)
                    out_urls.append(u)
                    if len(out_urls) >= limit:
                        break
    else:
        for u in locs:
            if u not in seen:
                seen.add(u)
                out_urls.append(u)
                if len(out_urls) >= limit:
                    break
    if not out_urls:
        return [], f"no <loc> entries found in {src}"
    return out_urls, None


def cmd_ping(a):
    cfg = state(a.root, "config").get("config", {})
    domain = (a.domain or cfg.get("domain") or "").replace("https://", "").strip("/")
    key = cfg.get("indexnow_key")
    if not domain:
        print(json.dumps({"ok": False, "error": "no domain configured"}, indent=2))
        return 1
    if not key:
        print(json.dumps({"ok": False, "error": "no IndexNow key yet - run: indexnow.py key"}, indent=2))
        return 1

    urls = list(a.url or [])
    pages = state(a.root, "pages").get("pages", [])
    if a.pending:
        urls += [p["url"] for p in pages if p.get("url") and not p.get("indexnow_submitted_at")]
    sitemap_urls = []
    if a.sitemap:
        # `--pending` reads the CONTENT QUEUE, which only knows pages this skill
        # published. On a generated silo - thousands of pages emitted by a
        # build script and never queued - it resolves to ZERO and the ping
        # becomes a no-op that still reports ok. Measured on a 5,388-URL site:
        # `--pending` submitted 0. A sitemap is the only complete source of
        # what that site actually publishes.
        sitemap_urls, sm_err = read_sitemap(a.sitemap, a.limit)
        if sm_err:
            print(json.dumps({"ok": False, "error": sm_err, "sitemap": a.sitemap,
                              "note": "refusing rather than submitting a partial set"},
                             indent=2))
            return 1
        urls += sitemap_urls
    urls = [u for u in dict.fromkeys(urls) if u.startswith("http")]
    if not urls:
        note = ("nothing pending - every logged page has already been submitted"
                if a.pending and not a.sitemap else "no URLs resolved from the given sources")
        if a.pending and not a.sitemap:
            note += (". ⚠ On a GENERATED site the content queue is empty by "
                     "construction and this zero is not a statement about the "
                     "site - pass --sitemap <url> to submit what it publishes.")
        print(json.dumps({"ok": True, "submitted": 0, "note": note}, indent=2))
        return 0
    if a.dry_run:
        print(json.dumps({"ok": True, "would_submit": urls, "host": domain}, indent=2))
        return 0

    # IndexNow caps a batch at 10,000; chunk defensively anyway.
    results = []
    for i in range(0, len(urls), 500):
        chunk = urls[i:i + 500]
        res = submit(domain, key, chunk)
        res["count"] = len(chunk)
        res["meaning"] = STATUS_MEANING.get(res["status"], "unexpected status")
        results.append(res)

    ok = all(r["status"] in (200, 202) for r in results)
    if ok:
        # Stamp only on success, so a failed ping stays pending instead of
        # silently dropping out of the queue.
        stamp = now()
        for p in pages:
            if p.get("url") in urls:
                state(a.root, "log-page", "--url", p["url"], "--indexnow-submitted", stamp)
    print(json.dumps({
        "ok": ok,
        "host": domain,
        "submitted": len(urls) if ok else 0,
        "urls": urls,
        "results": results,
        "reaches": "Bing, Yandex, Seznam, Naver, DuckDuckGo (via Bing). NOT Google.",
        "google": "Google does not participate in IndexNow. Run `indexnow.py google-steps` for the "
                  "batched Search Console follow-up, which is the only legitimate accelerator.",
    }, indent=2))
    return 0 if ok else 1


def cmd_google_steps(a):
    cfg = state(a.root, "config").get("config", {})
    domain = cfg.get("domain", "")
    pages = state(a.root, "pages").get("pages", [])
    pending = [p for p in pages if p.get("url") and not p.get("index_requested_at")]
    if not pending:
        print(json.dumps({"ok": True, "pending": 0,
                          "note": "every logged page has already been through Request indexing"},
                         indent=2))
        return 0
    urls = [p["url"] for p in pending]
    print(json.dumps({
        "ok": True,
        "pending": len(urls),
        "urls": urls,
        "why_manual": "Google offers no API for this. The Indexing API is JobPosting/BroadcastEvent "
                      "only, and URL Inspection is read-only. A human clicking 'Request indexing' is "
                      "the fastest legitimate route, so these are batched into ONE session - the "
                      "quota is per property per day, so batching costs nothing.",
        "steps": [
            f"Open Search Console for {domain} and make sure the right property is selected.",
            "For each URL below: paste it into the inspection bar at the top, wait for the result, "
            "then click 'Request indexing' and wait for the confirmation toast before the next one.",
            "Expect a daily quota (~10-12 URLs). If you hit it, stop and finish tomorrow - the "
            "remaining ones stay listed here.",
            "When done, record it so they stop showing up:",
        ],
        "record_when_done": [f'seostate.py log-page --url "{u}" --index-requested' for u in urls[:3]]
                            + (["... (one per URL)"] if len(urls) > 3 else []),
        "browser_option": "The browser-automation skill can drive this: navigate to Search Console, "
                          "inspect each URL, click Request indexing. Watch the quota, and confirm "
                          "each toast before moving on.",
        "verification": "`gsc.py inspect` tells you whether Google "
                        "actually has each page - that is the real done signal, not the click.",
    }, indent=2))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", help="repo root (defaults to the nearest .seo/)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("control", help="prove the status reader discriminates (submits nothing)").set_defaults(
        fn=lambda a: (print(json.dumps(run_control(), indent=2, ensure_ascii=False)), 0)[1])

    s = sub.add_parser("key", help="generate the IndexNow key and print placement steps")
    s.add_argument("--domain")
    s.set_defaults(fn=cmd_key)

    s = sub.add_parser("ping", help="submit URLs to IndexNow (Bing/Yandex/Seznam/Naver)")
    s.add_argument("--url", action="append")
    s.add_argument("--pending", action="store_true", help="every logged page not yet submitted")
    # The source that works on a GENERATED site, where the content queue is
    # empty by construction and --pending resolves to zero while still
    # reporting ok. Accepts a sitemap index and follows one level.
    s.add_argument("--sitemap", help="sitemap URL or file - submit every <loc> (index ok)")
    s.add_argument("--limit", type=int, default=50000, help="cap on URLs read from a sitemap")
    s.add_argument("--domain")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_ping)

    s = sub.add_parser("google-steps", help="the batched Search Console follow-up for Google")
    s.set_defaults(fn=cmd_google_steps)

    a = p.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
