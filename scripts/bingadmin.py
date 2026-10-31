#!/usr/bin/env python3
"""bingadmin.py - Bing Webmaster ACCOUNT CONFIGURATION, read and write.

`bing.py` measures. This changes the account: which sites are on it, who can
see them, which URLs Bing is told to drop, which query parameters it ignores,
which country a path targets, how fast it may crawl. All of it is state that
lives in Bing and in no file in the repo, survives every deploy, and is
invisible to any crawl of the site - so `state` reads all of it in one call.

  state          every configuration read below, in one report
  sites          sites on the account, with each one's verification codes
  site-add       AddSite, then print the three ways to verify it
  site-verify    VerifySite - ask Bing to check the meta tag / file / CNAME now
  site-remove    RemoveSite. Needs --yes AND --confirm-site <the same url>
  roles          GetSiteRoles - who has access, as what
  role-add       AddSiteRoles - delegate access (administrator/read-only/read-write)
  role-remove    RemoveSiteRole. Refuses to remove the last administrator
  blocks         blocked URLs + page-preview blocks, fully decoded
  block-add      AddBlockedUrl - drop a page or directory from Bing results
  block-remove   RemoveBlockedUrl
  preview-add    AddPagePreviewBlock - suppress the snippet/preview for a URL
  preview-remove RemovePagePreviewBlock
  params         GetQueryParameters - parameters Bing treats as ignorable
  param-add / param-remove / param-enable / param-disable
  regions        GetCountryRegionSettings - geo-targeting by page/dir/domain
  region-add / region-remove
  crawlrate-set  SaveCrawlSettings - the per-hour crawl-rate cap and crawl boost
  connected      GetConnectedPages - social/profile pages tied to the site
  connected-add  AddConnectedPage
  deeplink-blocks / deeplink-block-add / deeplink-block-remove
  sitemoves / sitemove / deeplinks / deeplink-algo / deeplink-weight
                 RETIRED - every one answered HTTP 404 on 2026-10-18, the same
                 answer a made-up method name gets. Kept so the day Bing serves
                 them again they work, and so the 404 is reported as "retired",
                 never as "this site has none".

THE RULE EVERY WRITE HERE FOLLOWS. Bing answers {"d": null} on success AND on
a no-op: removing a block, parameter, feed or region that does not exist
returns the identical null (measured 2026-10-18 on eight Remove* methods), and
AddSite / AddConnectedPage returned null for a site the account does not own.
So the response proves nothing. Every write here:

  1. reads the current state first, and refuses a write that would be a no-op
     (removing what is not there, adding what already is),
  2. is a DRY RUN until --yes, printing the exact payload it would send,
  3. reads the state back afterwards: `verified: confirmed` or `not_reflected`,
  4. appends a receipt to <root>/.seo/bing.jsonl when that directory exists.

Request bodies were confirmed against the live API with no-op calls: Bing
rejects an unknown field name or a malformed date with InvalidParameter, and
accepted these shapes. AddSiteRoles / RemoveSiteRole could not be probed that
way (authorisation is checked before the body), so their shapes are merj's.

Stdlib only. Key from BING_WEBMASTER_API_KEY or ~/.bing_webmaster_key.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bing  # noqa: E402
from controls import Controls  # noqa: E402

API_NS = "Microsoft.Bing.Webmaster.Api"

ROLE_NAMES = {0: "administrator", 1: "read_only", 2: "read_write"}
# role -> (isAdministrator, isReadOnly) for AddSiteRoles
ROLE_FLAGS = {"administrator": (True, False), "read_only": (False, True), "read_write": (False, False)}
BLOCK_ENTITY = {0: "page", 1: "directory"}
BLOCK_REQUEST = {0: "cache_only", 1: "full_removal"}
PREVIEW_REASON = {"adult": 1, "copyright": 2, "illegal": 3, "other": 4}
REGION_SCOPE = {"page": 0, "directory": 1, "domain": 2, "subdomain": 3}
SITEMOVE_SCOPE = {"domain": 0, "host": 1, "directory": 2}
SITEMOVE_TYPE = {"local": 0, "global": 1}
DEEPLINK_WEIGHT = {"disabled": 0, "low": 1, "normal": 2, "high": 3}
# Bing's own rule for a query-parameter name: unreserved characters and ':'.
PARAM_RE = re.compile(r"^[A-Za-z0-9\-_.~:]+$")
COUNTRY_RE = re.compile(r"^[a-z]{2}$")
CRAWL_RATE_MIN, CRAWL_RATE_MAX = 1, 10

RETIRED = ("GetSiteMoves", "SubmitSiteMove", "GetDeepLink", "GetDeepLinkAlgoUrls", "UpdateDeepLink")
RETIRED_ON = "2026-10-18"


def _now_iso():
    # Measured 2026-10-18: request bodies accept an ISO date (and /Date(ms)/);
    # a malformed one is InvalidParameter. ISO matches what the GETs accept.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _typed(name, **fields):
    return {"__type": f"{name}:#{API_NS}", **fields}


def _retired(r, method):
    if r.get("error") == "method_not_served":
        r["retired"] = True
        r["retired_means"] = (f"{method} answered 404 - measured on {RETIRED_ON}, alongside a "
                              "made-up method name that gets the same 404. Bing no longer serves "
                              "it; this is not a statement about the site.")
    return r


def _strip(r):
    return {k: v for k, v in r.items() if k != "ok"}


def _err_text(r):
    return r.get("error_name") or r.get("error")


# ------------------------------------------------------------------- reads
def sites(key):
    r = bing.call("GetUserSites", key)
    if not r["ok"]:
        return r
    rows = [{"url": s.get("Url"), "verified": s.get("IsVerified"),
             "authentication_code": s.get("AuthenticationCode"),
             "dns_verification_code": s.get("DnsVerificationCode")} for s in r["d"] or []]
    return {"ok": True, "count": len(rows), "sites": rows}


def verification_methods(row):
    """The three ways Bing accepts ownership, built from the site's own codes."""
    code, dns = row.get("authentication_code"), row.get("dns_verification_code")
    out = {}
    if code:
        out["meta_tag"] = f'<meta name="msvalidate.01" content="{code}" />'
        out["xml_file"] = {"path": "/BingSiteAuth.xml",
                           "content": f'<?xml version="1.0"?>\n<users>\n\t<user>{code}</user>\n</users>'}
    if dns:
        out["dns_cname"] = {"host": dns, "points_to": "verify.bing.com"}
    out["then"] = "deploy ONE of these, then run `bingadmin.py site-verify`"
    return out


def roles(key, site, all_subdomains=False):
    r = bing.call("GetSiteRoles", key, siteUrl=site,
                  includeAllSubdomains="true" if all_subdomains else "false")
    if not r["ok"]:
        return r
    rows = [{"email": x.get("Email"), "role": ROLE_NAMES.get(x.get("Role"), x.get("Role")),
             "date": bing._dotnet_date(x.get("Date")), "expired": x.get("Expired"),
             "delegator": x.get("DelegatorEmail"), "site": x.get("Site"),
             "verification_site": x.get("VerificationSite")} for x in r["d"] or []]
    out = {"ok": True, "site": site, "count": len(rows), "roles": rows, "_raw": r["d"] or []}
    if any(x["delegator"] == "GSCImport" for x in rows):
        out["delegator_note"] = ("`GSCImport` means the role came from importing the property "
                                 "from Google Search Console, not from a delegation in Bing.")
    return out


def blocks(key, site):
    b = bing.call("GetBlockedUrls", key, siteUrl=site)
    if not b["ok"]:
        return b
    p = bing.call("GetActivePagePreviewBlocks", key, siteUrl=site)
    reasons = {v: k for k, v in PREVIEW_REASON.items()}
    out = {"ok": True, "site": site,
           "blocked_urls": [{"url": x.get("Url"),
                             "entity": BLOCK_ENTITY.get(x.get("EntityType"), x.get("EntityType")),
                             "request": BLOCK_REQUEST.get(x.get("RequestType"), x.get("RequestType")),
                             "date": bing._dotnet_date(x.get("Date"))} for x in b["d"] or []],
           "_raw_blocked": b["d"] or []}
    if p["ok"]:
        out["preview_blocks"] = [{"url": x.get("Url"),
                                  "reason": reasons.get(x.get("BlockReason"), x.get("BlockReason")),
                                  "date": bing._dotnet_date(x.get("BlockDate"))} for x in p["d"] or []]
    else:
        out["preview_blocks_error"] = _strip(p)
    out["verdict"] = "clean" if not out["blocked_urls"] and not out.get("preview_blocks") else "blocks_present"
    out["means"] = ("each block holds a URL out of Bing (or its preview) whatever the page or "
                    "robots.txt says. Confirm every one is deliberate." if out["verdict"] != "clean"
                    else "no block of either kind is set on this account")
    return out


def params(key, site):
    r = bing.call("GetQueryParameters", key, siteUrl=site)
    if not r["ok"]:
        return r
    rows = [{"parameter": x.get("Parameter"), "enabled": x.get("IsEnabled"),
             "source": x.get("Source"), "date": bing._dotnet_date(x.get("Date"))} for x in r["d"] or []]
    return {"ok": True, "site": site, "count": len(rows), "parameters": rows,
            "means": ("parameters Bing is told to IGNORE when deciding if two URLs are the same "
                      "page. An enabled one collapses ?<param>= variants into one URL.")}


def regions(key, site):
    r = bing.call("GetCountryRegionSettings", key, siteUrl=site)
    if not r["ok"]:
        return r
    scopes = {v: k for k, v in REGION_SCOPE.items()}
    rows = [{"url": x.get("Url"), "country": x.get("TwoLetterIsoCountryCode"),
             "scope": scopes.get(x.get("Type"), x.get("Type")),
             "date": bing._dotnet_date(x.get("Date"))} for x in r["d"] or []]
    out = {"ok": True, "site": site, "count": len(rows), "regions": rows, "_raw": r["d"] or []}
    if rows:
        out["means"] = ("explicit geo-targeting. It overrides what Bing infers from hreflang, "
                        "ccTLD and content - check it agrees with the hreflang mesh.")
    return out


def connected(key, site):
    r = bing.call("GetConnectedPages", key, siteUrl=site)
    if not r["ok"]:
        return r
    rows = [{"url": x.get("Url"), "status": x.get("VerificationStatus"),
             "status_details": x.get("VerificationStatusDetails"),
             "verified": bing._dotnet_date(x.get("VerifiedDate")),
             "submitted": bing._dotnet_date(x.get("SubmissionDate"))} for x in r["d"] or []]
    return {"ok": True, "site": site, "count": len(rows), "connected": rows}


def deeplink_blocks(key, site):
    r = bing.call("GetDeepLinkBlocks", key, siteUrl=site)
    if not r["ok"]:
        return r
    rows = [{"market": x.get("Market"), "search_url": x.get("SearchUrl"),
             "deeplink_url": x.get("DeepLinkUrl"), "date": bing._dotnet_date(x.get("BlockDate"))}
            for x in r["d"] or []]
    return {"ok": True, "site": site, "count": len(rows), "deeplink_blocks": rows}


def crawlsettings(key, site):
    r = bing.call("GetCrawlSettings", key, siteUrl=site)
    if not r["ok"]:
        return r
    d = r["d"] or {}
    return {"ok": True, "site": site, "crawl_rate_by_hour": d.get("CrawlRate") or [],
            "crawl_boost_available": d.get("CrawlBoostAvailable"),
            "crawl_boost_enabled": d.get("CrawlBoostEnabled"), "_raw": d}


def sitemoves(key, site):
    r = bing.call("GetSiteMoves", key, siteUrl=site)
    if not r["ok"]:
        return _retired(r, "GetSiteMoves")
    return {"ok": True, "site": site, "site_moves": r["d"] or []}


def deeplinks(key, site, url):
    r = bing.call("GetDeepLink", key, siteUrl=site, url=url)
    if not r["ok"]:
        return _retired(r, "GetDeepLink")
    return {"ok": True, "site": site, "url": url, "deeplinks": r["d"] or []}


def deeplink_algo(key, site):
    r = bing.call("GetDeepLinkAlgoUrls", key, siteUrl=site)
    if not r["ok"]:
        return _retired(r, "GetDeepLinkAlgoUrls")
    return {"ok": True, "site": site, "algo_urls": r["d"] or []}


def _public(doc):
    """Drop the `_raw` fields readers keep for later writes."""
    if not isinstance(doc, dict):
        return doc
    return {k: v for k, v in doc.items() if not k.startswith("_raw")}


def state(key, site):
    """Every piece of account configuration, one call. Each section carries its own
    ok/error, so one failing endpoint is visible rather than sinking the report."""
    sections = {
        "sites": lambda: sites(key),
        "roles": lambda: roles(key, site),
        "blocks": lambda: blocks(key, site),
        "query_parameters": lambda: params(key, site),
        "regions": lambda: regions(key, site),
        "crawl_settings": lambda: crawlsettings(key, site),
        "connected_pages": lambda: connected(key, site),
        "deeplink_blocks": lambda: deeplink_blocks(key, site),
    }
    out = {"ok": True, "site": site, "sections": {}}
    failed = []
    for name, fn in sections.items():
        doc = _public(fn())
        out["sections"][name] = doc
        if not doc.get("ok"):
            failed.append(name)
    attention = []
    b = out["sections"]["blocks"]
    if b.get("ok") and b.get("verdict") != "clean":
        attention.append("URL or preview blocks are set - each one keeps a page out of Bing")
    pr = out["sections"]["query_parameters"]
    if pr.get("ok") and any(p["enabled"] for p in pr["parameters"]):
        attention.append("enabled query parameters collapse URL variants")
    rg = out["sections"]["regions"]
    if rg.get("ok") and rg["count"]:
        attention.append("explicit country targeting is set - check it against hreflang")
    cs = out["sections"]["crawl_settings"]
    rate = cs.get("crawl_rate_by_hour") or []
    if cs.get("ok") and rate and min(rate) < 5:
        attention.append(f"crawl rate is capped below Bing's default 5 in {sum(1 for x in rate if x < 5)} hour(s)")
    st = out["sections"]["sites"]
    if st.get("ok"):
        unverified = [s["url"] for s in st["sites"] if not s["verified"]]
        if unverified:
            attention.append(f"unverified sites on the account: {unverified}")
    out["attention"] = attention
    if failed:
        out["failed_sections"] = failed
        out["failed_means"] = ("these sections could not be read - their state is UNKNOWN, "
                               "not empty")
    return out


# ------------------------------------------------------------------ writes
def _apply(key, site, method, payload, confirm, read_back, expect, root, plan=None):
    """Dry run, or send + read back. read_back() -> doc; expect(doc) -> bool."""
    plan = {"ok": True, "site": site, "method": method, "payload": payload, "applied": False,
            **(plan or {})}
    if not confirm:
        plan["dry_run"] = True
        plan["what_to_do"] = "re-run with --yes to apply"
        return plan
    r = bing.post(method, key, payload)
    if not r["ok"]:
        plan["ok"] = False
        plan.update(_strip(_retired(r, method) if method in RETIRED else r))
        return plan
    plan["applied"] = True
    after = read_back()
    if not after.get("ok"):
        plan["verified"] = "unverifiable"
        plan["verified_means"] = ("the write returned null and the read-back failed, so whether "
                                  "it took effect is UNKNOWN. Re-read before retrying.")
        plan["read_back_error"] = _strip(after)
    elif expect(after):
        plan["verified"] = "confirmed"
    else:
        plan["ok"] = False
        plan["verified"] = "not_reflected"
        plan["verified_means"] = ("Bing answered null - which it also answers for a no-op - and "
                                  "the read-back does not show the change. It may lag; re-read "
                                  "before retrying.")
    plan["state_after"] = _public(after)
    plan["receipt"] = bing.receipt(root, {"action": method, "site": site, "payload": payload,
                                          "verified": plan["verified"]})
    return plan


def _refuse(error, detail, **kw):
    return {"ok": False, "error": error, "detail": detail, **kw}


def _site_row(doc, site):
    return next((s for s in doc.get("sites", []) if s["url"] == site), None)


def site_add(key, site, confirm, root):
    before = sites(key)
    if not before["ok"]:
        return before
    if _site_row(before, site):
        row = _site_row(before, site)
        return _refuse("already_on_account", f"{site} is already on this account",
                       verified=row["verified"], verification=verification_methods(row))
    plan = _apply(key, site, "AddSite", {"siteUrl": site}, confirm,
                  lambda: sites(key), lambda d: _site_row(d, site) is not None, root)
    if plan.get("verified") == "confirmed":
        row = _site_row(plan["state_after"], site)
        plan["verification"] = verification_methods(row)
    return plan


def site_verify(key, site, root):
    """VerifySite changes nothing but the verified flag, so it is not gated."""
    before = sites(key)
    if not before["ok"]:
        return before
    row = _site_row(before, site)
    if not row:
        return _refuse("not_on_account", f"{site} is not on this account - `site-add` it first")
    if row["verified"]:
        return {"ok": True, "site": site, "verified": True, "note": "already verified"}
    r = bing.post("VerifySite", key, {"siteUrl": site})
    if not r["ok"]:
        return r
    after = _site_row(sites(key), site) or {}
    out = {"ok": bool(after.get("verified")), "site": site, "verify_call_returned": r["d"],
           "verified": after.get("verified")}
    if not after.get("verified"):
        out["what_to_do"] = ("Bing could not find any of the three proofs. Deploy one, confirm it "
                             "is live, and run this again.")
        out["verification"] = verification_methods(row)
    bing.receipt(root, {"action": "VerifySite", "site": site, "verified": after.get("verified")})
    return out


def site_remove(key, site, confirm, confirm_site, root):
    before = sites(key)
    if not before["ok"]:
        return before
    if not _site_row(before, site):
        return _refuse("not_on_account", f"{site} is not on this account - nothing to remove")
    if confirm and confirm_site != site:
        return _refuse("confirm_site_mismatch",
                       "--confirm-site must repeat the exact site url. Removing a verified site "
                       "drops it from the account for every user it was delegated to.",
                       expected=site, got=confirm_site)
    return _apply(key, site, "RemoveSite", {"siteUrl": site}, confirm,
                  lambda: sites(key), lambda d: _site_row(d, site) is None, root,
                  {"warning": "removes the site and its delegations from this account"})


def _roles_for(doc, email):
    return [(x, raw) for x, raw in zip(doc.get("roles", []), doc.get("_raw", []))
            if (x["email"] or "").lower() == email.lower()]


def role_add(key, site, email, role, confirm, root):
    before = roles(key, site)
    if not before["ok"]:
        return before
    if _roles_for(before, email):
        return _refuse("role_exists", f"{email} already has a role on {site}",
                       roles=[x for x, _ in _roles_for(before, email)])
    s = sites(key)
    row = _site_row(s, site) if s.get("ok") else None
    if not row:
        return _refuse("site_not_on_account", f"{site} is not on this account")
    is_admin, read_only = ROLE_FLAGS[role]
    payload = {"siteUrl": site, "delegatedUrl": site, "userEmail": email,
               "authenticationCode": row["authentication_code"],
               "isAdministrator": is_admin, "isReadOnly": read_only}
    return _apply(key, site, "AddSiteRoles", payload, confirm, lambda: roles(key, site),
                  lambda d: bool(_roles_for(d, email)), root)


def last_admin(rows, email):
    """True when removing `email` would leave no live administrator."""
    admins = [x for x in rows if x["role"] == "administrator" and not x.get("expired")]
    return bool(admins) and all((x["email"] or "").lower() == email.lower() for x in admins)


def role_remove(key, site, email, confirm, root):
    before = roles(key, site)
    if not before["ok"]:
        return before
    hits = _roles_for(before, email)
    if not hits:
        return _refuse("no_such_role", f"{email} has no role on {site}")
    if last_admin(before["roles"], email):
        return _refuse("last_administrator",
                       f"{email} is the only administrator of {site}; removing it would lock "
                       "the account out of its own site")
    _, raw = hits[0]
    return _apply(key, site, "RemoveSiteRole", {"siteUrl": site, "siteRole": raw}, confirm,
                  lambda: roles(key, site), lambda d: not _roles_for(d, email), root)


def _blocked_row(doc, url):
    return next((raw for x, raw in zip(doc.get("blocked_urls", []), doc.get("_raw_blocked", []))
                 if x["url"] == url), None)


def block_payload(site, url, directory, mode):
    return {"siteUrl": site, "blockedUrl": _typed(
        "BlockedUrl", Date=_now_iso(), EntityType=1 if directory else 0,
        RequestType=1 if mode == "full_removal" else 0, Url=url)}


def block_add(key, site, url, directory, mode, confirm, root):
    if not bing.is_on_site(site, url):
        return _refuse("url_off_site", f"{url} is not on {site}")
    before = blocks(key, site)
    if not before["ok"]:
        return before
    if _blocked_row(before, url):
        return _refuse("already_blocked", f"{url} is already blocked")
    plan = {"warning": ("full_removal takes the URL out of Bing's index, not just its cache"
                        + (" - for EVERY url under this directory" if directory else ""))
            if mode == "full_removal" else
            "cache_only removes the cached copy; the page can still rank"}
    return _apply(key, site, "AddBlockedUrl", block_payload(site, url, directory, mode), confirm,
                  lambda: blocks(key, site), lambda d: _blocked_row(d, url) is not None, root, plan)


def block_remove(key, site, url, confirm, root):
    before = blocks(key, site)
    if not before["ok"]:
        return before
    raw = _blocked_row(before, url)
    if not raw:
        return _refuse("not_blocked", f"{url} is not blocked - RemoveBlockedUrl would answer the "
                                      "same null for a no-op, so nothing is sent")
    return _apply(key, site, "RemoveBlockedUrl", {"siteUrl": site, "blockedUrl": raw}, confirm,
                  lambda: blocks(key, site), lambda d: _blocked_row(d, url) is None, root)


def _has_preview(doc, url):
    return any(x["url"] == url for x in doc.get("preview_blocks", []))


def preview_add(key, site, url, reason, confirm, root):
    if not bing.is_on_site(site, url):
        return _refuse("url_off_site", f"{url} is not on {site}")
    before = blocks(key, site)
    if not before["ok"]:
        return before
    if _has_preview(before, url):
        return _refuse("already_blocked", f"{url} already has a preview block")
    return _apply(key, site, "AddPagePreviewBlock",
                  {"siteUrl": site, "url": url, "reason": PREVIEW_REASON[reason]}, confirm,
                  lambda: blocks(key, site), lambda d: _has_preview(d, url), root)


def preview_remove(key, site, url, confirm, root):
    before = blocks(key, site)
    if not before["ok"]:
        return before
    if not _has_preview(before, url):
        return _refuse("not_blocked", f"{url} has no preview block")
    return _apply(key, site, "RemovePagePreviewBlock", {"siteUrl": site, "url": url}, confirm,
                  lambda: blocks(key, site), lambda d: not _has_preview(d, url), root)


def _param(doc, name):
    return next((p for p in doc.get("parameters", []) if p["parameter"] == name), None)


def param_change(key, site, name, action, confirm, root):
    if not PARAM_RE.match(name or ""):
        return _refuse("bad_parameter", f"{name!r}: Bing accepts only [A-Za-z0-9-_.~:]")
    before = params(key, site)
    if not before["ok"]:
        return before
    cur = _param(before, name)
    if action == "add":
        if cur:
            return _refuse("parameter_exists", f"{name} is already set", current=cur)
        return _apply(key, site, "AddQueryParameter", {"siteUrl": site, "queryParameter": name},
                      confirm, lambda: params(key, site), lambda d: _param(d, name) is not None, root)
    if not cur:
        return _refuse("no_such_parameter", f"{name} is not set - nothing to change")
    if action == "remove":
        return _apply(key, site, "RemoveQueryParameter", {"siteUrl": site, "queryParameter": name},
                      confirm, lambda: params(key, site), lambda d: _param(d, name) is None, root)
    want = action == "enable"
    if cur["enabled"] == want:
        return _refuse("no_change", f"{name} is already {'enabled' if want else 'disabled'}")
    return _apply(key, site, "EnableDisableQueryParameter",
                  {"siteUrl": site, "queryParameter": name, "isEnabled": want}, confirm,
                  lambda: params(key, site),
                  lambda d: (_param(d, name) or {}).get("enabled") == want, root)


def _region_row(doc, country, scope, url):
    for x, raw in zip(doc.get("regions", []), doc.get("_raw", [])):
        if x["country"] == country and x["scope"] == scope and x["url"] == url:
            return raw
    return None


def region_change(key, site, country, scope, url, action, confirm, root):
    country = (country or "").lower()
    if not COUNTRY_RE.match(country):
        return _refuse("bad_country", f"{country!r}: a two-letter ISO code, e.g. de")
    if not bing.is_on_site(site, url):
        return _refuse("url_off_site", f"{url} is not on {site}")
    before = regions(key, site)
    if not before["ok"]:
        return before
    raw = _region_row(before, country, scope, url)
    if action == "add":
        if raw:
            return _refuse("region_exists", f"{url} already targets {country} as {scope}")
        payload = {"siteUrl": site, "settings": _typed(
            "CountryRegionSettings", Date=_now_iso(), TwoLetterIsoCountryCode=country,
            Type=REGION_SCOPE[scope], Url=url)}
        return _apply(key, site, "AddCountryRegionSettings", payload, confirm,
                      lambda: regions(key, site),
                      lambda d: _region_row(d, country, scope, url) is not None, root)
    if not raw:
        return _refuse("no_such_region", f"no {scope} targeting of {url} to {country}")
    return _apply(key, site, "RemoveCountryRegionSettings", {"siteUrl": site, "settings": raw},
                  confirm, lambda: regions(key, site),
                  lambda d: _region_row(d, country, scope, url) is None, root)


def parse_hours(spec, current):
    """'5' or 'all=5' or '0-6=8,7-23=3' -> 24 values, starting from `current`.
    Raises ValueError on an hour outside 0-23 or a rate outside 1-10."""
    rate = list(current) if len(current or []) == 24 else [5] * 24
    spec = (spec or "").strip()
    if re.fullmatch(r"\d+", spec):
        spec = f"all={spec}"
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        if "=" not in part:
            raise ValueError(f"{part!r}: expected <hours>=<rate>")
        hrs, val = part.split("=", 1)
        v = int(val)
        if not CRAWL_RATE_MIN <= v <= CRAWL_RATE_MAX:
            raise ValueError(f"rate {v} outside {CRAWL_RATE_MIN}-{CRAWL_RATE_MAX}")
        if hrs.strip() == "all":
            span = range(24)
        else:
            lo, _, hi = hrs.partition("-")
            lo, hi = int(lo), int(hi or lo)
            if not (0 <= lo <= hi <= 23):
                raise ValueError(f"hours {hrs!r} outside 0-23")
            span = range(lo, hi + 1)
        for h in span:
            rate[h] = v
    return rate


def crawlrate_set(key, site, spec, boost, confirm, root):
    cur = crawlsettings(key, site)
    if not cur["ok"]:
        return cur
    try:
        rate = parse_hours(spec, cur["crawl_rate_by_hour"]) if spec else list(cur["crawl_rate_by_hour"])
    except ValueError as e:
        return _refuse("bad_rate", str(e))
    enabled = cur["crawl_boost_enabled"]
    if boost is not None:
        if boost and not cur["crawl_boost_available"]:
            return _refuse("boost_unavailable", "Bing does not offer crawl boost on this site")
        enabled = boost
    if rate == cur["crawl_rate_by_hour"] and enabled == cur["crawl_boost_enabled"]:
        return _refuse("no_change", "the requested settings equal the current ones")
    payload = {"siteUrl": site, "crawlSettings": _typed(
        "CrawlSettings", CrawlBoostAvailable=cur["crawl_boost_available"],
        CrawlBoostEnabled=enabled, CrawlRate=rate)}
    return _apply(key, site, "SaveCrawlSettings", payload, confirm,
                  lambda: crawlsettings(key, site),
                  lambda d: d["crawl_rate_by_hour"] == rate and d["crawl_boost_enabled"] == enabled,
                  root, {"before": {"crawl_rate_by_hour": cur["crawl_rate_by_hour"],
                                    "crawl_boost_enabled": cur["crawl_boost_enabled"]},
                         "note": ("a higher rate removes a ceiling; it does not make Bing crawl "
                                  "more. A lower one is a real cap.")})


def connected_add(key, site, master_url, confirm, root):
    before = connected(key, site)
    if not before["ok"]:
        return before
    if any(x["url"] == master_url for x in before["connected"]):
        return _refuse("already_connected", f"{master_url} is already connected")
    return _apply(key, site, "AddConnectedPage", {"siteUrl": site, "masterUrl": master_url},
                  confirm, lambda: connected(key, site),
                  lambda d: any(x["url"] == master_url for x in d["connected"]), root,
                  {"note": ("measured 2026-10-18: AddConnectedPage answered null even for a site "
                            "the account does not own, so only the read-back counts")})


def _has_dlb(doc, market, search_url, deeplink_url):
    return any(x["market"] == market and x["search_url"] == search_url
               and x["deeplink_url"] == deeplink_url for x in doc.get("deeplink_blocks", []))


def deeplink_block_change(key, site, market, search_url, deeplink_url, action, confirm, root):
    before = deeplink_blocks(key, site)
    if not before["ok"]:
        return before
    have = _has_dlb(before, market, search_url, deeplink_url)
    if action == "add" and have:
        return _refuse("already_blocked", "this deep link is already blocked")
    if action == "remove" and not have:
        return _refuse("not_blocked", "no such deep-link block")
    payload = {"siteUrl": site, "market": market, "searchUrl": search_url,
               "deepLinkUrl": deeplink_url}
    method = "AddDeepLinkBlock" if action == "add" else "RemoveDeepLinkBlock"
    return _apply(key, site, method, payload, confirm, lambda: deeplink_blocks(key, site),
                  lambda d: _has_dlb(d, market, search_url, deeplink_url) == (action == "add"), root)


def sitemove(key, site, source, target, scope, move_type, confirm, root):
    payload = {"siteUrl": site, "settings": _typed(
        "SiteMoveSettings", Date=_now_iso(), MoveScope=SITEMOVE_SCOPE[scope],
        MoveType=SITEMOVE_TYPE[move_type], SourceUrl=source, TargetUrl=target)}
    return _apply(key, site, "SubmitSiteMove", payload, confirm, lambda: sitemoves(key, site),
                  lambda d: any((m.get("TargetUrl") == target) for m in d.get("site_moves", [])),
                  root, {"retired_note": f"SubmitSiteMove answered 404 on {RETIRED_ON}"})


def deeplink_weight(key, site, algo_url, deeplink, weight, confirm, root):
    payload = {"siteUrl": site, "algoUrl": algo_url, "deepLink": deeplink,
               "weight": DEEPLINK_WEIGHT[weight]}
    return _apply(key, site, "UpdateDeepLink", payload, confirm,
                  lambda: deeplinks(key, site, algo_url),
                  lambda d: any(x.get("Url") == deeplink and x.get("Weight") == DEEPLINK_WEIGHT[weight]
                                for x in d.get("deeplinks", [])),
                  root, {"retired_note": f"UpdateDeepLink answered 404 on {RETIRED_ON}"})


# ----------------------------------------------------------------- control
def run_control():
    """The guards and builders, offline. A write guard that silently passes is a
    destructive call sent on a typo, so each one is proven to refuse AND to allow."""
    c = Controls("bingadmin-control")
    c.check("a_single_rate_fills_all_24_hours", parse_hours("7", [5] * 24) == [7] * 24)
    r = parse_hours("0-5=8,22-23=3", [5] * 24)
    c.check("an_hour_range_spec_sets_only_those_hours",
            r[:6] == [8] * 6 and r[6:22] == [5] * 16 and r[22:] == [3, 3], str(r))
    c.check("a_spec_starts_from_the_CURRENT_rate_not_the_default",
            parse_hours("0=9", [2] * 24)[1:] == [2] * 23)
    for bad in ("0-24=5", "all=11", "all=0", "5-3=4", "nonsense"):
        try:
            parse_hours(bad, [5] * 24)
            c.check(f"bad_spec_{bad}_is_refused", False)
        except ValueError:
            c.check(f"bad_spec_{bad}_is_refused", True)

    c.check("a_query_parameter_with_unreserved_chars_passes", bool(PARAM_RE.match("utm_source")))
    c.check("a_parameter_with_an_equals_sign_is_refused", not PARAM_RE.match("a=b"))
    c.check("a_country_code_is_two_lowercase_letters",
            bool(COUNTRY_RE.match("de")) and not COUNTRY_RE.match("DEU"))

    rows = [{"email": "a@x", "role": "administrator", "expired": False},
            {"email": "b@x", "role": "read_only", "expired": False}]
    c.check("removing_the_only_administrator_is_refused", last_admin(rows, "A@x"))
    c.check("CONTROL_removing_a_non_admin_is_allowed", not last_admin(rows, "b@x"))
    two = rows + [{"email": "c@x", "role": "administrator", "expired": False}]
    c.check("CONTROL_removing_one_of_two_admins_is_allowed", not last_admin(two, "a@x"))
    exp = rows + [{"email": "c@x", "role": "administrator", "expired": True}]
    c.check("an_EXPIRED_second_admin_does_not_count", last_admin(exp, "a@x"))

    p = block_payload("https://x.test/", "https://x.test/d/", True, "full_removal")["blockedUrl"]
    c.check("a_directory_full_removal_payload_has_the_right_enums",
            (p["EntityType"], p["RequestType"]) == (1, 1), str(p))
    p = block_payload("https://x.test/", "https://x.test/p", False, "cache_only")["blockedUrl"]
    c.check("CONTROL_a_page_cache_only_payload_differs", (p["EntityType"], p["RequestType"]) == (0, 0))
    c.check("payloads_carry_the_wcf_type_hint", p["__type"] == f"BlockedUrl:#{API_NS}")

    v = verification_methods({"authentication_code": "ABC", "dns_verification_code": "h.x.test"})
    c.check("verification_offers_meta_file_and_cname",
            "ABC" in v["meta_tag"] and "ABC" in v["xml_file"]["content"]
            and v["dns_cname"]["host"] == "h.x.test")

    r404 = _retired({"ok": False, "error": "method_not_served"}, "GetSiteMoves")
    c.check("a_404_is_reported_as_retired", r404.get("retired") is True)
    c.check("CONTROL_an_auth_failure_is_not_called_retired",
            "retired" not in _retired({"ok": False, "error": "x", "error_code": 14}, "GetSiteMoves"))

    sent = []
    orig = bing.post
    bing.post = lambda *a, **k: sent.append(a) or {"ok": True, "d": None}
    try:
        dry = _apply("k", "https://x.test/", "AddQueryParameter", {"q": 1}, False,
                     lambda: {"ok": True}, lambda d: True, None)
        c.check("a_dry_run_sends_nothing", sent == [] and dry["dry_run"] is True)
        nr = _apply("k", "https://x.test/", "AddQueryParameter", {"q": 1}, True,
                    lambda: {"ok": True}, lambda d: False, None)
        c.check("a_null_answer_without_the_change_is_not_reflected_not_ok",
                nr["verified"] == "not_reflected" and nr["ok"] is False)
        ok = _apply("k", "https://x.test/", "AddQueryParameter", {"q": 1}, True,
                    lambda: {"ok": True}, lambda d: True, None)
        c.check("CONTROL_a_reflected_change_is_confirmed", ok["verified"] == "confirmed" and ok["ok"])
        un = _apply("k", "https://x.test/", "AddQueryParameter", {"q": 1}, True,
                    lambda: {"ok": False, "error": "x"}, lambda d: True, None)
        c.check("a_failed_read_back_is_unverifiable_never_confirmed", un["verified"] == "unverifiable")
    finally:
        bing.post = orig
    return c.verdict(retired_methods=list(RETIRED))


# --------------------------------------------------------------------- cli
def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--site", help="site url; inferred when the account has exactly one verified site")
    common.add_argument("--yes", action="store_true", help="apply a write. Without it: dry run")
    common.add_argument("--root", default=".", help="site repo; receipts go to <root>/.seo/bing.jsonl")

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("control", help="prove the guards and payload builders (no key needed)")
    for name, h in [("state", "every configuration read in one report"),
                    ("sites", "sites on the account with their verification codes"),
                    ("blocks", "blocked URLs and page-preview blocks"),
                    ("params", "ignored query parameters"),
                    ("regions", "country/region targeting"),
                    ("connected", "connected pages"),
                    ("deeplink-blocks", "blocked deep links (sitelinks)"),
                    ("sitemoves", "RETIRED: site moves"),
                    ("deeplink-algo", "RETIRED: pages with algorithmic deep links"),
                    ("site-add", "AddSite"), ("site-verify", "VerifySite")]:
        sub.add_parser(name, help=h, parents=[common])
    rs = sub.add_parser("roles", help="who has access, as what", parents=[common])
    rs.add_argument("--all-subdomains", action="store_true")
    sr = sub.add_parser("site-remove", help="RemoveSite (needs --yes and --confirm-site)", parents=[common])
    sr.add_argument("--confirm-site", help="repeat the exact site url")
    ra = sub.add_parser("role-add", help="AddSiteRoles", parents=[common])
    ra.add_argument("--email", required=True)
    ra.add_argument("--role", choices=sorted(ROLE_FLAGS), required=True)
    rr = sub.add_parser("role-remove", help="RemoveSiteRole", parents=[common])
    rr.add_argument("--email", required=True)
    ba = sub.add_parser("block-add", help="AddBlockedUrl", parents=[common])
    ba.add_argument("--url", required=True)
    ba.add_argument("--directory", action="store_true", help="block everything under this path")
    ba.add_argument("--mode", choices=["cache_only", "full_removal"], default="cache_only")
    br = sub.add_parser("block-remove", help="RemoveBlockedUrl", parents=[common])
    br.add_argument("--url", required=True)
    pa = sub.add_parser("preview-add", help="AddPagePreviewBlock", parents=[common])
    pa.add_argument("--url", required=True)
    pa.add_argument("--reason", choices=sorted(PREVIEW_REASON), required=True)
    pr = sub.add_parser("preview-remove", help="RemovePagePreviewBlock", parents=[common])
    pr.add_argument("--url", required=True)
    for action in ("add", "remove", "enable", "disable"):
        x = sub.add_parser(f"param-{action}", help=f"{action} an ignored query parameter", parents=[common])
        x.add_argument("--name", required=True)
    for action in ("add", "remove"):
        x = sub.add_parser(f"region-{action}", help=f"{action} country targeting", parents=[common])
        x.add_argument("--country", required=True, help="two-letter ISO code, e.g. de")
        x.add_argument("--scope", choices=sorted(REGION_SCOPE), required=True)
        x.add_argument("--url", required=True)
    cr = sub.add_parser("crawlrate-set", help="SaveCrawlSettings", parents=[common])
    cr.add_argument("--rate", help="'5', 'all=5' or '0-6=8,7-23=3' (1 slowest .. 10 fastest)")
    cr.add_argument("--boost", choices=["on", "off"])
    ca = sub.add_parser("connected-add", help="AddConnectedPage", parents=[common])
    ca.add_argument("--master-url", required=True)
    for action in ("add", "remove"):
        x = sub.add_parser(f"deeplink-block-{action}", help=f"{action} a deep-link block", parents=[common])
        x.add_argument("--market", required=True, help="e.g. en-US")
        x.add_argument("--search-url", required=True, help="the result URL the deep link appears under")
        x.add_argument("--deeplink-url", required=True)
    dl = sub.add_parser("deeplinks", help="RETIRED: deep links for one result URL", parents=[common])
    dl.add_argument("--url", required=True)
    sm = sub.add_parser("sitemove", help="RETIRED: SubmitSiteMove", parents=[common])
    sm.add_argument("--source", required=True)
    sm.add_argument("--target", required=True)
    sm.add_argument("--scope", choices=sorted(SITEMOVE_SCOPE), required=True)
    sm.add_argument("--type", dest="move_type", choices=sorted(SITEMOVE_TYPE), required=True)
    dw = sub.add_parser("deeplink-weight", help="RETIRED: UpdateDeepLink", parents=[common])
    dw.add_argument("--algo-url", required=True)
    dw.add_argument("--deeplink", required=True)
    dw.add_argument("--weight", choices=sorted(DEEPLINK_WEIGHT), required=True)

    a = p.parse_args()
    if a.cmd == "control":
        out = run_control()
        print(json.dumps(out, indent=2))
        sys.exit(0 if out.get("ok") else 1)
    if a.cmd == "crawlrate-set" and not a.rate and not a.boost:
        print(json.dumps({"ok": False, "error": "nothing_to_set", "detail": "pass --rate and/or --boost"}))
        sys.exit(2)

    key = bing.read_key()
    if not key:
        print(json.dumps({"ok": False, "state": "no_key",
                          "error": "no BING_WEBMASTER_API_KEY and no ~/.bing_webmaster_key"}))
        sys.exit(2)

    if a.cmd == "sites":
        out = sites(key)
        for s in out.get("sites", []):
            if not s["verified"]:
                s["verification"] = verification_methods(s)
        print(json.dumps(out, indent=2))
        sys.exit(0 if out.get("ok") else 3)

    # Site management names a site that may not be on the account yet, so it is
    # never inferred: a guessed site is the wrong site added or removed.
    if a.cmd in ("site-add", "site-verify", "site-remove"):
        if not a.site:
            print(json.dumps({"ok": False, "error": "pass --site", "detail": "never inferred for site management"}))
            sys.exit(2)
        site = bing._norm_site(a.site)
    else:
        site, err = bing._resolve_site(key, a.site)
        if err:
            print(json.dumps(err, indent=2))
            sys.exit(3)

    y, root = a.yes, a.root
    dispatch = {
        "state": lambda: state(key, site),
        "roles": lambda: roles(key, site, a.all_subdomains),
        "blocks": lambda: blocks(key, site),
        "params": lambda: params(key, site),
        "regions": lambda: regions(key, site),
        "connected": lambda: connected(key, site),
        "deeplink-blocks": lambda: deeplink_blocks(key, site),
        "sitemoves": lambda: sitemoves(key, site),
        "deeplink-algo": lambda: deeplink_algo(key, site),
        "deeplinks": lambda: deeplinks(key, site, getattr(a, "url", None)),
        "site-add": lambda: site_add(key, site, y, root),
        "site-verify": lambda: site_verify(key, site, root),
        "site-remove": lambda: site_remove(key, site, y, getattr(a, "confirm_site", None), root),
        "role-add": lambda: role_add(key, site, a.email, a.role, y, root),
        "role-remove": lambda: role_remove(key, site, a.email, y, root),
        "block-add": lambda: block_add(key, site, a.url, a.directory, a.mode, y, root),
        "block-remove": lambda: block_remove(key, site, a.url, y, root),
        "preview-add": lambda: preview_add(key, site, a.url, a.reason, y, root),
        "preview-remove": lambda: preview_remove(key, site, a.url, y, root),
        "crawlrate-set": lambda: crawlrate_set(key, site, a.rate,
                                               None if a.boost is None else a.boost == "on", y, root),
        "connected-add": lambda: connected_add(key, site, a.master_url, y, root),
        "sitemove": lambda: sitemove(key, site, getattr(a, "source", None), getattr(a, "target", None),
                                     getattr(a, "scope", None), getattr(a, "move_type", None), y, root),
        "deeplink-weight": lambda: deeplink_weight(key, site, getattr(a, "algo_url", None),
                                                   getattr(a, "deeplink", None),
                                                   getattr(a, "weight", None), y, root),
    }
    if a.cmd.startswith("param-"):
        out = param_change(key, site, a.name, a.cmd.split("-", 1)[1], y, root)
    elif a.cmd.startswith("region-"):
        out = region_change(key, site, a.country, a.scope, a.url, a.cmd.split("-", 1)[1], y, root)
    elif a.cmd.startswith("deeplink-block-"):
        out = deeplink_block_change(key, site, a.market, a.search_url, a.deeplink_url,
                                    a.cmd.rsplit("-", 1)[1], y, root)
    else:
        out = dispatch[a.cmd]()
    print(json.dumps(_public(out), indent=2, ensure_ascii=False))
    sys.exit(0 if out.get("ok") else 3)


if __name__ == "__main__":
    main()
