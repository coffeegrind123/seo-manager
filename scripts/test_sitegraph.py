#!/usr/bin/env python3
"""Controls for sitegraph.py.

Every case below is a bug that existed during the build and was caught, or a
distinction that silently produces a confident wrong answer. In order:

  1. tag-only boilerplate detection missed a nav rendered as <p class="silonav">,
     so 3,900 furniture links counted as editorial and the broken silo looked fine
  2. self-referential hreflang counted as an inbound link, so every English-only
     silo looked reachable from elsewhere
  3. a silo that only links to itself has healthy inlink counts and is reachable
     from nowhere - the actual 2026-08-31 failure
  4. a start node that was never crawled reports "unreachable: everything", which
     reads as a catastrophic site fault rather than a bad --start
  5. zero extracted links must refuse a verdict, not report every page an orphan
  6. a page that IS the global nav hub is the most reachable page on the site, so
     counting it as a contextual orphan pads the number until it means nothing
"""
import json
import subprocess
import sys
import tempfile
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sitegraph as sg  # noqa: E402

FAILS = []


def check(name, cond):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}")
        FAILS.append(name)


def build(pages: dict[str, str]) -> sg.Graph:
    """pages: url -> html. Builds a graph the way the offline walker would."""
    g = sg.Graph()
    for url, html in pages.items():
        p = sg.parse(html)
        src = g.uid(url)
        g.meta[src] = {"exists": True, "words": p.words, "title": p.title}
        for href in p.alternates:
            g.adj[src].append((g.uid(sg.norm(href)), g.aid("[hreflang]"), True))
        for href, anchor, boiler in p.links:
            if not sg.is_internal(href, None):
                continue
            g.adj[src].append((g.uid(sg.norm(href)), g.aid(anchor), boiler))
    return g


print("parser + url controls")
r = sg.run_control()
check("run_control all green", r["ok"])
check("norm collapses double slashes", sg.norm("/a//b") == "/a/b")
check("norm handles query drop", sg.norm("/a?b=1") == "/a")
check("norm keeps query when asked", sg.norm("/a?b=1", keep_query=True) == "/a?b=1")

print("\n1. statistical boilerplate beats tag-only detection")
# A nav rendered as a plain <p> - exactly combatskirmish.net's `silonav`.
pages = {f"/p{i}": '<p class="silonav"><a href="/hub">Hub</a></p>' for i in range(80)}
pages["/p0"] += '<a href="/only">the one editorial link</a>'   # linked from ONE page
pages["/hub"] = "<a href='/p0'>x</a>"
pages["/only"] = "<p>nothing</p>"
g = build(pages)
sw = g.site_wide(0.3)
check("frequent target flagged site-wide despite <p> markup", g._uid["/hub"] in sw)
check("rare target NOT flagged site-wide", g._uid["/only"] not in sw)
ctx = g.inbound(contextual_only=True, site_wide=sw)
check("furniture excluded from contextual inbound", len(ctx.get(g._uid["/hub"], [])) == 0)
check("editorial link kept in contextual inbound", len(ctx.get(g._uid["/only"], [])) == 1)

print("\n2. self-referential hreflang is not an inbound link")
g = build({"/en/x": '<link rel="alternate" hreflang="en" href="/en/x">'
                    '<link rel="alternate" hreflang="x-default" href="/en/x">'})
check("self-loop yields zero inbound", len(g.inbound().get(g._uid["/en/x"], [])) == 0)
g2 = build({"/en/x": '<link rel="alternate" hreflang="es" href="/es/x">',
            "/es/x": '<link rel="alternate" hreflang="en" href="/en/x">'})
check("cross-locale alternate IS an inbound link",
      len(g2.inbound().get(g2._uid["/es/x"], [])) == 1)

print("\n3. island silo: healthy inlinks, reachable from nowhere")
# Five pages that cross-link each other and nothing else points in.
isl = {f"/iso/{i}": "".join(f'<a href="/iso/{j}">sib</a>' for j in range(5) if j != i)
       for i in range(5)}
isl["/other/a"] = '<a href="/other/b">x</a>'
isl["/other/b"] = '<a href="/other/a">x</a><a href="/iso/0">into the island</a>'
g = build(isl)
inb = g.inbound(contextual_only=True, site_wide=g.site_wide(0.3))
ext_for = lambda u: sum(1 for s, _a in inb.get(g._uid[u], [])
                        if sg.silo_of(g.urls[s]) != sg.silo_of(u))
check("island page has healthy RAW inbound", len(inb.get(g._uid["/iso/3"], [])) == 4)
check("island page has ZERO external-silo inbound", ext_for("/iso/3") == 0)
check("the one linked-in page is not an island", ext_for("/iso/0") == 1)

print("\n3a. a silo with a well-linked HUB is not an island")
# 60 leaves whose only inbound is the hub, plus a hub that IS linked from outside.
# Leaves must NOT all link back to the hub, or the frequency rule correctly calls
# the hub furniture and the silo has no entry point at all. The first version of
# this test got that wrong. It is also the real distinction: /maps on the live
# site earns its 992 entry points from MODE pages linking individual maps, not
# from its own hub link, which IS furniture.
hub = {f"/big/{i}": f'<a href="/big/{(i+1)%60}">sib</a>' for i in range(60)}
hub["/big/hub"] = "".join(f'<a href="/big/{i}">leaf</a>' for i in range(60))
for i in range(40):
    hub[f"/else/{i}"] = '<a href="/else/0">x</a>'
hub["/else/0"] = '<a href="/big/hub">the hub</a>'
g = build(hub)
sw = g.site_wide(0.3)
inb = g.inbound(contextual_only=True, site_wide=sw)
ext = [sum(1 for s2, _a in inb.get(g._uid[u], []) if sg.silo_of(g.urls[s2]) != "/big")
       for u in hub if u.startswith("/big/")]
entry = sum(1 for v in ext if v > 0)
check("leaves have zero external inbound", sorted(ext)[len(ext) // 2] == 0)
check("but the silo HAS an entry point (the hub)", entry >= 1)
island_at = lambda e, n: e <= max(1, n // 100)
# A SINGLE entry point still counts as an island, and that is deliberate rather
# than a rounding accident: pre-fix /guides had exactly one and Google had still
# never crawled any of its pages nine days in. One link into a silo is not a
# crawl path. Plenty of entry points - /maps had 992 - is.
check("one entry point is still an island (the measured /guides case)",
      island_at(1, 60) is True)
check("many entry points is not an island (the measured /maps case)",
      island_at(992, 3435) is False)

print("\n3b. locale prefixes fold into the content silo")
loc = {}
for lg in ("es", "de", "fr", "ar"):
    for i in range(15):
        loc[f"/{lg}/maps/m{i}"] = f'<a href="/{lg}/maps/m{(i+1)%15}">sib</a>'
for i in range(15):
    loc[f"/maps/m{i}"] = f'<a href="/maps/m{(i+1)%15}">sib</a>'
g = build(loc)
locs = sg.locale_prefixes(g)
check("detects the four locale prefixes", locs == {"es", "de", "fr", "ar"})
check("localized map folds into /maps", sg.silo_of("/ar/maps/m3", 1, locs) == "/maps")
check("english map stays /maps", sg.silo_of("/maps/m3", 1, locs) == "/maps")
check("without folding they differ", sg.silo_of("/ar/maps/m3", 1, set()) == "/ar")
g2 = build({"/ui/a": '<a href="/ui/b">x</a>', "/ui/b": '<a href="/ui/a">y</a>'})
check("a lone two-letter silo is NOT treated as a locale", sg.locale_prefixes(g2) == set())

print("\n4. depth refuses on a start that was never crawled")
with tempfile.TemporaryDirectory() as td:
    gp = os.path.join(td, "g.json")
    g = build({"/real": '<a href="/never-crawled">x</a>'})
    with open(gp, "w") as fh:
        json.dump(g.to_json({"mode": "offline"}), fh)
    p = subprocess.run([sys.executable, os.path.join(os.path.dirname(sg.__file__), "sitegraph.py"),
                        "depth", "--graph", gp, "--start", "/never-crawled"],
                       capture_output=True, text=True)
    d = json.loads(p.stdout)
    check("refuses rather than reporting mass unreachability", d.get("control_failed") is True)
    check("refusal exits non-zero", p.returncode != 0)

print("\n5. a graph with no links refuses a verdict")
g = build({"/a": "<p>no links</p>", "/b": "<p>none either</p>"})
guard = sg.graph_guard(g, {})
check("zero-edge graph refuses", guard is not None and guard.get("control_failed"))
g = build({"/a": '<a href="/b">x</a>', "/b": '<a href="/a">y</a>'})
check("healthy graph passes the guard", sg.graph_guard(g, {}) is None)

print("\n6. offline path mapping")
with tempfile.TemporaryDirectory() as td:
    os.makedirs(os.path.join(td, "guides"))
    os.makedirs(os.path.join(td, "how-to-play"))
    open(os.path.join(td, "guides", "bhop.html"), "w").write('<a href="/x">x</a>')
    open(os.path.join(td, "how-to-play", "index.html"), "w").write('<a href="/y">y</a>')
    g, info = sg.build_offline([(td, "/")], False, ["index.html"])
    check("file -> /guides/bhop", "/guides/bhop" in g._uid)
    check("index.html -> /how-to-play", "/how-to-play" in g._uid)
    g, _ = sg.build_offline([(td, "/")], False, ["index.html"], [(r"^/guides/", "/")])
    check("--rewrite remaps the served path", "/bhop" in g._uid and "/guides/bhop" not in g._uid)

print("\n7. a global-nav hub is not a contextual orphan")
# 80 leaf pages whose nav links the hub, exactly like combatskirmish.net's silonav.
# The hub gets 80 inbound links and ZERO body links - the shape that made every
# one of the site's 27 "contextual orphans" a false positive on 2026-09-01.
pages = {f"/p{i}": '<p class="silonav"><a href="/hub">Hub</a></p>' for i in range(80)}
# The hub body-links its leaves, so the leaves are properly reachable and the only
# two interesting pages left are the hub itself and the genuinely unlinked one.
pages["/hub"] = ("<p>the hub, linked from every nav and from no article body</p>"
                 + "".join(f'<p><a href="/p{i}">leaf {i}</a></p>' for i in range(80)))
pages["/lost"] = "<p>genuinely linked from nowhere</p>"
g = build(pages)


class A:
    graph = None
    contextual = True
    near = 0
    limit = 200
    ignore = None
    boiler_threshold = 0.3


with tempfile.TemporaryDirectory() as td:
    f = os.path.join(td, "g.json")
    with open(f, "w", encoding="utf-8") as fh:
        json.dump(g.to_json({"mode": "offline"}), fh)
    A.graph = f
    out = sg.cmd_orphans(A)

check("the nav hub is NOT counted as an orphan", "/hub" not in out["orphan_urls"])
check("the nav hub is still listed for review",
      any(r["url"] == "/hub" for r in out["nav_hub_urls"]))
check("the hub's real reachability is reported",
      any(r["url"] == "/hub" and r["all_inlinks"] == 80 for r in out["nav_hub_urls"]))
check("the genuinely unlinked page IS still an orphan", "/lost" in out["orphan_urls"])
check("orphan count excludes the hub", out["orphans"] == 1)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("all sitegraph tests passed")
