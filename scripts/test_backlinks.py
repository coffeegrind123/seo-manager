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
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("all backlinks tests passed")
