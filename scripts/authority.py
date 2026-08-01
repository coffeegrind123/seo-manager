#!/usr/bin/env python3
"""Domain authority (DR-equivalent) for the seo-manager skill.

The whole quality bar scales off ONE number: how strong is this site today?
It sets the KD ceiling, the volume band, and whether the research workflow's
rung 2 is even open. The usual source is DataForSEO's paid backlinks summary;
this does the same job from free sources, and is explicit about which one
answered.

Source ladder, best first:

  dataforseo   Paid backlinks summary. rank/10 -> DR. Exactly what the
               original uses. BYO DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD.
  openpagerank Open PageRank. FREE, built from Common Crawl's link graph.
               0-10 scale -> x10 -> DR-equivalent, and it also returns a
               REFERRING-DOMAIN COUNT, which is the only free backlink-volume
               number in this whole skill.
               BYO OPENPAGERANK_API_KEY, or a chmod-600 ~/.openpagerank_key.

               TWO generations of this API exist and the key tells them apart:
                 - `opr_live_...`  -> the CURRENT host, run by Keywords
                   Everywhere, which acquired Open PageRank. Free plan is
                   30,000 domains/month, up to 100 domains per call, with
                   monthly history back to 2018 and spam-scored-down link
                   networks. Sign up at
                   https://openpagerank.keywordseverywhere.com/ (it wants a
                   free Keywords Everywhere API key first, from
                   https://keywordseverywhere.com/first-install-addon.html).
                 - 40-char hex   -> the LEGACY domcop endpoint, 1000 req/day.
                   Still served, but its own operators retire it on
                   2026-09-30. Kept here only so an existing key keeps working.
               Measured 2026-08-01: www.domcop.com/openpagerank/ no longer
               takes new signups at all, so the legacy path is a migration
               shim, not a thing to point anyone at.
  estimate     Keyless composite from signals anyone can measure: domain age
               (RDAP), how many pages the site has indexed, and its Search
               Console footprint if you pass one. Deliberately CONSERVATIVE
               and deliberately capped at 25 - an estimate must never unlock
               the high-authority KD ceiling, because being wrong upward is
               what queues keywords a young site cannot win.

Whatever answers, the output carries `source` and `confidence`, and the
downstream zones are computed from the same table as the quality bar. A null
DR is treated as 0 everywhere (not indexed yet == no authority yet).

Stdlib only.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def fetch(url, *, data=None, headers=None, timeout=25):
    req = urllib.request.Request(
        url,
        data=data.encode() if isinstance(data, str) else data,
        headers={"User-Agent": UA, "Accept": "application/json, */*", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, str(exc)


def clean_domain(d: str) -> str:
    d = (d or "").strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0]
    return d[4:] if d.startswith("www.") else d


# ------------------------------------------------------------ the zone table


def kd_zones(dr):
    """The research quality bar's dynamic KD zones. Keep in sync with
    references/quality-bar.md - editing one without the other makes the tooling
    state a rule the agent does not actually apply."""
    d = dr or 0
    if d >= 35:
        return {"auto_approve_below": 35, "pending_below": 45}
    if d >= 20:
        return {"auto_approve_below": 25, "pending_below": 35}
    if d >= 10:
        return {"auto_approve_below": 15, "pending_below": 25}
    return {"auto_approve_below": 10, "pending_below": 20}


def volume_band(dr):
    d = dr or 0
    if d >= 35:
        return {"floor": 500, "ceiling": None, "soft_edge": None}
    if d >= 20:
        return {"floor": 300, "ceiling": 3000, "soft_edge": 6000}
    if d >= 10:
        return {"floor": 200, "ceiling": 1500, "soft_edge": 3000}
    return {"floor": 100, "ceiling": 800, "soft_edge": 1500}


# ------------------------------------------------------------------ sources


def from_dataforseo(domain: str):
    login = os.environ.get("DATAFORSEO_LOGIN", "")
    password = os.environ.get("DATAFORSEO_PASSWORD", "")
    if not (login and password):
        return None
    auth = base64.b64encode(f"{login}:{password}".encode()).decode()
    payload = json.dumps([{"target": domain, "internal_list_limit": 1, "backlinks_status_type": "live"}])
    status, text = fetch(
        "https://api.dataforseo.com/v3/backlinks/summary/live",
        data=payload,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        timeout=60,
    )
    if status != 200:
        return {"error": f"dataforseo HTTP {status}: {text[:160]}"}
    try:
        r = json.loads(text)["tasks"][0]["result"][0]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {"error": f"dataforseo returned no result: {text[:200]}"}
    rank = r.get("rank")
    return {
        "source": "dataforseo",
        "confidence": "high",
        "dr": round(rank / 10) if rank is not None else None,
        "raw_rank": rank,
        "referring_domains": r.get("referring_domains"),
        "backlinks": r.get("backlinks"),
        "spam_score": r.get("spam_score"),
    }


def read_secret(env_name: str, path: str):
    """Env first, then a chmod-600 dotfile. Never a committed file."""
    val = os.environ.get(env_name, "").strip()
    if val:
        return val
    try:
        p = os.path.expanduser(path)
        if os.path.isfile(p):
            return open(p).read().strip()
    except OSError:
        pass
    return ""


# OPR is a 0-10 log-scale PageRank over the Common Crawl link graph. x10 lines
# it up with the 0-100 DR-equivalent scale the zones use. It is not Ahrefs DR
# and will disagree at the edges; for picking a KD ceiling it is close enough,
# and it is free.
def _opr_dr(decimal):
    try:
        return round(float(decimal) * 10)
    except (TypeError, ValueError):
        return None


def _opr_trend(history):
    """Is this domain's authority climbing, flat, or collapsing?

    History is monthly since 2018. Compare the newest point against ~12 months
    back. Returns None when there is not enough history to say - which is a
    different thing from 'flat', and must stay different.
    """
    pts = [h for h in (history or []) if h.get("open_page_rank") is not None]
    if len(pts) < 13:
        return None
    now, then = pts[-1], pts[-13]
    try:
        delta = round(float(now["open_page_rank"]) - float(then["open_page_rank"]), 2)
    except (TypeError, ValueError, KeyError):
        return None
    return {
        "delta_12mo": delta,
        "direction": "rising" if delta > 0.05 else "falling" if delta < -0.05 else "flat",
        "from": then.get("date"),
        "to": now.get("date"),
    }


def from_openpagerank(domain: str):
    key = read_secret("OPENPAGERANK_API_KEY", "~/.openpagerank_key")
    if not key:
        return None
    rows = openpagerank_bulk([domain], key)
    if isinstance(rows, dict) and rows.get("error"):
        return rows
    row = (rows or [{}])[0]

    # A domain the link graph has never seen is NOT authority zero - it is NO
    # ANSWER. Returning dr=0 here would be a fabricated measurement that the
    # quality bar would then act on, so this falls through to the estimate
    # exactly like a failed call does.
    if not row.get("found"):
        return {"error": f"openpagerank: {domain} is not in the Common Crawl link graph (no verdict)"}

    return {
        "source": "openpagerank",
        "confidence": "medium",
        "dr": _opr_dr(row.get("open_page_rank")),
        "page_rank_decimal": row.get("open_page_rank"),
        "opr_rank": row.get("rank"),
        # The only free referring-domain count anywhere in this skill.
        "referring_domains": row.get("referring_domains"),
        "trend": _opr_trend(row.get("history")),
        "as_of": row.get("_as_of"),
        "endpoint": row.get("_endpoint"),
    }


def _reconcile(requested, returned):
    """Put the response back in step with the request.

    Two measured behaviours make a naive read of this API wrong, and both fail
    silently (2026-08-01):

    1. ROWS CAN BE OMITTED. `httpbin.org` sent alone came back with an EMPTY
       results array - not `found: false`, simply absent. So the response is
       NOT positionally aligned with the request, and any caller zipping the
       two by index attributes one domain's authority to another. That is a
       wrong number that looks entirely plausible.

    2. SUBDOMAINS ARE SILENTLY NORMALISED TO THE APEX. Asking for
       `search.marginalia.nu` returns a row for `marginalia.nu`. Taken at face
       value you have just credited a subdomain with its parent's authority,
       which on any large host is an enormous overestimate.

    So: return exactly one row per requested domain, in order; mark an omitted
    one `found: false` with `no_data: true` (absent from the response, which is
    weaker than an explicit not-found); and where the API answered about a
    DIFFERENT name than the one asked for, keep the answer but record
    `answered_for` so the substitution is visible rather than assumed.
    """
    by_name = {}
    for r in returned:
        if r.get("domain"):
            by_name[r["domain"].lower()] = r
    out = []
    for d in requested:
        key = d.lower()
        row = by_name.get(key)
        if row is None:
            # The apex, in case this was a subdomain the API folded upward.
            parts = key.split(".")
            for i in range(1, len(parts) - 1):
                cand = ".".join(parts[i:])
                if cand in by_name:
                    row = dict(by_name[cand])
                    row["answered_for"] = cand
                    row["requested"] = d
                    break
        if row is None:
            out.append({"domain": d, "found": False, "no_data": True,
                        "open_page_rank": None, "rank": None,
                        "referring_domains": None, "history": None,
                        "note": "omitted from the API response entirely - no data, not a measured zero"})
        else:
            out.append(row)
    return out


def openpagerank_bulk(domains, key=None):
    """Score up to 100 domains in one call. Used for competitor benchmarking too.

    Returns a list of rows each carrying `found`, or {"error": ...}. Both key
    generations are normalised to the SAME row shape so callers never branch.
    """
    key = key or read_secret("OPENPAGERANK_API_KEY", "~/.openpagerank_key")
    if not key:
        return {"error": "no OPENPAGERANK_API_KEY (and no ~/.openpagerank_key)"}
    domains = [clean_domain(d) for d in domains if d]
    if not domains:
        return []

    if key.startswith("opr_"):
        body = json.dumps({"domains": domains[:100], "include_history": True})
        status, text = fetch(
            "https://openpagerank.keywordseverywhere.com/v1/domains/bulk",
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=40,
        )
        if status != 200:
            return {"error": f"openpagerank HTTP {status}: {text[:200]}"}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"error": f"openpagerank: unparseable response: {text[:200]}"}
        out = []
        for r in payload.get("results", []):
            r["_as_of"] = payload.get("as_of")
            r["_endpoint"] = "keywordseverywhere"
            out.append(r)
        return _reconcile(domains[:100], out)

    # Legacy domcop endpoint. Retires 2026-09-30; normalise it into the new shape.
    qs = urllib.parse.urlencode([("domains[]", d) for d in domains[:100]])
    status, text = fetch(f"https://openpagerank.com/api/v1.0/getPageRank?{qs}", headers={"API-OPR": key})
    if status != 200:
        return {"error": f"openpagerank(legacy) HTTP {status}: {text[:200]}"}
    try:
        resp = json.loads(text).get("response", [])
    except json.JSONDecodeError:
        return {"error": f"openpagerank(legacy): unparseable response: {text[:200]}"}
    return _reconcile(domains[:100], [
        {
            "domain": r.get("domain"),
            "found": str(r.get("status_code")) == "200",
            "open_page_rank": r.get("page_rank_decimal"),
            "rank": r.get("rank"),
            "referring_domains": None,  # the legacy endpoint never returned this
            "history": None,
            "_endpoint": "domcop-legacy",
        }
        for r in resp
    ])


def from_cloudflare_radar(domain: str):
    """Popularity bucket from Cloudflare's 1.1.1.1 resolver traffic.

    A genuinely INDEPENDENT second opinion: OPR measures the link graph,
    Radar measures how many real people resolve the name. A site can be
    absent from one and present in the other, which is exactly what makes it
    worth carrying - our own domain is `found: false` at OPR but Radar still
    places it in a bucket.

    Any Cloudflare API token works; no special scope is needed for Radar.
    This is a SIGNAL, never the DR - it is a rank bucket, not a 0-100 score,
    and converting it into one would be inventing precision that is not there.
    """
    key = read_secret("CLOUDFLARE_API_TOKEN", "~/.cloudflare_token")
    if not key:
        return None
    status, text = fetch(
        f"https://api.cloudflare.com/client/v4/radar/ranking/domain/{urllib.parse.quote(domain)}",
        headers={"Authorization": f"Bearer {key}"},
    )
    if status != 200:
        return {"error": f"cloudflare radar HTTP {status}: {text[:160]}"}
    try:
        d = json.loads(text)
        if not d.get("success"):
            return {"error": f"cloudflare radar: {json.dumps(d.get('errors'))[:160]}"}
        det = d["result"]["details_0"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return {"error": f"cloudflare radar: unexpected shape: {text[:160]}"}
    return {
        "source": "cloudflare-radar",
        "rank": det.get("rank"),
        "bucket": det.get("bucket"),
        "categories": [c.get("name") for c in (det.get("categories") or [])],
    }


def domain_age_days(domain: str):
    """RDAP - the modern WHOIS. Free, keyless, no account."""
    labels = [x for x in domain.split(".") if x]
    candidates = [domain] + ([".".join(labels[-2:])] if len(labels) > 2 else [])
    for d in candidates:
        # A browser User-Agent gets 403'd by the .com registry that rdap.org
        # redirects to (measured, not assumed - the Chrome UA fails, a plain
        # tool UA succeeds). RDAP is a machine API; identify as a machine.
        status, text = fetch(
            f"https://rdap.org/domain/{urllib.parse.quote(d)}",
            headers={"Accept": "application/rdap+json, application/json", "User-Agent": "seo-manager/1.0"},
            timeout=15,
        )
        if status != 200:
            continue
        try:
            events = json.loads(text).get("events", [])
        except json.JSONDecodeError:
            continue
        for e in events:
            if e.get("eventAction") == "registration" and e.get("eventDate"):
                try:
                    ts = datetime.fromisoformat(e["eventDate"].replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return max(0, (datetime.now(timezone.utc) - ts).days), e["eventDate"]
    return None, None


def indexed_page_count(domain: str):
    """How many pages the site actually has live, from its own sitemap.

    Deliberately NOT a `site:` operator query - those are unreliable, blocked
    from datacenter IPs, and Google has said for years the count is an
    estimate. The sitemap is the site's own claim and it is free to read.
    """
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"):
        status, text = fetch(f"https://{domain}{path}", timeout=20)
        if status != 200 or "<" not in text:
            continue
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text)
        if "<sitemapindex" in text:
            total, checked = 0, 0
            for child in locs[:10]:
                s2, t2 = fetch(child, timeout=20)
                if s2 == 200:
                    total += len(re.findall(r"<loc>", t2))
                    checked += 1
            if total:
                return {"pages": total, "via": f"{path} (sitemap index, {checked} children read)",
                        "partial": len(locs) > 10}
        elif locs:
            return {"pages": len(locs), "via": path, "partial": False}
    return None


def from_estimate(domain: str, gsc_impressions=None, gsc_clicks=None):
    """Keyless composite. Conservative on purpose and capped at 25.

    Being wrong DOWNWARD costs a few keywords a stronger site could have won -
    the next run picks them up. Being wrong UPWARD queues keywords the site
    loses on, burns build slots, and buries the evidence under three weeks of
    settling time. So the estimate never opens the DR-35 ceiling.
    """
    age_days, registered = domain_age_days(domain)
    sitemap = indexed_page_count(domain)
    pages = (sitemap or {}).get("pages")

    parts = []
    score = 0.0
    if age_days is not None:
        years = age_days / 365.25
        # Age alone is weak evidence but it is real: a 2020 domain has had time
        # to earn links a 2026 one has not.
        age_pts = min(10.0, years * 2.0)
        score += age_pts
        parts.append({"signal": "domain_age", "value": f"{years:.1f} years", "points": round(age_pts, 1)})
    if pages:
        # Content mass: a site with 200 live URLs is further along than one
        # with 5, regardless of links.
        size_pts = min(8.0, (pages ** 0.5) / 2.0)
        score += size_pts
        parts.append({"signal": "live_pages", "value": pages, "points": round(size_pts, 1)})
    if gsc_impressions:
        # The strongest free signal available: Google is already showing this
        # site to people. 10k impressions/28d is a real footprint.
        imp_pts = min(9.0, (gsc_impressions / 1000.0) ** 0.5 * 2.0)
        score += imp_pts
        parts.append({"signal": "gsc_impressions", "value": gsc_impressions, "points": round(imp_pts, 1)})
    if gsc_clicks:
        click_pts = min(6.0, (gsc_clicks / 100.0) ** 0.5 * 2.0)
        score += click_pts
        parts.append({"signal": "gsc_clicks", "value": gsc_clicks, "points": round(click_pts, 1)})

    dr = min(25, round(score))
    return {
        "source": "estimate",
        "confidence": "low",
        "dr": dr,
        "capped_at": 25,
        "components": parts,
        "domain_registered": registered,
        "sitemap": sitemap,
        "caveat": (
            "This is a keyless COMPOSITE, not a measured link-graph score, and it is capped at 25 "
            "so it can never unlock the DR-35 KD ceiling. Treat it as a floor on what you know. "
            "For a real number set OPENPAGERANK_API_KEY (free, ~5 minutes, 30k domains/month: "
            "https://openpagerank.keywordseverywhere.com/) or DataForSEO credentials. "
            "NOTE: a real OPR lookup can also come back with no verdict at all - a domain "
            "absent from the Common Crawl link graph gets no score, and that is not a zero."
        ),
    }


# --------------------------------------------------------------------- main


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--domain", required=True)
    p.add_argument(
        "--bulk",
        help="comma-separated EXTRA domains to score alongside --domain in one Open PageRank call "
        "(up to 100 total). Competitor benchmarking: who on page 1 actually outranks you.",
    )
    p.add_argument("--source", default="auto", choices=["auto", "dataforseo", "openpagerank", "estimate"])
    p.add_argument("--gsc-impressions", type=int, help="last 28d impressions, from the search-console skill")
    p.add_argument("--gsc-clicks", type=int, help="last 28d clicks")
    p.add_argument("--save", action="store_true", help="write dr into .seo/config.json via seostate.py")
    p.add_argument("--root", help="repo root for --save")
    a = p.parse_args()

    domain = clean_domain(a.domain)
    attempts = []
    result = None
    order = ["dataforseo", "openpagerank", "estimate"] if a.source == "auto" else [a.source]
    for src in order:
        got = {"dataforseo": from_dataforseo, "openpagerank": from_openpagerank}.get(src)
        got = got(domain) if got else from_estimate(domain, a.gsc_impressions, a.gsc_clicks)
        if got is None:
            attempts.append({"source": src, "skipped": "no credentials configured"})
            continue
        if got.get("error"):
            attempts.append({"source": src, "error": got["error"]})
            continue
        if got.get("dr") is None:
            attempts.append({"source": src, "error": "source answered but returned no rank"})
            continue
        result = got
        break

    if result is None:
        result = from_estimate(domain, a.gsc_impressions, a.gsc_clicks)
        attempts.append({"source": "estimate", "note": "used as the last resort"})

    dr = result["dr"]
    payload = {
        "ok": True,
        "domain": domain,
        "dr_equivalent": dr,
        **result,
        "attempts": attempts,
        "kd_zones": kd_zones(dr),
        "volume_band": volume_band(dr),
        "reading": (
            f"DR-equivalent {dr}: auto-approve guides under KD {kd_zones(dr)['auto_approve_below']}, "
            f"leave KD {kd_zones(dr)['auto_approve_below']}-{kd_zones(dr)['pending_below']} pending "
            f"only with strong SERP weakness. Volume band "
            f"{volume_band(dr)['floor']}-{volume_band(dr)['ceiling'] or 'unbounded'}. "
            "KD is an INPUT, never the verdict - the measured authority count on page 1 overrules it "
            "once you have looked."
        ),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    radar = from_cloudflare_radar(domain)
    if radar is not None:
        # Deliberately alongside the DR, never folded into it.
        payload["popularity"] = radar

    if a.bulk:
        extra = [d for d in (x.strip() for x in a.bulk.split(",")) if d]
        rows = openpagerank_bulk([domain] + extra)
        if isinstance(rows, dict) and rows.get("error"):
            payload["bulk"] = {"ok": False, "error": rows["error"]}
        else:
            # `found: false` stays visible as its own state. Rendering it as 0
            # would put an unmeasured competitor at the bottom of the table and
            # read as "weaker than us", which is the opposite of unknown.
            payload["bulk"] = {
                "ok": True,
                "rows": [
                    {
                        "domain": r.get("domain"),
                        "found": bool(r.get("found")),
                        "dr_equivalent": _opr_dr(r.get("open_page_rank")) if r.get("found") else None,
                        "referring_domains": r.get("referring_domains"),
                        "trend": _opr_trend(r.get("history")),
                    }
                    for r in rows
                ],
                "note": "found=false means absent from the Common Crawl link graph - no verdict, NOT a zero.",
            }
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if a.save:
        import subprocess

        here = os.path.dirname(os.path.abspath(__file__))
        cmd = [sys.executable, os.path.join(here, "seostate.py")]
        if a.root:
            cmd += ["--root", a.root]
        cmd += ["config", "--set", f"dr={dr}", "--set", f'dr_fetched_at="{payload["checked_at"]}"',
                "--set", f'dr_source="{result["source"]}"']
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
