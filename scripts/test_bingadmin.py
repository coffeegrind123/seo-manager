#!/usr/bin/env python3
"""Controls for bingadmin.py - account configuration writes.

Every case is a way a write here does damage or reports a success it cannot prove:

  1. Bing answers null on success AND on a no-op (measured 2026-10-18), so a
     remove of something absent must be refused before sending, never "done".
  2. A write is `confirmed` only when the read-back shows it; a null answer with
     no change is `not_reflected`, and a failed read-back is `unverifiable`.
  3. Destructive calls need more than --yes where one typo costs a site: the last
     administrator cannot be removed; RemoveSite needs --confirm-site.
  4. A retired endpoint (404) is reported as retired, never as "none set".
  5. `state` keeps a failing section visible instead of sinking the report.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bing  # noqa: E402
import bingadmin as ba  # noqa: E402

FAILS = []
SITE = "https://x.net/"
HERE = os.path.dirname(os.path.abspath(__file__))
ENV = dict(os.environ, BING_WEBMASTER_API_KEY="test-key-not-used")


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        FAILS.append(name)


class Account:
    """A fake account: GETs read from `state`, POSTs mutate it the way Bing would
    (or not at all, when `inert` - Bing's null-for-a-no-op)."""

    def __init__(self, **state):
        self.s = {"GetUserSites": [{"Url": SITE, "IsVerified": True, "AuthenticationCode": "AC",
                                    "DnsVerificationCode": "h.x.net"}],
                  "GetSiteRoles": [], "GetBlockedUrls": [], "GetActivePagePreviewBlocks": [],
                  "GetQueryParameters": [], "GetCountryRegionSettings": [],
                  "GetCrawlSettings": {"CrawlRate": [5] * 24, "CrawlBoostAvailable": False,
                                       "CrawlBoostEnabled": False},
                  "GetConnectedPages": [], "GetDeepLinkBlocks": []}
        self.s.update(state)
        self.posts, self.inert, self.fail = [], False, set()

    def call(self, method, key, **kw):
        if method in self.fail:
            return {"ok": False, "error": "x", "error_code": 1}
        if method in ba.RETIRED:
            return {"ok": False, "error": "method_not_served", "http_status": 404}
        return {"ok": True, "d": self.s.get(method, [])}

    def post(self, method, key, p):
        self.posts.append((method, p))
        if method in ba.RETIRED:
            return {"ok": False, "error": "method_not_served", "http_status": 404}
        if self.inert:
            return {"ok": True, "d": None}
        s = self.s
        if method == "AddQueryParameter":
            s["GetQueryParameters"].append({"Parameter": p["queryParameter"], "IsEnabled": True})
        elif method == "RemoveQueryParameter":
            s["GetQueryParameters"] = [x for x in s["GetQueryParameters"] if x["Parameter"] != p["queryParameter"]]
        elif method == "EnableDisableQueryParameter":
            for x in s["GetQueryParameters"]:
                if x["Parameter"] == p["queryParameter"]:
                    x["IsEnabled"] = p["isEnabled"]
        elif method == "AddBlockedUrl":
            s["GetBlockedUrls"].append(dict(p["blockedUrl"]))
        elif method == "RemoveBlockedUrl":
            s["GetBlockedUrls"] = [x for x in s["GetBlockedUrls"] if x["Url"] != p["blockedUrl"]["Url"]]
        elif method == "SaveCrawlSettings":
            cs = p["crawlSettings"]
            s["GetCrawlSettings"] = {"CrawlRate": cs["CrawlRate"], "CrawlBoostAvailable": cs["CrawlBoostAvailable"],
                                     "CrawlBoostEnabled": cs["CrawlBoostEnabled"]}
        elif method == "RemoveSite":
            s["GetUserSites"] = [x for x in s["GetUserSites"] if x["Url"] != p["siteUrl"]]
        elif method == "AddCountryRegionSettings":
            st = p["settings"]
            s["GetCountryRegionSettings"].append({"Url": st["Url"], "TwoLetterIsoCountryCode": st["TwoLetterIsoCountryCode"],
                                                  "Type": st["Type"]})
        return {"ok": True, "d": None}


def use(acct):
    bing.call, bing.post = acct.call, acct.post
    return acct


print("1. a no-op write is refused before the network")
acc = use(Account())
check("removing a parameter that is not set is refused",
      ba.param_change("k", SITE, "utm", "remove", True, None)["error"] == "no_such_parameter")
check("removing a block that does not exist is refused",
      ba.block_remove("k", SITE, SITE + "a", True, None)["error"] == "not_blocked")
check("removing a region that does not exist is refused",
      ba.region_change("k", SITE, "de", "directory", SITE + "de/", "remove", True, None)["error"] == "no_such_region")
check("and nothing was sent for any of them", acc.posts == [])
acc = use(Account(GetQueryParameters=[{"Parameter": "utm", "IsEnabled": True}]))
check("adding a parameter that exists is refused",
      ba.param_change("k", SITE, "utm", "add", True, None)["error"] == "parameter_exists")
check("enabling an enabled parameter is no_change",
      ba.param_change("k", SITE, "utm", "enable", True, None)["error"] == "no_change")
check("a bad parameter name is refused", ba.param_change("k", SITE, "a=b", "add", True, None)["error"] == "bad_parameter")
check("an off-site block is refused", ba.block_add("k", SITE, "https://evil.example/", False, "cache_only",
                                                   True, None)["error"] == "url_off_site")
check("still nothing sent", acc.posts == [])

print("\n2. dry run, confirmed, not_reflected, unverifiable")
acc = use(Account())
r = ba.param_change("k", SITE, "utm", "add", False, None)
check("no --yes is a dry run showing the payload",
      r["dry_run"] is True and r["payload"] == {"siteUrl": SITE, "queryParameter": "utm"} and acc.posts == [])
r = ba.param_change("k", SITE, "utm", "add", True, None)
check("--yes + a read-back showing it = confirmed", r["verified"] == "confirmed" and r["ok"])
r = ba.param_change("k", SITE, "utm", "disable", True, None)
check("disable sends isEnabled false and is confirmed",
      acc.posts[-1] == ("EnableDisableQueryParameter", {"siteUrl": SITE, "queryParameter": "utm", "isEnabled": False})
      and r["verified"] == "confirmed")
acc = use(Account())
acc.inert = True
r = ba.param_change("k", SITE, "utm", "add", True, None)
check("Bing's null with no change is not_reflected and NOT ok", r["verified"] == "not_reflected" and r["ok"] is False)
acc = use(Account())
orig = ba.params
calls = {"n": 0}


def flaky(key, site):
    calls["n"] += 1
    return orig(key, site) if calls["n"] == 1 else {"ok": False, "error": "x"}


ba.params = flaky
r = ba.param_change("k", SITE, "utm", "add", True, None)
ba.params = orig
check("a failed read-back is unverifiable, never confirmed", r["verified"] == "unverifiable")

print("\n3. blocks: the removed row is Bing's own, so enums round-trip")
acc = use(Account())
r = ba.block_add("k", SITE, SITE + "old/", True, "full_removal", True, None)
check("a full-removal directory block warns about EVERY url under it", "EVERY url" in r["warning"])
check("and is confirmed", r["verified"] == "confirmed")
held = acc.s["GetBlockedUrls"][0]
r = ba.block_remove("k", SITE, SITE + "old/", True, None)
check("remove sends the row exactly as Bing returned it", acc.posts[-1][1]["blockedUrl"] == held)
check("and is confirmed gone", r["verified"] == "confirmed")

print("\n4. the destructive guards")
acc = use(Account(GetSiteRoles=[{"Email": "me@x", "Role": 0, "Expired": False}]))
r = ba.role_remove("k", SITE, "ME@x", True, None)
check("the only administrator cannot be removed", r["error"] == "last_administrator" and acc.posts == [])
check("a role that does not exist is refused", ba.role_remove("k", SITE, "who@x", True, None)["error"] == "no_such_role")
acc = use(Account())
r = ba.site_remove("k", SITE, True, "https://x.net", None)
check("site-remove refuses a --confirm-site that is not the exact url",
      r["error"] == "confirm_site_mismatch" and acc.posts == [])
r = ba.site_remove("k", SITE, True, SITE, None)
check("CONTROL the exact url removes and is confirmed", r["verified"] == "confirmed")
acc = use(Account())
check("adding a site already on the account is refused, with its verification",
      ba.site_add("k", SITE, True, None)["error"] == "already_on_account")

print("\n5. crawl rate: read-modify-write, no_change, boost")
acc = use(Account())
r = ba.crawlrate_set("k", SITE, "0-5=8", None, True, None)
check("only the named hours change", acc.s["GetCrawlSettings"]["CrawlRate"] == [8] * 6 + [5] * 18)
check("and it is confirmed", r["verified"] == "confirmed")
check("the same settings again are no_change",
      ba.crawlrate_set("k", SITE, "0-5=8", None, True, None)["error"] == "no_change")
check("boost where Bing does not offer it is refused",
      ba.crawlrate_set("k", SITE, None, True, True, None)["error"] == "boost_unavailable")
check("a rate of 11 is refused", ba.crawlrate_set("k", SITE, "11", None, True, None)["error"] == "bad_rate")

print("\n6. regions and retired endpoints")
acc = use(Account())
r = ba.region_change("k", SITE, "DE", "directory", SITE + "de/", "add", True, None)
check("the country is lower-cased and the add confirmed", r["verified"] == "confirmed"
      and acc.s["GetCountryRegionSettings"][0]["TwoLetterIsoCountryCode"] == "de")
check("a three-letter country is refused",
      ba.region_change("k", SITE, "deu", "page", SITE, "add", True, None)["error"] == "bad_country")
r = ba.sitemoves("k", SITE)
check("a 404 endpoint says RETIRED, not 'no site moves'", r.get("retired") is True and r["ok"] is False)
r = ba.sitemove("k", SITE, SITE, "https://y.net/", "domain", "global", True, None)
check("a retired WRITE is retired too, and never `confirmed`", r.get("retired") is True and r.get("verified") is None)

print("\n7. state: one failing section stays visible")
acc = use(Account(GetBlockedUrls=[{"Url": SITE + "a", "EntityType": 0, "RequestType": 1}]))
acc.fail = {"GetCountryRegionSettings"}
r = ba.state("k", SITE)
check("the failed section is named, not dropped", r["failed_sections"] == ["regions"])
check("a block on the account is called out", any("block" in x for x in r["attention"]))
check("raw rows kept for writes are not printed", "_raw_blocked" not in r["sections"]["blocks"])

print("\n8. CLI refusals precede the network")


def cli(*args):
    p = subprocess.run([sys.executable, os.path.join(HERE, "bingadmin.py"), *args],
                       capture_output=True, text=True, env=ENV, timeout=60)
    try:
        return p.returncode, json.loads(p.stdout)
    except Exception:
        return p.returncode, {"_stdout": p.stdout[:200], "_stderr": p.stderr[:200]}


rc, out = cli("site-remove", "--yes")
check("site management never infers the site", out.get("error") == "pass --site" and rc != 0)
rc, out = cli("crawlrate-set")
check("crawlrate-set with nothing to set is refused", out.get("error") == "nothing_to_set")
rc, out = cli("control")
check("the control passes", rc == 0 and out.get("ok") is True)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("all bingadmin tests passed")
