#!/usr/bin/env python3
"""Controls for the referrer classifier.

Each case is a row that appeared in a REAL report on 2026-09-01 and was counted
as a backlink when it was nothing of the kind. Of 40 referring domains, 34
survived the naive filter and roughly 5 were real links.

  1. an attack probe carries a forged Referer, so `wordpress.org -> /wp-login.php`
     reads as an editorial link from wordpress.org
  2. a second domain the same owner runs is a self-referral, not a backlink
  3. Cloudflare IPs on cPanel ports are textbook referrer spam
  4. a hotlinked favicon is not a page visit
  5. and the ones that ARE real must survive all of the above
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backlinks as bl  # noqa: E402

FAILS = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        FAILS.append(name)


OWN = {"combatskirmish.net", "cs16.net"}


def c(host, landing):
    return bl.classify_referrer(host, landing, OWN)


print("1. attack probes are not referrals")
check("wordpress.org -> /wp-login.php is a probe",  c("wordpress.org", "/wp-login.php") == "probe")
check("a site -> /xmlrpc.php is a probe",           c("cristodelaesperanza.org", "//xmlrpc.php") == "probe")
check("-> /.env is a probe",                        c("example.com", "/.env") == "probe")
check("but wordpress.org -> / is GENUINE",          c("wordpress.org", "/") == "genuine")

print("\n2. a second owned domain is self, at any subdomain or port")
check("cs16.net",              c("cs16.net", "/") == "self")
check("ms.cs16.net:27010",     c("ms.cs16.net:27010", "/ring") == "self")
check("cs16.net:443",          c("cs16.net:443", "/") == "self")
check("trailing-dot form",     c("cs16.net.", "/") == "self")
check("an unrelated .net is NOT self", c("someothersite.net", "/") == "genuine")

print("\n3. referrer spam")
check("bare IP",               c("172.67.202.220", "/") == "spam")
check("IP on a cPanel port",   c("188.114.97.2:2082", "/") == "spam")
check("hostname on a cPanel port", c("shady.example:8880", "/") == "spam")
check("throwaway workers.dev", c("odd-block-9da6.e0yddn00.workers.dev", "/") == "spam")
check("a normal port is NOT spam", c("forum.example.com", "/") == "genuine")

print("\n4. hotlinked assets are not page visits")
check("apple-touch-icon",      c("1milliontaps.lol", "/frontend/assets/apple-touch-icon.png") == "asset")
check("an API endpoint",       c("sbox.facepunch.com", "/api/random-name") == "asset")
check("a map image",           c("someblog.com", "/mi/de_dust2.jpg") == "asset")

print("\n5. the real links survive - the whole point")
check("reddit.com -> /",       c("reddit.com", "/") == "genuine")
check("seedhub.cc -> /zh/",    c("seedhub.cc", "/zh/") == "genuine")
check("steamcommunity.com",    c("steamcommunity.com", "/") == "genuine")
check("a guide landing",       c("somegamingsite.com", "/guides/bunny-hop") == "genuine")

print("\n6. precedence: self beats probe, probe beats asset")
check("own domain hitting a probe path is self, not probe",
      c("cs16.net", "/wp-login.php") == "self")
check("a probe on an asset-looking path is still a probe",
      c("evil.example", "/vendor/phpunit") == "probe")

print("\n6b. search engines are never backlinks, including the short domains")
def is_search(h):
    return any(x in h for x in bl.SEARCH_HOSTS)
check("ya.ru is a search engine, not a link (the `yandex.` prefix misses it)", is_search("ya.ru"))
check("kagi.com is a search engine", is_search("kagi.com"))
check("yandex.ru still matches",     is_search("yandex.ru"))
check("CONTROL: a real referrer is NOT matched as search", not is_search("reddit.com"))
check("CONTROL: seedhub.cc is NOT matched as search",      not is_search("seedhub.cc"))

print("\n7. every referrers flag survives the --remote round trip")
# A --remote run reconstructs the argv by hand, so a flag added to the parser and
# forgotten there is SILENTLY DROPPED - no error, and only on remote runs, which
# is how this command is normally used. That is exactly how --own shipped inert.
import re
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "backlinks.py"), encoding="utf-8").read()
block = src.split('args = ["referrers"]', 1)[1].split("cmd = [\"ssh\"", 1)[0]
sub = src.split('s.add_argument("-f", "--file"', 1)[1].split("s.add_argument(\"--domain\"", 1)[0]
declared = set(re.findall(r'"(--[a-z-]+)"', sub))
# Flags that steer the remote call itself must NOT be forwarded to the far side.
remote_only = {"--remote", "--ssh-key", "--timeout"}
missing = sorted(f for f in declared - remote_only if f'"{f}"' not in block)
check(f"no referrers flag is dropped on --remote (missing: {missing})", not missing)
check("the control itself found real flags to check", len(declared - remote_only) >= 5)

print()
print("\n8. reclaim - a real link that lands on a dead page is a link being wasted")
import subprocess as _sp, tempfile as _tf, json as _js
from pathlib import Path
UA_H = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
def _ln(path, status, ref, n=1):
    return "".join(f'1.2.3.{i} - - [01/Oct/2026:10:00:0{i % 10} +0000] "GET {path} HTTP/1.1" '
                   f'{status} 512 "{ref}" "{UA_H}"\n' for i in range(n))
with _tf.TemporaryDirectory() as td:
    log = Path(td) / "access.log"
    log.write_text(_ln("/old-guide", 404, "https://forum.test/thread/9", 3)
                   + _ln("/moved", 301, "https://blog.test/post", 2)
                   + _ln("/fine", 200, "https://blog.test/post", 4)
                   + _ln("/cached", 304, "https://blog.test/post", 1))
    out = _sp.run([sys.executable, str(Path(__file__).parent / "backlinks.py"), "referrers",
                   "--file", str(log), "--format", "combined", "--site", "oursite.test"],
                  capture_output=True, text=True)
    d = _js.loads(out.stdout)
    rc = {r["path"]: r for r in d.get("reclaim_candidates", [])}
    check("a genuine referral landing on a 404 is a reclaim candidate",
          rc.get("/old-guide", {}).get("status") == 404 and rc["/old-guide"]["visits"] == 3)
    check("its referring domain is named", "forum.test" in rc.get("/old-guide", {}).get("referrers", []))
    check("a referral landing on a redirect is listed as redirected", rc.get("/moved", {}).get("status") == 301)
    check("CONTROL: 200 and 304 landings are not candidates", "/fine" not in rc and "/cached" not in rc)
    saved = Path(td) / "ref.json"
    saved.write_text(out.stdout)
    import backlinks as _bl
    sm = ["https://oursite.test/guides/old-guide-2026", "https://oursite.test/maps/dust2"]
    def fake_chain(u):
        return [{"url": u, "status": 404, "location": None, "error": None}]
    rec = _bl.reclaim_rows(d["reclaim_candidates"], "https://oursite.test", sm, chain=fake_chain)
    r0 = next(r for r in rec if r["path"] == "/old-guide")
    check("the still-dead target gets a 301 suggestion by slug", r0["suggest_301_to"] ==
          "https://oursite.test/guides/old-guide-2026")
    def fixed_chain(u):
        return [{"url": u, "status": 200, "location": None, "error": None}]
    rec = _bl.reclaim_rows(d["reclaim_candidates"], "https://oursite.test", sm, chain=fixed_chain)
    check("CONTROL: a target that answers 200 now is already reclaimed",
          next(r for r in rec if r["path"] == "/old-guide")["state"] == "already_fixed")

print("\n9. unlinked mentions - a page that names us without linking is a prospect")
import backlinks as _bl
D, B = "nova.app", "Nova"
c = _bl.classify_mention_page
r = c('<p>We tried <a href="https://www.nova.app/x" rel="nofollow ugc">Nova</a>.</p>',
      "https://blog.test/p", D, B)
check("a link to us is `linked`, with its rel kept", r["state"] == "linked" and r["rels"] == ["nofollow", "ugc"])
r = c("<p>Nova is the one we use for this.</p>", "https://blog.test/p", D, B)
check("a name without a link is `mention_only`", r["state"] == "mention_only")
r = c("<p>Casanova was a film.</p><!-- Nova -->", "https://blog.test/p", D, B)
check("CONTROL: a substring or a comment is not a mention", r["state"] == "absent")
r = c('<script>var x="Nova"</script><p>nothing</p>', "https://blog.test/p", D, B)
check("CONTROL: a script string is not a mention", r["state"] == "absent")
check("medium/substack subdomains group to one publisher each",
      _bl.publisher_key("https://alice.substack.com/p/x") == "alice.substack.com"
      and _bl.publisher_key("https://medium.com/@bob/x") == "medium.com/@bob"
      and _bl.publisher_key("https://www.blog.test/a") == "blog.test")
check("an IP/DNS/scanner listing is a machine listing, not a prospect",
      _bl.machine_listing("https://ipv4.bgp.he.net/ip/2606:4700::1")
      and _bl.machine_listing("https://stackray.app/targets/abc/scans")
      and _bl.machine_listing("https://www.whois.com/whois/nova.app")
      and _bl.machine_listing("https://urlscan.io/result/x/"))
check("CONTROL: an ordinary blog post is not a machine listing",
      not _bl.machine_listing("https://blog.test/2026/10/our-favourite-tools"))

if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("all backlinks tests passed")
