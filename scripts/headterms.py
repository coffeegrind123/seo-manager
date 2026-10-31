#!/usr/bin/env python3
"""headterms.py - who OWNS each head term, and which page is quietly competing for it.

`sameness.py` measures whether two pages' BODIES are too alike. It cannot see
the failure this file exists for: two pages with entirely different bodies whose
TITLES and H1s carry the same head phrase. To a search engine that is two
candidates for one query, and it rotates between them.

Measured on a real site (2026-10-12, Bing Webmaster `pagequeries`): seven pages
carried the site's head phrase in their title or H1. The engine rotated four of
them at average position 8-10 - the bottom edge of page 1, so the site fell off
page 1 whenever a module was added - and the homepage, the page meant to rank,
was shown for NONE of the two biggest variants. One of the seven had been
retitled toward the head phrase by an earlier run of this very skill, six weeks
before, with every body-level gate green. Nothing checked titles against an
owner, because nothing recorded an owner.

So ownership is now a recorded fact, `.seo/headterms.json`, and this checks it:

    {
      "version": 1,
      "terms": [
        {"phrase": "best hiking boots", "owner": "/",
         "why": "the homepage is the commercial answer", "since": "2026-10-12"},
        {"phrase": "hiking boot sizing", "owner": "/guides/sizing",
         "allow": ["/boots/*"]}
      ],
      "ignore": ["/legal/*"]
    }

`ignore` skips paths for every term (legal pages, a locale tree whose own owner map
lives elsewhere). A term's `allow` lists paths that may carry THAT phrase - a
programmatic family whose pages are long-tail variants of a hub's phrase
("<item> sizing" on every /boots/* page) is a designed pattern, not a collision,
and without `allow` the only choices are thousands of false collisions or
dropping the term from the map.

    headterms.py check --url /guides/new --title "..." --h1 "..."   # BEFORE writing a head
    headterms.py scan --root public/                                 # a built site on disk
    headterms.py scan --urls urls.txt                                # live pages
    headterms.py control

A collision is a page that is NOT the owner carrying an owned phrase in its
title or H1. Body copy and anchors are deliberately not checked - linking to the
owner WITH the phrase is exactly what a supporting page should do.

Matching is on normalised tokens (lowercase, NFKC, every non-word character a
separator), contiguous and on token boundaries: "Play X 1.6 Online!" carries
"play x 1 6 online"; "playing x" does not carry "play x". It is a containment
test, not a similarity score, on purpose - a gate that fires on "kind of
similar" gets argued with, and a gate that gets argued with stops being a gate.

Two refusals, because both read like a clean pass otherwise:
  * an empty or missing ownership map ("nothing collides" when nothing was asked)
  * a scan that parsed zero pages (a wrong --root reads exactly like a clean site)

Stdlib only.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls, refuse  # noqa: E402

DEFAULT_MAP = ".seo/headterms.json"
FIELDS = ("title", "h1")


# ------------------------------------------------------------------ page parsing
class _Page(HTMLParser):
    """Title, first H1, canonical, visible text and JSON-LD strings of one HTML
    document. Shared with claims.py, which needs the same visible-text rules.

    Visible text skips <script> (except JSON-LD, whose string values ARE claims a
    search engine shows as rich results), <style>, <template> and comments.
    Headings and paragraphs end a "block" so sentence splitting downstream never
    glues a heading onto the paragraph under it."""

    BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "div",
             "section", "article", "header", "footer", "br", "tr", "dt", "dd",
             "figcaption", "blockquote", "title", "option"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.h1 = ""
        self.canonical = None
        self.blocks: list[str] = []
        self.jsonld: list[str] = []
        self._buf: list[str] = []
        self._skip = 0
        self._in = None          # "title" | "h1" | None
        self._h1_done = False
        self._ld = False
        self._ld_buf: list[str] = []

    def _flush(self):
        t = " ".join("".join(self._buf).split())
        if t:
            self.blocks.append(t)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script":
            if (a.get("type") or "").lower() == "application/ld+json":
                self._ld = True
                self._ld_buf = []
            else:
                self._skip += 1
            return
        if tag in ("style", "template", "noscript"):
            self._skip += 1
            return
        if tag == "link" and "canonical" in (a.get("rel") or "").lower().split():
            self.canonical = a.get("href")
        if tag in self.BLOCK:
            self._flush()
        if tag == "title":
            self._in = "title"
        elif tag == "h1" and not self._h1_done:
            self._in = "h1"

    def handle_endtag(self, tag):
        if tag == "script":
            if self._ld:
                self._ld = False
                self.jsonld.extend(_json_strings("".join(self._ld_buf)))
            elif self._skip:
                self._skip -= 1
            return
        if tag in ("style", "template", "noscript"):
            self._skip = max(0, self._skip - 1)
            return
        if tag == "title" and self._in == "title":
            self._in = None
        if tag == "h1" and self._in == "h1":
            self._in = None
            self._h1_done = True
        if tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._ld:
            self._ld_buf.append(data)
            return
        if self._skip:
            return
        if self._in == "title":
            self.title += data
        elif self._in == "h1":
            self.h1 += data
        self._buf.append(data)

    def close(self):
        super().close()
        self._flush()
        self.title = " ".join(self.title.split())
        self.h1 = " ".join(self.h1.split())


def _json_strings(raw: str) -> list[str]:
    try:
        obj = json.loads(raw)
    except Exception:
        return []
    out: list[str] = []

    def walk(x):
        if isinstance(x, str):
            if " " in x.strip():              # prose, not ids/urls/types
                out.append(" ".join(x.split()))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(obj)
    return out


def parse_page(html: str) -> dict:
    p = _Page()
    try:
        p.feed(html)
        p.close()
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "title": p.title, "h1": p.h1, "canonical": p.canonical,
            "blocks": p.blocks, "jsonld": p.jsonld}


# ------------------------------------------------------------------- matching
def norm_tokens(s: str) -> list[str]:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return [t for t in re.split(r"[\W_]+", s) if t]


def carries(text: str, phrase: str) -> bool:
    hay, needle = norm_tokens(text), norm_tokens(phrase)
    if not needle or len(needle) > len(hay):
        return False
    n = len(needle)
    return any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def norm_path(u: str | None) -> str | None:
    if not u:
        return None
    parts = urlsplit(u.strip())
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    for suffix in ("/index.html", ".html"):
        if path.endswith(suffix):
            path = path[: -len(suffix)] or "/"
    return path or "/"


# ------------------------------------------------------------------ the map
def load_map(path: str) -> tuple[dict | None, str | None]:
    p = Path(path)
    if not p.is_file():
        return None, f"no ownership map at {path} - record one (see the module docstring)"
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"cannot read {path}: {exc}"
    terms = [t for t in (m.get("terms") or []) if isinstance(t, dict)
             and (t.get("phrase") or "").strip() and t.get("owner")]
    if not terms:
        return None, (f"{path} owns no terms - an empty map would report 'no collisions' "
                      f"without having asked anything")
    m["terms"] = terms
    return m, None


def _ignored(path: str, m: dict) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in (m.get("ignore") or []))


def collisions_for(url: str, title: str, h1: str, m: dict) -> list[dict]:
    path = norm_path(url)
    if path is None or _ignored(path, m):
        return []
    out = []
    for t in m["terms"]:
        owner = norm_path(t["owner"])
        if owner == path or any(fnmatch.fnmatch(path, g) for g in (t.get("allow") or [])):
            continue
        hit = [f for f, v in (("title", title), ("h1", h1)) if carries(v, t["phrase"])]
        if hit:
            out.append({"url": path, "phrase": t["phrase"], "owner": owner,
                        "in": hit, "title": title, "h1": h1})
    return out


# ------------------------------------------------------------------ commands
def cmd_check(a) -> dict:
    m, err = load_map(a.map)
    if err:
        return refuse("headterms-check", err)
    if not (a.title or a.h1):
        return refuse("headterms-check", "nothing to check - pass --title and/or --h1")
    found = collisions_for(a.url, a.title or "", a.h1 or "", m)
    return {"ok": not found, "check": "headterms-check", "url": norm_path(a.url),
            "collisions": found,
            "verdict": "collides" if found else "clear",
            "what_to_do": ("pick a head that answers THIS page's own question; link to the "
                           "owner with the phrase as anchor instead. Changing the owner is a "
                           "decision for the map, made on measurements, not a way to pass "
                           "this check") if found else None}


def _scan_pages(pages: list[tuple[str, str]], m: dict) -> dict:
    seen, coll, owners_seen = 0, [], {}
    unparsed = []
    for url, html in pages:
        pg = parse_page(html)
        if not pg["ok"]:
            unparsed.append({"url": url, "error": pg["error"]})
            continue
        seen += 1
        path = norm_path(pg["canonical"]) if pg["canonical"] else norm_path(url)
        coll.extend(collisions_for(path, pg["title"], pg["h1"], m))
        owners_seen[path] = (pg["title"], pg["h1"])
    if seen == 0:
        return refuse("headterms-scan", "parsed 0 pages - a wrong --root or an empty URL "
                      "list reads exactly like a clean site", unparsed=unparsed[:10])
    owner_report = []
    for t in m["terms"]:
        o = norm_path(t["owner"])
        if o not in owners_seen:
            owner_report.append({"phrase": t["phrase"], "owner": o, "state": "owner_not_scanned"})
            continue
        title, h1 = owners_seen[o]
        if not (carries(title, t["phrase"]) or carries(h1, t["phrase"])):
            owner_report.append({"phrase": t["phrase"], "owner": o, "state": "owner_missing_phrase",
                                 "title": title, "h1": h1})
    by_phrase: dict[str, int] = {}
    for c in coll:
        by_phrase[c["phrase"]] = by_phrase.get(c["phrase"], 0) + 1
    return {"ok": not coll, "check": "headterms-scan", "pages_parsed": seen,
            "collisions": coll, "collisions_by_phrase": by_phrase,
            "owners": owner_report, "unparsed": unparsed[:10],
            "reading": ("owner_missing_phrase is a warning, not a collision: the owner does "
                        "not carry its own phrase in title or H1. owner_not_scanned means the "
                        "owner URL was not in this scan, so nothing is known about it.")}


def _pages_from_root(root: str, glob: str) -> list[tuple[str, str]]:
    base = Path(root)
    out = []
    for f in sorted(base.glob(glob)):
        if f.is_file():
            rel = "/" + f.relative_to(base).as_posix()
            out.append((rel, f.read_text(encoding="utf-8", errors="replace")))
    return out


def _pages_from_urls(file: str, timeout: int) -> list[tuple[str, str]]:
    from providers import http, BROWSER_UA  # noqa: E402 - network only when asked
    out = []
    for u in Path(file).read_text(encoding="utf-8").split():
        r = http(u, ua=BROWSER_UA, timeout=timeout)
        if r.ok:
            out.append((u, (r.get("body") or b"").decode("utf-8", "replace")))
    return out


def cmd_scan(a) -> dict:
    m, err = load_map(a.map)
    if err:
        return refuse("headterms-scan", err)
    if a.root:
        pages = _pages_from_root(a.root, a.glob)
    elif a.urls:
        pages = _pages_from_urls(a.urls, a.timeout)
    else:
        return refuse("headterms-scan", "pass --root DIR or --urls FILE")
    return _scan_pages(pages, m)


# ------------------------------------------------------------------ control
_OWNER = ('<html><head><title>Best Hiking Boots — Reviewed | Example</title>'
          '<link rel="canonical" href="https://example.com/"></head>'
          '<body><h1>Best Hiking Boots</h1><p>body</p></body></html>')
_INTRUDER = ('<html><head><title>Best Hiking Boots: Our Sizing Guide</title>'
             '<link rel="canonical" href="https://example.com/guides/sizing"></head>'
             '<body><h1>Sizing guide</h1></body></html>')
_BODY_ONLY = ('<html><head><title>Hiking Boot Care</title>'
              '<link rel="canonical" href="https://example.com/guides/care"></head>'
              '<body><h1>Caring for boots</h1><p>See the <a href="/">best hiking boots</a>.</p>'
              '</body></html>')
_NEAR_MISS = ('<html><head><title>Bestselling hiking boots</title>'
              '<link rel="canonical" href="https://example.com/sale"></head>'
              '<body><h1>Sale</h1></body></html>')
_H1_ONLY = ('<html><head><title>Trail notes</title>'
            '<link rel="canonical" href="https://example.com/notes/"></head>'
            '<body><h1>The best   hiking-boots, tested</h1></body></html>')
_SCRIPT_H1 = ('<html><head><title>Socks</title></head><body>'
              '<script>document.write("<h1>best hiking boots</h1>")</script><h1>Socks</h1></body></html>')


def run_control() -> dict:
    c = Controls("headterms-control")
    m = {"terms": [{"phrase": "best hiking boots", "owner": "https://example.com/"}],
         "ignore": ["/legal/*"]}

    # token containment: independent expectations, written from the rule, not the code
    c.check("phrase in title with punctuation is carried", carries("Best Hiking Boots — Reviewed", "best hiking boots"))
    c.check("hyphen is a separator", carries("the best hiking-boots", "best hiking boots"))
    c.check("token boundary: 'bestselling' does not carry 'best'", not carries("Bestselling hiking boots", "best hiking boots"))
    c.check("order matters", not carries("hiking boots best", "best hiking boots"))
    c.check("unicode fullwidth normalises (NFKC)", carries("ＢＥＳＴ hiking boots", "best hiking boots"))

    c.check("path norm: trailing slash and index.html collapse",
            norm_path("https://example.com/notes/index.html") == "/notes" and norm_path("/notes/") == "/notes")

    r = _scan_pages([("/", _OWNER), ("/guides/sizing.html", _INTRUDER), ("/guides/care.html", _BODY_ONLY),
                     ("/sale.html", _NEAR_MISS), ("/notes/index.html", _H1_ONLY),
                     ("/socks.html", _SCRIPT_H1)], m)
    urls = sorted(x["url"] for x in r.get("collisions", []))
    c.check("intruder title and H1-only page are BOTH flagged, nothing else",
            urls == ["/guides/sizing", "/notes"], f"got {urls}")
    c.check("the owner itself is never a collision", "/" not in urls)
    c.check("a phrase in body/anchor only is not a collision", "/guides/care" not in urls)
    c.check("an H1 written by a script is not the page's H1",
            "/socks" not in urls)
    c.check("owner carrying its phrase raises no owner warning", r.get("owners") == [])

    r2 = _scan_pages([("/x.html", _INTRUDER)], {"terms": [{"phrase": "best hiking boots", "owner": "/"}]})
    c.check("owner absent from scan is reported, not assumed fine",
            any(o["state"] == "owner_not_scanned" for o in r2.get("owners", [])))
    r3 = _scan_pages([("/", '<html><title>Boots</title><h1>Boots</h1></html>')],
                     {"terms": [{"phrase": "best hiking boots", "owner": "/"}]})
    c.check("owner missing its own phrase is reported",
            any(o["state"] == "owner_missing_phrase" for o in r3.get("owners", [])))

    c.check("zero parsed pages refuses", _scan_pages([], m).get("control_failed") is True)
    c.check("ignored paths are skipped",
            collisions_for("/legal/terms", "Best hiking boots policy", "", m) == [])
    ma = {"terms": [{"phrase": "best hiking boots", "owner": "/", "allow": ["/boots/*"]}]}
    c.check("a term's allow glob permits only that family",
            collisions_for("/boots/x", "Best hiking boots: model X", "", ma) == []
            and len(collisions_for("/socks/x", "Best hiking boots: socks", "", ma)) == 1)
    return c.verdict()


# ------------------------------------------------------------------ cli
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("control", help="prove the gate separates owners, intruders and near-misses")

    s = sub.add_parser("check", help="would THIS title/H1 collide with an owned phrase?")
    s.add_argument("--url", required=True)
    s.add_argument("--title", default="")
    s.add_argument("--h1", default="")
    s.add_argument("--map", default=DEFAULT_MAP)

    s = sub.add_parser("scan", help="every collision across a built site or a URL list")
    s.add_argument("--root")
    s.add_argument("--glob", default="**/*.html")
    s.add_argument("--urls", help="newline-separated URLs to fetch")
    s.add_argument("--timeout", type=int, default=20)
    s.add_argument("--map", default=DEFAULT_MAP)
    s.add_argument("--fail-on-collision", action="store_true", help="exit 1 on any collision (CI)")

    a = p.parse_args()
    if a.cmd == "control":
        out = run_control()
    elif a.cmd == "check":
        out = cmd_check(a)
    else:
        out = cmd_scan(a)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if a.cmd == "scan" and getattr(a, "fail_on_collision", False) and out.get("collisions"):
        sys.exit(1)


if __name__ == "__main__":
    main()
