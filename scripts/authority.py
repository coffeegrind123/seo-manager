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
  openpagerank Open PageRank (domcop). FREE API key, 1000 req/day, built from
               Common Crawl's link graph. 0-10 scale -> x10 -> DR-equivalent.
               Register at https://www.domcop.com/openpagerank/ - it is the
               single best free substitute and takes two minutes.
               BYO OPENPAGERANK_API_KEY.
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


def from_openpagerank(domain: str):
    key = os.environ.get("OPENPAGERANK_API_KEY", "").strip()
    if not key:
        return None
    qs = urllib.parse.urlencode({"domains[0]": domain})
    status, text = fetch(f"https://openpagerank.com/api/v1.0/getPageRank?{qs}", headers={"API-OPR": key})
    if status != 200:
        return {"error": f"openpagerank HTTP {status}: {text[:160]}"}
    try:
        row = json.loads(text)["response"][0]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {"error": f"openpagerank returned no row: {text[:200]}"}
    if str(row.get("status_code")) != "200":
        return {"error": f"openpagerank: {row.get('error') or 'domain not found in the index'}"}
    decimal = row.get("page_rank_decimal")
    try:
        dr = round(float(decimal) * 10)
    except (TypeError, ValueError):
        dr = None
    return {
        "source": "openpagerank",
        "confidence": "medium",
        # OPR is a 0-10 log-scale PageRank over the Common Crawl link graph.
        # x10 lines it up with the 0-100 DR-equivalent scale the zones use.
        # It is not Ahrefs DR and will disagree at the edges; for picking a KD
        # ceiling it is close enough, and it is free.
        "dr": dr,
        "page_rank_decimal": decimal,
        "opr_rank": row.get("rank"),
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
            "For a real number set OPENPAGERANK_API_KEY (free, 2 minutes: "
            "https://www.domcop.com/openpagerank/) or DataForSEO credentials."
        ),
    }


# --------------------------------------------------------------------- main


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--domain", required=True)
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
