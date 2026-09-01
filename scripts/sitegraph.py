#!/usr/bin/env python3
"""
sitegraph.py - the internal link graph, offline or live.

WHY THIS EXISTS (2026-08-31, combatskirmish.net). Twelve guides and a tool page
sat live for nine days with ZERO impressions. GSC said "URL is unknown to Google,
never crawled" while the control page was indexed; the origin log said Googlebot
had fetched 221 distinct URLs of 5,388 and not one of them was a guide. The pages
were technically perfect - 200, index,follow, self-canonical, in the sitemap - and
reachable only through one nav link to a hub, i.e. two hops from anything a
crawler visits.

Finding that took a `grep -rl` over 3,977 files, which ran for minutes and hit
ENOMEM. A link graph answers it in one pass - and, more to the point, the silo
report below surfaces it WITHOUT anyone having to think of the question first.

Two modes, and the offline one is the point:

  offline  walk a directory of generated .html and map paths -> URLs. No network,
           no rate limit, no robots.txt, runs on an undeployed tree. This is how
           you catch the problem BEFORE it ships.
  live     BFS over HTTP, obeying robots.txt per origin, rate-limited.

Stdlib only, like every other script here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import defaultdict, deque
from html.parser import HTMLParser

UA = "Mozilla/5.0 (compatible; sitegraph/1.0; +seo-manager)"

_AC = None


def _agentcheck():
    """agentcheck.py carries the only correct robots resolver in this skill
    (most-specific UA group, then LONGEST path match, ties to Allow). Imported
    lazily so sitegraph still runs standalone if it is missing."""
    global _AC
    if _AC is None:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import agentcheck as _m
        _AC = _m
    return _AC
BOILERPLATE_TAGS = {"nav", "footer", "header", "aside"}


# --------------------------------------------------------------------------- parse
class PageParser(HTMLParser):
    """Extracts links (with anchor text and a boilerplate flag), title, canonical,
    meta-robots and a rough word count in ONE pass over the document.

    The boilerplate flag is load-bearing, not a nicety: a link inside <nav> is on
    every page by construction, so counting it as an inbound link makes every page
    look well-linked. The 2026-08-31 case was exactly this - every guide had a nav
    link to its hub and no contextual link anywhere."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, bool]] = []   # (href, anchor, in_boilerplate)
        self.title = ""
        self.canonical = ""
        self.robots = ""
        self.alternates: list[str] = []
        self._stack: list[str] = []
        self._a_href: str | None = None
        self._a_text: list[str] = []
        self._a_boiler = False
        self._in_title = False
        self._in_skip = 0          # script/style depth
        self._text: list[str] = []

    def _boiler(self) -> bool:
        return any(t in BOILERPLATE_TAGS for t in self._stack)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self._in_skip += 1
        self._stack.append(tag)
        if tag == "title":
            self._in_title = True
        elif tag == "a":
            href = a.get("href")
            if href:
                self._a_href = href
                self._a_text = []
                self._a_boiler = self._boiler()
        elif tag == "link":
            rel = (a.get("rel") or "").lower()
            if "canonical" in rel and a.get("href"):
                self.canonical = a["href"]
            # hreflang alternates ARE discovery edges. Google follows them, and a
            # site with 22 locales whose pages link within their own locale looks
            # like 21 island silos if you only count <a>. That is the instrument
            # being wrong, not the site - so count them, but mark them, because
            # they are not editorial links and must not inflate a contextual count.
            if "alternate" in rel and a.get("hreflang") and a.get("href"):
                self.alternates.append(a["href"])
        elif tag == "meta":
            if (a.get("name") or "").lower() == "robots":
                self.robots = (a.get("content") or "").lower()

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._in_skip:
            self._in_skip -= 1
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._a_href is not None:
            anchor = re.sub(r"\s+", " ", "".join(self._a_text)).strip()
            self.links.append((self._a_href, anchor[:120], self._a_boiler))
            self._a_href = None
        while self._stack:
            popped = self._stack.pop()
            if popped == tag:
                break

    def handle_data(self, data):
        if self._in_skip:
            return
        if self._in_title:
            self.title += data
        if self._a_href is not None:
            self._a_text.append(data)
        self._text.append(data)

    @property
    def words(self) -> int:
        return len(re.findall(r"[A-Za-zÀ-ɏЀ-ӿ]{2,}", " ".join(self._text)))


def parse(html: str) -> PageParser:
    p = PageParser()
    try:
        p.feed(html)
    except Exception:
        pass
    return p


# --------------------------------------------------------------------------- urls
def norm(u: str, keep_query: bool = False) -> str:
    """Normalise an internal URL to a comparable path. Fragments always go; the
    query goes by default because ?utm= and friends are the same page."""
    u = (u or "").strip()
    if not u:
        return ""
    sp = urllib.parse.urlsplit(u)
    path = urllib.parse.unquote(sp.path or "/")
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    if not path:
        path = "/"
    if keep_query and sp.query:
        return f"{path}?{sp.query}"
    return path


def is_internal(href: str, host: str | None) -> bool:
    sp = urllib.parse.urlsplit(href)
    if sp.scheme and sp.scheme not in ("http", "https"):
        return False           # mailto:, tel:, javascript:, data:
    if not sp.netloc:
        return True            # relative
    if host is None:
        return False
    return sp.netloc.lower().split(":")[0].removeprefix("www.") == host


# --------------------------------------------------------------------------- graph
class Graph:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self._uid: dict[str, int] = {}
        self.anchors: list[str] = []
        self._aid: dict[str, int] = {}
        self.adj: dict[int, list[tuple[int, int, int]]] = defaultdict(list)  # src -> (dst, anchor, boiler)
        self.meta: dict[int, dict] = {}

    def uid(self, url: str) -> int:
        i = self._uid.get(url)
        if i is None:
            i = len(self.urls)
            self._uid[url] = i
            self.urls.append(url)
        return i

    def aid(self, text: str) -> int:
        i = self._aid.get(text)
        if i is None:
            i = len(self.anchors)
            self._aid[text] = i
            self.anchors.append(text)
        return i

    def section_roots(self, threshold: float = 0.5, min_pages: int = 10) -> set[int]:
        """Targets that are the ROOT of a path section and are linked from most
        of the pages beneath them - i.e. a section/locale nav.

        `site_wide` cannot see these. A locale nav appears on 100% of that
        locale's pages and on 1.5% of the site, so a site-wide frequency rule
        never fires. Measured 2026-09-01 on combatskirmish.net: /zh was linked
        from 60 of the 60 pages in its own locale, by a breadcrumb and a logo,
        and every one of the 21 locale homes still reported ZERO contextual
        inlinks - which reads as "the entry point to this language is orphaned"
        when it is the single best-linked page in its own section.

        ⚠ DELIBERATELY NARROW, and the narrowness is the point. It requires the
        target to be the PARENT PATH of the pages linking to it, so it can never
        suppress the island finding this tool was written for: /guides/bunny-hop
        is linked from 16 of the 17 guides, but /guides is their parent and
        bunny-hop is not, so the guides island stays visible. A pure
        share-of-section rule would have hidden it at any threshold - 16/17 is
        0.94, indistinguishable from a nav by frequency alone."""
        roots: set[int] = set()
        children: dict[int, list[int]] = defaultdict(list)
        for uid, meta in self.meta.items():
            if not meta.get("exists", True):
                continue
            u = self.urls[uid]
            if u == "/":
                continue
            for cand, cmeta in self.meta.items():
                if cand == uid or not cmeta.get("exists", True):
                    continue
                if self.urls[cand].startswith(u + "/"):
                    children[uid].append(cand)
        for uid, kids in children.items():
            if len(kids) < min_pages:
                continue
            linkers = sum(1 for k in kids
                          if any(dst == uid for dst, _a, _b in self.adj.get(k, [])))
            if linkers / len(kids) > threshold:
                roots.add(uid)
        return roots

    def to_json(self, extra: dict) -> dict:
        return {
            "urls": self.urls,
            "anchors": self.anchors,
            "adj": {str(k): v for k, v in self.adj.items()},
            "meta": {str(k): v for k, v in self.meta.items()},
            **extra,
        }

    @staticmethod
    def from_json(d: dict) -> tuple["Graph", dict]:
        g = Graph()
        g.urls = d["urls"]
        g._uid = {u: i for i, u in enumerate(g.urls)}
        g.anchors = d["anchors"]
        g.adj = defaultdict(list, {int(k): [tuple(e) for e in v] for k, v in d["adj"].items()})
        g.meta = {int(k): v for k, v in d.get("meta", {}).items()}
        return g, d

    def site_wide(self, threshold: float = 0.3, min_pages: int = 50,
                  min_sources: int = 10) -> set[int]:
        """Targets linked from more than `threshold` of all pages are site
        FURNITURE, whatever markup wraps them.

        Tag-based detection (<nav>/<footer>) is not enough and this is not
        hypothetical: combatskirmish.net renders its whole silo nav as
        `<p class="silonav">`, a plain paragraph. A tag-only rule counted that
        as a contextual link on 3,900 pages and reported the guides silo as
        healthy - the exact opposite of the truth it was written to find. A
        class allowlist would be a per-site guess; frequency is a measurement."""
        pages = max(1, sum(1 for u in self.meta if self.meta[u].get("exists", True)))
        # A SHARE is meaningless on a small site: at 30% of 7 pages, two ordinary
        # editorial links look like site furniture. Require both a real corpus and
        # an absolute floor before the frequency rule is allowed to fire; below
        # that, fall back to tag-based detection alone. Caught by test_sitegraph.
        if pages < min_pages:
            return set()
        srcs: dict[int, set[int]] = defaultdict(set)
        for src, edges in self.adj.items():
            for dst, _a, _b in edges:
                if dst != src:
                    srcs[dst].add(src)
        return {d for d, ss in srcs.items()
                if len(ss) >= min_sources and len(ss) / pages > threshold}

    def inbound(self, contextual_only: bool = False,
                site_wide: set[int] | None = None) -> dict[int, list[tuple[int, int]]]:
        inb: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for src, edges in self.adj.items():
            for dst, anch, boiler in edges:
                if dst == src:
                    continue        # a self-loop is not an inbound link. Self-referential
                                    # hreflang is REQUIRED markup, and counting it made every
                                    # English-only silo look reachable from elsewhere.
                if contextual_only and (boiler or (site_wide and dst in site_wide)):
                    continue
                inb[dst].append((src, anch))
        return inb


# --------------------------------------------------------------------------- offline
def build_offline(roots: list[tuple[str, str]], keep_query: bool, index_names: list[str],
                  rewrites: list[tuple[str, str]] | None = None) -> tuple[Graph, dict]:
    """roots: list of (directory, url_prefix). A file <dir>/a/b.html becomes
    <prefix>/a/b; <dir>/a/index.html becomes <prefix>/a."""
    g = Graph()
    files_read = 0
    rw = [(re.compile(a), b) for a, b in (rewrites or [])]

    def apply_rw(u: str) -> str:
        for rx, repl in rw:
            u = rx.sub(repl, u)
        return norm(u)

    for root, prefix in roots:
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                if not name.endswith((".html", ".htm")):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                if name in index_names:
                    rel = rel[: -(len(name) + 1)] if "/" in rel else ""
                else:
                    rel = rel.rsplit(".", 1)[0]
                url = apply_rw(norm((prefix.rstrip("/") + "/" + rel) if rel else (prefix or "/")))
                try:
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        html = fh.read()
                except OSError:
                    continue
                files_read += 1
                p = parse(html)
                src = g.uid(url)
                g.meta[src] = {
                    "file": os.path.relpath(full),
                    "title": re.sub(r"\s+", " ", p.title).strip()[:160],
                    "words": p.words,
                    "canonical": norm(p.canonical) if p.canonical else "",
                    "robots": p.robots,
                    "exists": True,
                }
                for href in p.alternates:
                    tgt = apply_rw(norm(href, keep_query))
                    if tgt:
                        g.adj[src].append((g.uid(tgt), g.aid("[hreflang]"), True))
                for href, anchor, boiler in p.links:
                    if not is_internal(href, None) and urllib.parse.urlsplit(href).netloc:
                        continue
                    if urllib.parse.urlsplit(href).scheme not in ("", "http", "https"):
                        continue
                    tgt = apply_rw(norm(urllib.parse.urljoin(url + "/", href) if not href.startswith("/") else href, keep_query))
                    if not tgt:
                        continue
                    g.adj[src].append((g.uid(tgt), g.aid(anchor), boiler))
    return g, {"mode": "offline", "files_read": files_read}


# --------------------------------------------------------------------------- live
def build_live(start: str, max_pages: int, delay: float, timeout: int,
               keep_query: bool, obey_robots: bool) -> tuple[Graph, dict]:
    sp = urllib.parse.urlsplit(start)
    host = sp.netloc.lower().split(":")[0].removeprefix("www.")
    origin = f"{sp.scheme}://{sp.netloc}"
    groups = None
    robots_state = "not-checked"
    if obey_robots:
        # Fetch robots.txt AS OURSELVES, and resolve it with Google's semantics.
        #
        # Two bugs lived here, both found crawling a real Cloudflare-fronted site:
        #
        # 1. RobotFileParser.read() uses urllib's DEFAULT User-Agent, which
        #    Cloudflare answers with 403. A 403 on robots.txt means disallow-all
        #    by spec, so the crawler correctly refused every URL - and then
        #    reported "no pages indexed - check --url", which sends you looking
        #    in entirely the wrong place. Robots rules are per-UA; you have to
        #    ask for the file as the agent you intend to be.
        # 2. urllib.robotparser is FIRST-MATCH-WINS. On a file whose `*` group
        #    opens with `Allow: /` before `Disallow: /api/`, it declares /api/x
        #    ALLOWED. Google uses LONGEST-MATCH, which disallows it. That is not
        #    academic - it is the difference between obeying robots.txt and only
        #    appearing to. `agentcheck.allowed()` already implements the correct
        #    rule, so this reuses it rather than shipping a second, wronger one.
        try:
            req = urllib.request.Request(origin + "/robots.txt", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read(500_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
            groups = _agentcheck().parse_robots(body)
            robots_state = "loaded"
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                # Denied, not permitted. Per RFC 9309 this means disallow-all, and
                # saying so is the whole point - "we were refused the file" and
                # "the site disallows us" must not look identical downstream.
                return Graph(), {"mode": "live", "origin": origin, "fetched": 0,
                                 "robots": f"DENIED-{e.code}", "robots_blocked": 0,
                                 "robots_note": (f"robots.txt returned {e.code}, which RFC 9309 "
                                                 "treats as disallow-all, so nothing was crawled. "
                                                 "This is a REFUSAL, not an empty site. Retry from "
                                                 "an allowed network, or --ignore-robots only if "
                                                 "you own the site and accept that.")}
            groups, robots_state = None, f"unreadable-http-{e.code}-proceeding"
        except Exception:
            groups, robots_state = None, "unreadable-proceeding"
    g = Graph()
    q: deque[str] = deque([norm(start, keep_query)])
    seen: set[str] = set(q)
    fetched = blocked = 0
    while q and fetched < max_pages:
        path = q.popleft()
        url = origin + path
        if groups is not None and not _agentcheck().allowed(groups, "sitegraph", path).get("allowed", True):
            blocked += 1
            continue
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        status, html, ctype = 0, "", ""
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status = r.status
                ctype = (r.headers.get("Content-Type") or "").lower()
                if "html" in ctype:
                    html = r.read(3_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
        except urllib.error.HTTPError as e:
            status = e.code
        except Exception:
            status = -1
        fetched += 1
        time.sleep(delay)
        src = g.uid(path)
        p = parse(html) if html else None
        g.meta[src] = {
            "status": status,
            "title": (re.sub(r"\s+", " ", p.title).strip()[:160] if p else ""),
            "words": (p.words if p else 0),
            "canonical": (norm(p.canonical) if p and p.canonical else ""),
            "robots": (p.robots if p else ""),
            "exists": status == 200,
        }
        if not p:
            continue
        for href in p.alternates:
            if is_internal(href, host):
                t = norm(urllib.parse.urljoin(url, href), keep_query)
                if t:
                    g.adj[src].append((g.uid(t), g.aid("[hreflang]"), True))
        for href, anchor, boiler in p.links:
            if not is_internal(href, host):
                continue
            tgt = norm(urllib.parse.urljoin(url, href), keep_query)
            if not tgt:
                continue
            g.adj[src].append((g.uid(tgt), g.aid(anchor), boiler))
            if tgt not in seen and len(seen) < max_pages * 4:
                seen.add(tgt)
                q.append(tgt)
    return g, {"mode": "live", "origin": origin, "fetched": fetched,
               "robots": robots_state, "robots_blocked": blocked}


# --------------------------------------------------------------------------- control
CONTROL_HTML = """<html><head><title>T</title>
<link rel="canonical" href="/c"><meta name="robots" content="noindex">
<link rel="alternate" hreflang="es" href="/es/c">
</head><body>
<nav><a href="/nav">navlink</a></nav>
<p>Some words here for counting.</p>
<a href="/real">real anchor</a><a href="mailto:x@y.z">mail</a>
<a href="https://external.example/x">ext</a>
<footer><a href="/foot">footlink</a></footer>
</body></html>"""


def _self_loop_control() -> bool:
    """A page whose only 'inbound link' is its own self-referential hreflang must
    report ZERO inbound links."""
    g = Graph()
    a = g.uid("/a")
    g.meta[a] = {"exists": True}
    g.adj[a].append((a, g.aid("[hreflang]"), True))
    return len(g.inbound().get(a, [])) == 0


def run_control() -> dict:
    p = parse(CONTROL_HTML)
    hrefs = [h for h, _a, _b in p.links]
    boiler = {h: b for h, _a, b in p.links}
    anchors = {h: a for h, a, _b in p.links}
    checks = {
        "title_read": p.title == "T",
        "canonical_read": p.canonical == "/c",
        "robots_read": p.robots == "noindex",
        "all_anchors_found": hrefs == ["/nav", "/real", "mailto:x@y.z", "https://external.example/x", "/foot"],
        "anchor_text_read": anchors.get("/real") == "real anchor",
        "nav_marked_boilerplate": boiler.get("/nav") is True,
        "footer_marked_boilerplate": boiler.get("/foot") is True,
        "body_link_not_boilerplate": boiler.get("/real") is False,
        "mailto_excluded_as_internal": is_internal("mailto:x@y.z", "example.com") is False,
        "external_excluded": is_internal("https://external.example/x", "example.com") is False,
        "relative_is_internal": is_internal("/real", "example.com") is True,
        "norm_strips_fragment": norm("/a/b#frag") == "/a/b",
        "norm_strips_trailing_slash": norm("/a/b/") == "/a/b",
        "norm_keeps_root": norm("/") == "/",
        "words_counted": p.words >= 4,
        "hreflang_alternate_read": p.alternates == ["/es/c"],
        "self_loop_not_an_inlink": _self_loop_control(),
    }
    return {"ok": all(checks.values()), "check": "sitegraph-control", "checks": checks}


# --------------------------------------------------------------------------- guards
def graph_guard(g: Graph, info: dict) -> dict | None:
    """A link graph with no links is not a site with no links - it is a broken
    parser, and it would report EVERY page as an orphan. Refuse the verdict, the
    same way hreflang.py and contract.py do."""
    pages = len(g.meta)
    edges = sum(len(v) for v in g.adj.values())
    with_out = sum(1 for k in g.meta if g.adj.get(k))
    if pages == 0:
        return {"ok": False, "control_failed": True, "reason": "no pages indexed - check --root/--url"}
    if edges == 0:
        return {"ok": False, "control_failed": True, "reason": f"{pages} pages but ZERO links extracted - parser or scope is broken, refusing a verdict"}
    share = with_out / pages
    if share < 0.5:
        return {"ok": False, "control_failed": True,
                "reason": f"only {with_out}/{pages} ({share:.0%}) pages have any outbound link - implausible, refusing a verdict"}
    return None


def load(path: str) -> tuple[Graph, dict]:
    with open(path, "r", encoding="utf-8") as fh:
        d = json.load(fh)
    return Graph.from_json(d)


def out(o) -> None:
    print(json.dumps(o, indent=2, ensure_ascii=False))


def pct(xs: list[int], p: float) -> int:
    if not xs:
        return 0
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round((len(s) - 1) * p))))
    return s[i]


LOCALE_SEG = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2,4})?$")


def locale_prefixes(g: "Graph", min_locales: int = 3) -> set[str]:
    """Which leading path segments are LOCALES rather than content silos.

    Needed because depth-1 bucketing splits a localized site by language:
    silo_of('/ar/maps/x') is '/ar', so an Arabic map page counts as EXTERNAL to
    the English maps silo. It is not - it is the same content type in another
    language, and on a 22-locale site that turns ~780 sibling links per locale
    into fake external inbound and hides whether the silo is really an island.

    Detected, not configured: a leading segment is a locale only if it is
    ISO-shaped AND the site has at least `min_locales` of them. A site with a
    genuine two-letter silo (/ui, /os) is unaffected, because one such segment
    never reaches the threshold."""
    firsts: dict[str, int] = defaultdict(int)
    for u in g.urls:
        parts = [x for x in u.split("/") if x]
        if parts and LOCALE_SEG.match(parts[0]):
            firsts[parts[0]] += 1
    return set(firsts) if len(firsts) >= min_locales else set()


def silo_of(url: str, depth: int = 1, locales: set[str] | None = None) -> str:
    parts = [x for x in url.split("/") if x]
    if locales and parts and parts[0] in locales:
        parts = parts[1:]          # bucket by content type, not by language
        if not parts:
            return "/(locale home)"
    if not parts:
        return "/"
    return "/" + "/".join(parts[:depth])


# --------------------------------------------------------------------------- commands
def cmd_crawl(a) -> dict:
    if a.url:
        g, info = build_live(a.url, a.max_pages, a.delay, a.timeout, a.keep_query, not a.ignore_robots)
    else:
        roots = []
        for spec in a.root:
            if "=" in spec:
                d, p = spec.split("=", 1)
            else:
                d, p = spec, "/"
            roots.append((d, p))
        rws = []
        for spec in a.rewrite:
            if "=" not in spec:
                return {"ok": False, "reason": f"--rewrite needs REGEX=REPL, got {spec!r}"}
            k, v = spec.split("=", 1)
            rws.append((k, v))
        g, info = build_offline(roots, a.keep_query, a.index_name, rws)
    info["taken_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    guard = graph_guard(g, info)
    payload = g.to_json(info)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    edges = sum(len(v) for v in g.adj.values())
    res = {"ok": guard is None, "mode": info["mode"], "pages": len(g.meta),
           "link_targets": len(g.urls), "edges": edges, "out": a.out}
    if guard:
        res.update(guard)
    return res


def cmd_inlinks(a) -> dict:
    g, d = load(a.graph)
    guard = graph_guard(g, d)
    if guard:
        return guard
    target = norm(a.url)
    if target not in g._uid:
        return {"ok": True, "url": target, "inlinks": 0,
                "note": "no page and no link in the graph points here"}
    tid = g._uid[target]
    sw = g.site_wide(a.boiler_threshold)
    inb = g.inbound()
    rows = []
    for src, anch in inb.get(tid, []):
        tag_boiler = any(d0 == tid and b for d0, _a0, b in g.adj[src])
        rows.append({"from": g.urls[src], "anchor": g.anchors[anch],
                     "boilerplate": bool(tag_boiler or tid in sw)})
    contextual = [r for r in rows if not r["boilerplate"]]
    return {"ok": True, "url": target, "inlinks": len(rows),
            "contextual_inlinks": len(contextual),
            "distinct_anchors": sorted({r["anchor"] for r in rows if r["anchor"]})[:20],
            "sample": rows[: a.limit]}


def cmd_orphans(a) -> dict:
    g, d = load(a.graph)
    guard = graph_guard(g, d)
    if guard:
        return guard
    sw = g.site_wide(a.boiler_threshold)
    # Furniture at TWO scales - see Graph.section_roots for why one is not enough.
    furniture = sw | g.section_roots()
    inb = g.inbound(contextual_only=a.contextual, site_wide=sw)
    inb_all = g.inbound() if a.contextual else inb
    orphans, near, hubs = [], [], []
    for uid, meta in g.meta.items():
        if not meta.get("exists", True):
            continue
        url = g.urls[uid]
        if a.ignore and re.search(a.ignore, url):
            continue
        n = len(inb.get(uid, []))
        if n > a.near:
            continue
        # A page that is ITSELF site furniture - in the global nav on nearly every
        # page - is the MOST reachable page on the site. Calling it an orphan is a
        # false positive, and the expensive kind: it pads the count with pages that
        # are fine, so the number stops meaning anything and the real orphan hides
        # in the list. Measured 2026-09-01 on combatskirmish.net: all 27 "contextual
        # orphans" were 5 global-nav hubs (/maps /modes /guides /how-to-play /tools)
        # plus 21 locale homes reached from the language switcher, each carrying
        # 160-4166 inbound links. Zero were unreachable. They are still LISTED,
        # because a hub with no editorial link into it is worth seeing - just not
        # counted as an orphan.
        if uid in furniture:
            hubs.append({"url": url, "contextual_inlinks": n,
                         "all_inlinks": len(inb_all.get(uid, []))})
        elif n == 0:
            orphans.append(url)
        else:
            near.append({"url": url, "inlinks": n})
    return {"ok": True, "mode": ("contextual-only" if a.contextual else "all-links"),
            "pages": len(g.meta), "orphans": len(orphans),
            "near_orphans_at_or_below": a.near, "near_orphans": len(near),
            "orphan_urls": sorted(orphans)[: a.limit],
            "near_orphan_urls": sorted(near, key=lambda r: r["inlinks"])[: a.limit],
            "nav_hubs_excluded": len(hubs),
            "nav_hub_urls": sorted(hubs, key=lambda r: r["url"])[: a.limit],
            "note": ("A page with links only from nav/footer is reachable but not "
                     "RECOMMENDED - that is the shape that leaves pages uncrawled. "
                     "Re-run with --contextual to see it." if not a.contextual else
                     "Counting body links only; nav/footer links excluded. "
                     "`nav_hub_urls` are pages that ARE site furniture themselves - "
                     "linked from the global nav sitewide - so they are highly "
                     "reachable despite zero body links, and are listed rather than "
                     "counted as orphans.")}


def cmd_silos(a) -> dict:
    """The report that surfaces the problem without being asked. Group pages by
    silo and show the inlink distribution; a silo whose median inbound link count
    is 1 while its neighbours sit in the tens is the 2026-08-31 bug, visible."""
    g, d = load(a.graph)
    guard = graph_guard(g, d)
    if guard:
        return guard
    sw = g.site_wide(a.boiler_threshold)
    inb_all = g.inbound()
    inb_ctx = g.inbound(contextual_only=True, site_wide=sw)
    locs = set() if a.keep_locales else locale_prefixes(g)
    buckets: dict[str, list[int]] = defaultdict(list)
    ctx: dict[str, list[int]] = defaultdict(list)
    ext: dict[str, list[int]] = defaultdict(list)
    alt: dict[str, list[int]] = defaultdict(list)
    words: dict[str, list[int]] = defaultdict(list)
    for uid, meta in g.meta.items():
        if not meta.get("exists", True):
            continue
        s = silo_of(g.urls[uid], a.depth, locs)
        buckets[s].append(len(inb_all.get(uid, [])))
        ctx[s].append(len(inb_ctx.get(uid, [])))
        # EXTERNAL-SILO inbound: contextual links arriving from a DIFFERENT silo.
        # This is the one that matters and it is not the same as the others.
        # Measured 2026-08-31 on combatskirmish.net: every /guides page had 16
        # contextual inlinks and looked healthy - but all 16 came from inside
        # /guides, because the guides cross-link each other. A silo that only
        # links to itself is an ISLAND: densely connected, and reachable from
        # nowhere a crawler already goes. Google had never fetched one of them.
        ext[s].append(sum(1 for src, _a in inb_ctx.get(uid, [])
                          if silo_of(g.urls[src], a.depth, locs) != s))
        alt[s].append(sum(1 for _src, an in inb_all.get(uid, []) if g.anchors[an] == "[hreflang]"))
        words[s].append(meta.get("words", 0))
    rows = []
    for s, vals in buckets.items():
        rows.append({
            "silo": s, "pages": len(vals),
            "inlinks_median": pct(vals, 0.5), "inlinks_p10": pct(vals, 0.10),
            "contextual_median": pct(ctx[s], 0.5),
            "external_silo_median": pct(ext[s], 0.5),
            "external_silo_zero_pages": sum(1 for v in ext[s] if v == 0),
            "entry_points": sum(1 for v in ext[s] if v > 0),
            # A silo is only an ISLAND if it has (almost) no ENTRY POINT - a page
            # inside it that something outside links to. Median external inbound
            # alone is not enough: /maps on combatskirmish.net has 3,435 pages whose
            # MEDIAN external inbound is 0, yet its hub links 2,590 of them and the
            # hub is in the site nav, so every map is one hop from a reachable page.
            # Calling that an island is a false positive - and Googlebot fetched 122
            # of those pages in 30 days, which settles it empirically.
            "island": (pct(ext[s], 0.5) == 0 and pct(ctx[s], 0.5) > 0
                       and pct(alt[s], 0.5) == 0
                       and sum(1 for v in ext[s] if v > 0) <= max(1, len(ext[s]) // 100)),
            "hreflang_inbound_median": pct(alt[s], 0.5),
            "words_median": pct(words[s], 0.5),
        })
    rows.sort(key=lambda r: (r["external_silo_median"], r["contextual_median"]))
    islands = [r["silo"] for r in rows if r["island"]]
    return {"ok": True, "silos": len(rows), "islands": islands,
            "locale_prefixes_folded": sorted(locs), "rows": rows,
            "reading": ("Read `entry_points` beside `external_silo_median`: a silo with a "
                        "well-linked HUB is reachable even when its leaves have no external "
                        "inbound of their own, because the hub is one hop away. `island` "
                        "requires BOTH a zero median AND essentially no entry point. "
                        "Sorted weakest-first by EXTERNAL-SILO inbound links - contextual "
                        "links arriving from outside the silo. Read that column, not "
                        "inlinks_median. `island: true` means the silo is well linked "
                        "INTERNALLY and reached from nowhere else: its pages cross-link "
                        "each other, so every naive inlink count looks healthy while a "
                        "crawler that has never entered the silo has no way in. That is "
                        "exactly what shipped on combatskirmish.net - 16 inlinks per guide, "
                        "all 16 from other guides - and it cost 12 pages nine days of being "
                        "literally unknown to Google.")}


def cmd_depth(a) -> dict:
    g, d = load(a.graph)
    guard = graph_guard(g, d)
    if guard:
        return guard
    start = norm(a.start)
    if start not in g._uid:
        return {"ok": False, "control_failed": True,
                "reason": f"start {start} is not in the graph at all"}
    sid = g._uid[start]
    # REFUSE rather than report a catastrophe. If the start node was never crawled
    # as a SOURCE it has no outbound edges, so BFS reaches nothing and the result
    # reads "unreachable: 3981" - which looks like the site is broken when in fact
    # the start page simply is not in the indexed tree. On combatskirmish.net the
    # homepage lives in frontend/src, not public/seo, so `--start /` hits this
    # every time. "Cannot ask" must never share a code path with "the answer is no".
    if not g.adj.get(sid):
        return {"ok": False, "control_failed": True,
                "reason": (f"start {start} has no outbound links in this graph - it was "
                           f"linked to but never crawled as a page, so depth cannot be "
                           f"computed from it. Pick a start that IS in the tree "
                           f"(e.g. --start /maps), or add its directory with --root."),
                "linked_from": len(g.inbound().get(sid, [])),
                "is_a_crawled_page": sid in g.meta}
    sw = g.site_wide(a.boiler_threshold) if a.contextual else set()
    dist = {g._uid[start]: 0}
    q = deque([g._uid[start]])
    while q:
        cur = q.popleft()
        for dst, _a0, boiler in g.adj.get(cur, []):
            if a.contextual and (boiler or dst in sw):
                continue
            if dst not in dist:
                dist[dst] = dist[cur] + 1
                q.append(dst)
    reached = {u: v for u, v in dist.items() if u in g.meta and g.meta[u].get("exists", True)}
    unreached = [g.urls[u] for u in g.meta if u not in dist and g.meta[u].get("exists", True)]
    hist: dict[int, int] = defaultdict(int)
    for v in reached.values():
        hist[v] += 1
    deep = sorted(({"url": g.urls[u], "depth": v} for u, v in reached.items() if v >= a.max_depth),
                  key=lambda r: -r["depth"])
    return {"ok": True, "start": start, "mode": ("contextual-only" if a.contextual else "all-links"),
            "reached": len(reached), "unreachable": len(unreached),
            "by_depth": {str(k): hist[k] for k in sorted(hist)},
            "deeper_than": a.max_depth, "deep_pages": deep[: a.limit],
            "unreachable_sample": sorted(unreached)[: a.limit]}


def cmd_broken(a) -> dict:
    g, d = load(a.graph)
    guard = graph_guard(g, d)
    if guard:
        return guard
    live = d.get("mode") == "live"
    inb = g.inbound()
    missing: dict[str, dict] = {}
    for uid, url in enumerate(g.urls):
        meta = g.meta.get(uid)
        if live:
            bad = meta is not None and meta.get("status") not in (200, None)
        else:
            bad = meta is None          # a link target with no file behind it
        if not bad:
            continue
        if a.ignore and re.search(a.ignore, url):
            continue
        srcs = inb.get(uid, [])
        missing[url] = {"url": url, "linked_from": len(srcs),
                        "status": (meta or {}).get("status"),
                        "sample_sources": [g.urls[s] for s, _ in srcs[:3]]}
    by_silo: dict[str, int] = defaultdict(int)
    for u in missing:
        by_silo[silo_of(u, 1)] += 1
    rows = sorted(missing.values(), key=lambda r: -r["linked_from"])
    return {"ok": True, "mode": d.get("mode"), "targets_without_a_page": len(missing),
            "by_silo": dict(sorted(by_silo.items(), key=lambda kv: -kv[1])),
            "worst": rows[: a.limit],
            "note": ("OFFLINE MODE: a target with no file is not necessarily broken - a "
                     "server-rendered route (/play, /servers/<x>, /leaderboard) has no file "
                     "on disk by design. Read by_silo first: a whole silo listed here is a "
                     "dynamic route, a lone URL under an otherwise-present silo is a real "
                     "dead link. Use --ignore to pin the known-dynamic ones."
                     if not live else
                     "LIVE MODE: these returned a non-200 status.")}


def cmd_report(a) -> dict:
    g, d = load(a.graph)
    guard = graph_guard(g, d)
    if guard:
        return guard
    sw = g.site_wide(a.boiler_threshold)
    inb_all, inb_ctx = g.inbound(), g.inbound(contextual_only=True, site_wide=sw)
    pages = [u for u in g.meta if g.meta[u].get("exists", True)]
    n_all = [len(inb_all.get(u, [])) for u in pages]
    n_ctx = [len(inb_ctx.get(u, [])) for u in pages]
    return {"ok": True, "mode": d.get("mode"), "taken_at": d.get("taken_at"),
            "pages": len(pages), "link_targets": len(g.urls),
            "edges": sum(len(v) for v in g.adj.values()),
            "inlinks": {"median": pct(n_all, .5), "p10": pct(n_all, .1), "p90": pct(n_all, .9)},
            "contextual_inlinks": {"median": pct(n_ctx, .5), "p10": pct(n_ctx, .1), "p90": pct(n_ctx, .9)},
            "pages_with_no_inlink": sum(1 for v in n_all if v == 0),
            "pages_with_no_contextual_inlink": sum(1 for v in n_ctx if v == 0),
            # Over-CONCENTRATION is the opposite failure to an orphan and just as
            # real. An internal link on more than ~30% of your pages is site
            # furniture, and search engines discount it as such - so a page linked
            # from 64% of the site is not 64% well-recommended, it is navigation.
            # Measured 2026-08-31: a discovery fix meant to spread link equity put
            # /guides/beginner-tips on 2,557 of 3,981 pages, pushing it over that
            # line while nine sibling guides sat at 17 inbound each. Nothing
            # reported it; it surfaced only as an odd entry in the orphan list,
            # because it had become furniture by its own success. Now it is stated.
            # share is over DISTINCT SOURCE PAGES, not edges: /play is linked
            # three times on a single page (CTA, nav, footer), so an edge count
            # gives a nonsensical 591% and hides the real concentration.
            "most_linked": [
                {"url": g.urls[u], "linking_pages": c,
                 "share_of_pages": round(c / max(1, len(pages)), 3),
                 "is_site_furniture": u in sw}
                for u, c in sorted(
                    ((u, len({src for src, _a in inb_all.get(u, [])})) for u in inb_all),
                    key=lambda kv: -kv[1])[:12]
            ],
            "furniture_targets": len(sw),
            "next": "sitegraph.py silos --graph <f>  # the weakest silo is the story"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("crawl", help="build the graph (offline tree or live BFS)")
    c.add_argument("--root", action="append", default=[],
                   help="offline: DIR or DIR=/url/prefix. Repeatable.")
    c.add_argument("--url", help="live: start URL (same origin only)")
    c.add_argument("--out", required=True, help="write the graph JSON here")
    c.add_argument("--max-pages", type=int, default=2000)
    c.add_argument("--delay", type=float, default=1.0, help="live: seconds between requests")
    c.add_argument("--timeout", type=int, default=20)
    c.add_argument("--keep-query", action="store_true")
    c.add_argument("--ignore-robots", action="store_true", help="live: NOT recommended")
    c.add_argument("--index-name", action="append", default=["index.html", "index.htm"])
    c.add_argument("--rewrite", action="append", default=[], metavar="REGEX=REPL",
                   help="rewrite mapped URLs, e.g. '^/landers/=/' when the generator writes "
                        "public/seo/landers/x.html but the server serves it at /x. Repeatable. "
                        "Without this those pages look like orphans, which is a mapping "
                        "artifact rather than a finding.")
    c.set_defaults(fn=cmd_crawl)

    for name, fn, extra in (("inlinks", cmd_inlinks, "url"), ("orphans", cmd_orphans, None),
                            ("silos", cmd_silos, None), ("depth", cmd_depth, None),
                            ("broken", cmd_broken, None), ("report", cmd_report, None)):
        p = sub.add_parser(name)
        p.add_argument("--graph", required=True)
        p.add_argument("--limit", type=int, default=25)
        p.add_argument("--boiler-threshold", type=float, default=0.3,
                       help="a link target present on more than this SHARE of pages is "
                            "site furniture, whatever markup wraps it (default 0.30)")
        if extra == "url":
            p.add_argument("url")
        if name in ("orphans", "depth"):
            p.add_argument("--contextual", action="store_true",
                           help="ignore nav/footer links - the reachable-but-not-recommended case")
        if name == "orphans":
            p.add_argument("--near", type=int, default=1, help="also list pages at or below N inlinks")
            p.add_argument("--ignore", help="regex of URLs to skip")
        if name == "silos":
            p.add_argument("--depth", type=int, default=1, help="path segments per silo bucket")
            p.add_argument("--keep-locales", action="store_true",
                           help="do NOT fold locale prefixes - bucket /es/maps separately from /maps")
        if name == "depth":
            p.add_argument("--start", default="/")
            p.add_argument("--max-depth", type=int, default=4)
        if name == "broken":
            p.add_argument("--ignore", help="regex of known-dynamic routes to skip")
        p.set_defaults(fn=fn)

    p = sub.add_parser("control", help="fire every parser guarantee at synthetic input")
    p.set_defaults(fn=lambda a: run_control())

    a = ap.parse_args()
    r = a.fn(a)
    out(r)
    sys.exit(0 if r.get("ok") else 1)


if __name__ == "__main__":
    main()
