#!/usr/bin/env python3
"""
render-advanced.py — turn run-seo-manager.py's JSON into the HTML fragments for
the advanced report sections, in the template's own markup.

These sections are DATA: tables of measured values with a status each. Writing
them by hand from a 1 MB JSON is where a report drifts from its evidence, so
they are rendered mechanically; the model still writes the summary verdict,
the priority actions and the insight cards, using `summary` from this output.

Status rules (one place):
    critical / high finding        -> Fail
    medium finding                 -> Warning
    low / info finding             -> Info
    no finding, instrument ran     -> Pass
    instrument did not run         -> Not measured (never Pass), with the reason

Usage:
    python3 render-advanced.py <run-seo-manager.json> [--out fragments.json]
                               [--fill reports/<name>-full-audit.html]

--fill replaces the advanced {{placeholders}} in a copy of the template in
place and leaves the core ones ({{summary_*}}, {{priority_actions_html}}, ...)
for the model. It refuses if an advanced placeholder is missing from the file.

Output JSON: {"fragments": {placeholder: html, ...},
              "summary": {"critical": [...], "warnings": [...], "passing": [...]},
              "scorecard": [{"section", "status", "fail", "warn", "pass"}]}
"""

import argparse
import html
import json
import sys
from pathlib import Path

SEV_STATUS = {"critical": "fail", "high": "fail", "medium": "warn", "low": "info", "info": "info"}
LABEL = {"pass": "Pass", "warn": "Warning", "fail": "Fail", "info": "Info", "na": "Not measured"}
RANK = {"fail": 3, "warn": 2, "info": 1, "pass": 0, "na": -1}
NOT_RUN = {"no_key": "no credential", "cannot_ask": "could not ask", "error": "instrument error",
           "timeout": "timed out", "skipped": "skipped", "not_installed": "seo-manager not installed"}

# Core Web Vitals thresholds (web.dev/articles/vitals): good / poor at p75.
CWV = {"largest_contentful_paint": ("LCP", 2500, 4000, "ms"),
       "interaction_to_next_paint": ("INP", 200, 500, "ms"),
       "cumulative_layout_shift": ("CLS", 0.1, 0.25, "")}


def e(x) -> str:
    return html.escape(str(x if x is not None else ""), quote=True)


class Section:
    def __init__(self, title: str):
        self.title, self.parts, self.rows = title, [], []

    def label(self, text: str):
        self.parts.append(f'<div class="subsection-label">{e(text)}</div>')

    def note(self, text: str):
        self.parts.append(f'<p class="inventory-note">{e(text)}</p>')

    def table(self, rows: list):
        """rows: (check, status, detail) where detail is a str (pass/info/na)
        or (issues: list[str], fix: str|None) for warn/fail."""
        body = []
        for check, status, detail in rows:
            self.rows.append((check, status, detail))
            if isinstance(detail, tuple):
                issues, fix = detail
                cell = "".join(f'<div class="detail-issue">· {e(i)}</div>' for i in issues[:2])
                cell += f'<div class="detail-fix">{e(fix)}</div>' if fix else ""
            else:
                cell = e(detail)
            body.append(f'<tr><td>{e(check)}</td><td><span class="status status-{status}">'
                        f'{LABEL[status]}</span></td><td>{cell}</td></tr>')
        self.parts.append('<div class="table-scroll"><table class="check-table"><thead><tr><th>Check'
                          '</th><th>Status</th><th>Detail</th></tr></thead><tbody>'
                          + "".join(body) + "</tbody></table></div>")

    def data_table(self, headers: list, rows: list):
        head = "".join(f"<th>{e(h)}</th>" for h in headers)
        body = "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in r) + "</tr>" for r in rows)
        self.parts.append(f'<div class="table-scroll"><table class="check-table"><thead><tr>{head}'
                          f'</tr></thead><tbody>{body}</tbody></table></div>')

    def html(self) -> str:
        return "\n".join(self.parts)

    def status(self) -> str:
        seen = {s for _c, s, _d in self.rows}
        for s in ("fail", "warn", "pass", "info"):
            if s in seen:
                return s
        return "na"


def got(inst: dict, name: str):
    """(data, None) when the instrument ran, else (None, 'reason')."""
    r = inst.get(name) or {"state": "skipped", "reason": "not run"}
    if r.get("state") == "ok":
        return r.get("data") or {}, None
    if r.get("state") == "not_applicable":
        return None, "not applicable: " + str(r.get("reason") or "")
    why = r.get("reason") or (r.get("data") or {}).get("reason") or r.get("output_tail") or ""
    return None, f"{NOT_RUN.get(r.get('state'), r.get('state'))}" + (f" - {str(why)[:160]}" if why else "")


def finding_rows(findings: list, label=None) -> list:
    rows = []
    for f in findings or []:
        st = SEV_STATUS.get(f.get("severity"), "info")
        what = f.get("detail") or f.get("rule")
        where = f.get("url") or f.get("path")
        issue = f"{what}" + (f" ({where})" if where and where not in str(what) else "")
        rows.append((label or f.get("rule", "finding").replace("_", " "), st,
                     ([issue], f.get("fix")) if st in ("warn", "fail") else issue))
    return rows


def worst_or_pass(check: str, findings: list, pass_detail: str) -> list:
    rows = finding_rows(findings, check)
    return rows if rows else [(check, "pass", pass_detail)]


# ------------------------------------------------------------------ sections

# `sitecheck all` hoists every part's findings to the top level; each is
# routed back to the part that raised it by its rule name.
def _part_of(rule: str) -> str:
    if rule.startswith(("host_", "http_serves")):
        return "hosts"
    if rule.startswith(("soft_404", "unknown_url")):
        return "soft404"
    if rule.startswith("sitemap_"):
        return "sitemap"
    if rule.startswith("canonical_"):
        return "canonicals"
    if rule.startswith(("redirect_", "temporary_redirect", "variant")):
        return "variants"
    return "headers"


def indexability(inst) -> Section:
    s = Section("Indexability & Directives")
    d, why = got(inst, "sitecheck")
    s.label("Site-level crawl and canonical health")
    if why:
        s.table([("Site-level checks", "na", why)])
    else:
        parts = d.get("parts") or {}
        labels = {"hosts": "Host variants (http/https, www)", "soft404": "Soft 404 probe",
                  "headers": "Security & framing headers", "sitemap": "Sitemap sample",
                  "canonicals": "Canonical targets (sitemap sample)", "variants": "URL variants"}
        rows = []
        for key, label in labels.items():
            part = parts.get(key)
            plist = part if isinstance(part, list) else [part] if part else []
            if not plist:
                rows.append((label, "na", "not run"))
                continue
            if any(p_.get("ok") is False for p_ in plist):
                rows.append((label, "na", "; ".join(str(p_.get("reason") or p_.get("error") or "refused")
                                                    for p_ in plist if p_.get("ok") is False)[:200]))
                continue
            fs = [f for p_ in plist for f in p_.get("findings") or []] + \
                [f for f in d.get("findings") or [] if _part_of(f.get("rule", "")) == key]
            if key == "soft404":
                ok_ = plist[0].get("status") in (404, 410)
                rows += fs and finding_rows(fs, label) or [(label, "pass" if ok_ else "fail",
                                                            plist[0].get("verdict") if ok_ else
                                                            ([f"a missing URL answers {plist[0].get('verdict')}"],
                                                             "Return 404/410 for URLs that do not exist."))]
                continue
            detail = {"hosts": f"{len(plist[0].get('variants') or [])} variants reach one origin",
                      "headers": "no header finding",
                      "sitemap": "; ".join(f"{p_.get('entries')} entries, {p_.get('audited')} audited"
                                          for p_ in plist),
                      "canonicals": f"{plist[0].get('checked')} pages checked, none broken",
                      "variants": f"{plist[0].get('checked')} variant URLs checked"}[key]
            fs = list({(f.get("rule"), f.get("url")): f for f in fs}.values())
            rows += worst_or_pass(label, fs, detail)
        s.table(rows)
    d, why = got(inst, "redirect_chain")
    s.label("This page's URL variants")
    s.table([("Redirects to this page", "na", why)] if why else
            worst_or_pass("Redirects to this page", d.get("findings"),
                          f"{d.get('checked')} variants reach the page cleanly"))
    d, why = got(inst, "agentcheck")
    s.label("Robots directives as each engine reads them")
    if why:
        s.table([("Directives", "na", why)])
    else:
        cu = (d.get("page") or {}).get("content_use") or {}
        g = cu.get("google") or {}
        dirs = g.get("directives") or []
        noidx = "noindex" in dirs
        s.table([
            ("Googlebot (meta + X-Robots-Tag)", "fail" if noidx else "pass",
             ([f"Google reads: {', '.join(dirs)}"], "Remove noindex if this page should rank.")
             if noidx else (", ".join(dirs) or "no directives - indexable")),
            ("Bingbot", "warn" if (cu.get("bing") or {}).get("bing_chat") != "allowed" else "pass",
             f"Bing Chat use: {(cu.get('bing') or {}).get('bing_chat')}"),
        ])
    return s


def ai_readiness(inst) -> Section:
    s = Section("AI & Agent Readiness")
    d, why = got(inst, "agentcheck")
    if why:
        s.table([("AI & agent checks", "na", why)])
        return s
    pol = d.get("policy") or {}
    s.label("AI crawler access (robots.txt, per crawler)")
    summ = pol.get("summary") or {}
    meaning = {"ai_search": "answer-engine indexing", "ai_user": "fetches a person asked for",
               "ai_training": "model training"}
    rows = []
    for cat in ("ai_search", "ai_user", "ai_training"):
        v = summ.get(cat) or {}
        if not v:
            continue
        blocked = [b["bot"] for b in pol.get("bots") or [] if b.get("category") == cat
                   and b.get("allowed") is False]
        st = "pass" if not blocked else ("info" if cat == "ai_training" else "warn")
        detail = (f"{v.get('allowed')}/{v.get('total')} allowed" if not blocked else
                  (f"blocked: {', '.join(blocked[:8])}" if st == "info" else
                   ([f"blocked: {', '.join(blocked[:8])}"],
                    "Allow answer-engine and user-triggered crawlers unless that is a deliberate choice: "
                    "they are how assistants cite and fetch the page.")))
        rows.append((f"{cat} - {meaning[cat]}", st, detail))
    rows += finding_rows(pol.get("findings"))
    s.table(rows or [("robots.txt", "na", "no policy rows")])
    reach = d.get("reach") or {}
    s.label("What each AI crawler is actually served")
    by = reach.get("by_state") or {}
    if reach.get("state") != "measured":
        s.table([("Served vs refused", "na", reach.get("reason") or reach.get("state") or "not measured")])
    else:
        refused = [b for k, v in by.items() if k != "served" for b in v]
        s.table(finding_rows(reach.get("findings")) or [
            ("Served vs refused", "pass" if not refused else "warn",
             f"{len(by.get('served') or [])} crawler user agents served the page" if not refused else
             ([f"not served to: {', '.join(refused[:8])}"],
              "Check the WAF/CDN bot rules for those user agents."))])
    cu = (d.get("page") or {}).get("content_use") or {}
    g, b, t = cu.get("google") or {}, cu.get("bing") or {}, cu.get("tdm") or {}
    s.label("What engines may DO with the page")
    gs = {"eligible": "pass", "limited": "warn", "ineligible": "fail"}.get(g.get("ai_features"), "na")
    s.table([
        ("Google AI Overviews / AI Mode eligibility", gs,
         "indexed + snippet-eligible" if gs == "pass" else
         ([f"{g.get('ai_features')}: {', '.join(g.get('why') or []) or 'max-snippet or data-nosnippet limits it'}",
           f"{g.get('data_nosnippet_share') or 0:.0%} of words inside data-nosnippet"],
          "Remove nosnippet / max-snippet:0 / noindex if the page should appear in AI answers.")),
        ("Bing Chat", "pass" if b.get("bing_chat") == "allowed" else "info",
         {"allowed": "full use", "url_title_snippet": "NOCACHE: url, title and snippet only",
          "excluded": "NOARCHIVE: excluded from answers"}.get(b.get("bing_chat"), "unknown")),
        ("TDMRep (text & data mining reservation)", "info",
         "no reservation stated" if t.get("reservation") is None else
         f"reservation={t.get('reservation')} (from {t.get('source')})"
         + (f" - {t.get('error')}" if t.get("error") else "")),
    ])
    s.label("Machine-readable surfaces")
    llms, disc = d.get("llms") or {}, d.get("discovery") or {}
    s.table(worst_or_pass("llms.txt", llms.get("findings"),
                          ("present: " + ", ".join(llms.get("present"))) if llms.get("present")
                          else "not present (optional; Google does not use it)")
            + worst_or_pass("/.well-known discovery documents", disc.get("findings"),
                            "no malformed discovery document"))
    page = d.get("page") or {}
    s.label("Agent usability of this page")
    tb, st_ = page.get("token_budget") or {}, page.get("structure") or {}
    lm = st_.get("landmarks") or {}
    s.table(worst_or_pass("Static HTML, landmarks, labels, links", page.get("findings"),
                          f"~{tb.get('estimated_tokens')} tokens of text; landmarks: "
                          + ", ".join(k for k, v in lm.items() if v)))
    return s


def international(inst) -> Section:
    s = Section("International (hreflang)")
    d, why = got(inst, "hreflang_audit")
    s.label("The annotation mesh")
    if why:
        s.table([("hreflang mesh", "na", why)])
    elif not d.get("locales"):
        s.table([("hreflang mesh", "na", "no hreflang annotations - single-language site")])
    else:
        s.table(worst_or_pass("hreflang mesh", d.get("findings"),
                              f"{len(d.get('locales'))} codes across {d.get('pages_read')} pages read; "
                              "self-reference, return tags and x-default hold"))
    d, why = got(inst, "hreflang_redirects")
    s.label("Can every alternate be reached (no automatic redirects)")
    s.table([("Language / Googlebot redirects", "na", why)] if why else
            worst_or_pass("Language / Googlebot redirects", d.get("findings"),
                          f"{len(d.get('alternates') or [])} alternates served the same to every client"))
    return s


PSI_CATEGORY = {"FAST": "pass", "AVERAGE": "warn", "SLOW": "fail"}
FIX = {"LCP": "Prioritise the hero (fetchpriority=high), cut render-blocking CSS/JS.",
       "INP": "Break up long tasks on interaction; defer third-party scripts.",
       "CLS": "Reserve space for images, embeds and late UI."}


def _history_rows(mets: dict, scope: str) -> list:
    rows = []
    for key, (short, good, poor, unit) in CWV.items():
        m = mets.get(key) or {}
        v = m.get("latest_p75")
        if v is None:
            rows.append((f"{short} p75 ({scope})", "na", "no field data"))
            continue
        st = "pass" if v <= good else ("warn" if v <= poor else "fail")
        val = f"{v:g}{unit}"
        tnote = f"trend: {m.get('trend')}" + (f" ({m.get('why')})" if m.get("why") else "")
        rows.append((f"{short} p75 ({scope})", st, f"{val} - {tnote}" if st == "pass" else
                     ([f"p75 {val} (good <= {good:g}{unit}, poor > {poor:g}{unit})", tnote], FIX[short])))
    return rows


def field_vitals(inst) -> Section:
    """Two scopes, never merged: THIS URL (PSI's CrUX record, all devices,
    28 days) and the WHOLE ORIGIN on phones with its weekly trend (CrUX
    History). They can disagree by an order of magnitude - measured
    2026-10-25: a homepage INP p75 of 201 ms on an origin whose phone INP p75
    was 4,964 ms - so each row says which one it is."""
    s = Section("Field Core Web Vitals (real users)")
    pc, why = got(inst, "pagecheck")
    fc = ((pc or {}).get("vitals") or {}).get("field_crux") or {}
    s.label("This URL - all devices, last 28 days (CrUX via PageSpeed Insights)")
    if why or not fc:
        s.table([("Field data for this URL", "na", why or
                  "no URL-level CrUX record - too little Chrome traffic on this exact URL")])
    else:
        rows = []
        for k, short in (("lcp", "LCP"), ("inp", "INP"), ("cls", "CLS")):
            m = fc.get(k)
            if not m:
                rows.append((f"{short} p75 (this URL)", "na", "no field data"))
                continue
            v = m.get("p75")
            val = f"{v / 100:g}" if short == "CLS" else f"{v:g}ms"   # PSI sends CLS x100
            st = PSI_CATEGORY.get(m.get("category"), "info")
            rows.append((f"{short} p75 (this URL)", st, f"{val} ({m.get('category')})" if st == "pass" else
                         ([f"p75 {val} - {m.get('category')}"], FIX[short])))
        s.table(rows)
    shown = False
    for name, scope in (("vitals_history", "url"), ("vitals_origin_history", "origin")):
        d, why = got(inst, name)
        if why or d.get("state") != "answered" or scope not in d:
            continue
        mets = d.get("metrics") or {}
        if not any(m.get("latest_p75") is not None for m in mets.values()):
            continue
        s.label(("This URL" if scope == "url" else "Whole origin")
                + f" - {str(d.get('form_factor', 'PHONE')).lower()}s, weekly 28-day p75 (CrUX History)")
        s.table(_history_rows(mets, "this URL, phones" if scope == "url" else "origin, phones"))
        shown = True
    if not shown:
        s.label("Whole origin - weekly trend (CrUX History)")
        s.table([("Origin field history", "na", got(inst, "vitals_origin_history")[1]
                  or "no CrUX history record - too little traffic")])
    d, why = got(inst, "vitals_page")
    s.label("Server and document delivery")
    s.table([("TTFB, size, compression", "na", why)] if why else
            worst_or_pass("TTFB, size, compression", d.get("findings"),
                          f"TTFB {d.get('ttfb_ms')} ms, {d.get('html_kb')} KB HTML, "
                          f"compressed: {d.get('compressed')}"))
    return s


def structured_data(inst) -> Section:
    s = Section("Structured Data & Markup Validity")
    d, why = got(inst, "pagecheck")
    if why:
        s.table([("Rendered schema, HTML validity", "na", why)])
        return s
    sch, h, hist = d.get("schema") or {}, d.get("html") or {}, d.get("history") or {}
    rows = []
    if sch.get("ok"):
        types = ", ".join(f"{k} x{v}" for k, v in (sch.get("types") or {}).items()) or "none"
        rows.append(("Schema in the RENDERED page", "fail" if sch.get("error_count") else "pass",
                     ([f"{sch.get('error_count')} errors: " + "; ".join(str(x.get('message', x))[:120]
                                                                       for x in sch.get("errors") or [])],
                      "Fix the listed properties; invalid markup earns no rich result.")
                     if sch.get("error_count") else f"types: {types}"))
        for dep in sch.get("deprecated") or []:
            rows.append((f"Retired rich-result type: {dep.get('type')}", "info",
                         f"retired {dep.get('retired')} - {dep.get('guidance') or dep.get('detail')}"))
    else:
        rows.append(("Schema in the RENDERED page", "na", sch.get("error") or "not checked"))
    if h.get("ok"):
        n = h.get("errors") or 0
        rows.append(("W3C HTML validity", "warn" if n else "pass",
                     ([f"{n} errors, e.g. line {(h.get('top_errors') or [{}])[0].get('line')}: "
                       f"{(h.get('top_errors') or [{}])[0].get('message', '')[:140]}"],
                      "Fix parser errors that touch <head>; a malformed head can drop canonical or "
                      "robots tags.") if n else "no errors"))
    if hist.get("ok"):
        rows.append(("Archived history (Wayback)", "info",
                     f"{hist.get('distinct_versions_in_window')} distinct versions since "
                     f"{str(hist.get('first_capture'))[:8]}" if hist.get("archived") else "never archived"))
    s.table(rows)
    return s


def content_quality(inst) -> Section:
    s = Section("Content Quality")
    d, why = got(inst, "slop")
    s.label("AI-writing tells (mechanical, not a judgement of the content)")
    if why:
        s.table([("Writing tells", "na", why)])
    else:
        rows = []
        for f in d.get("flagged") or []:
            ex = (f.get("hits") or [{}])[0].get("context", "")
            rows.append((f["rule"].replace("_", " "), "warn",
                         ([f"{f.get('count')} hits (tolerance {f.get('tolerance')}) - {f.get('why')}",
                           f"e.g. \"{ex[:120]}\""], f.get("fix"))))
        for f in d.get("within_tolerance") or []:
            rows.append((f["rule"].replace("_", " "), "info",
                         f"{f.get('count')} hit(s), within tolerance"))
        s.table(rows or [("Writing tells", "pass", f"none across {d.get('words')} words")])
    d, why = got(inst, "extractable")
    s.label("Can an assistant quote a sentence from it")
    s.table([("Liftable lead sentence", "na", why)] if why else
            [("Liftable lead sentence", "pass" if d.get("liftable") else "warn",
              "a self-contained sentence leads the content" if d.get("liftable") else
              ([str((d.get("worst") or [{}])[0])[:200]],
               "Open with one sentence that answers the page's question on its own."))])
    return s


def link_graph(inst) -> Section:
    s = Section("Internal Links")
    t, why = got(inst, "sitegraph_target")
    rep, _w = got(inst, "sitegraph_report")
    br, _w2 = got(inst, "sitegraph_broken")
    dep, _w3 = got(inst, "sitegraph_depth")
    if why:
        crawl_why = got(inst, "sitegraph_crawl")[1]
        s.table([("Link graph", "na", crawl_why or why)])
        return s
    s.label(f"Bounded crawl: {(rep or {}).get('pages')} pages")
    rows = []
    if not t.get("found"):
        rows.append(("This page in the crawl", "warn",
                     ([t.get("note") or "not reached"], "Link to it from a crawled hub page.")))
    else:
        ctx = t.get("contextual_links") or 0
        rows.append(("Inbound internal links", "pass" if ctx else "warn",
                     f"{t.get('inbound_links')} links from {t.get('linking_pages')} pages, "
                     f"{ctx} contextual" if ctx else
                     ([f"{t.get('inbound_links')} links, all from navigation/footer"],
                      "Add links from related body copy; boilerplate links carry little topical signal.")))
        depth = t.get("click_depth")
        rows.append(("Click depth from the homepage", "pass" if depth is not None and depth <= 3 else "warn",
                     f"{depth} click(s)" if depth is not None and depth <= 3 else
                     ([f"depth {depth}"], "Bring it within 3 clicks of the homepage.")))
    if rep:
        n0 = rep.get("pages_with_no_contextual_inlink") or 0
        rows.append(("Pages with no contextual inlink (crawl)", "info" if n0 else "pass",
                     f"{n0} of {rep.get('pages')} crawled pages are linked only from navigation"))
    if br:
        nb = br.get("targets_without_a_page") or 0
        rows.append(("Broken internal link targets", "fail" if nb else "pass",
                     ([f"{nb} targets answered non-200", str((br.get('worst') or [''])[0])[:140]],
                      "Fix or remove the links.") if nb else "none in the crawl"))
        un = br.get("unreadable") or []
        if un:
            rows.append(("Link targets the crawl could not read", "info",
                         f"{len(un)} target(s) timed out or failed to fetch, e.g. {un[0]['url']} - "
                         "unknown, not broken; re-crawl before acting"))
    if dep:
        deep = dep.get("deep_pages") or []
        rows.append((f"Pages deeper than {dep.get('deeper_than')} clicks", "warn" if deep else "pass",
                     ([f"{len(deep)} deep pages, e.g. {deep[0] if deep else ''}"],
                      "Link deep pages from hubs.") if deep else
                     f"all {dep.get('reached')} reached within {max(map(int, (dep.get('by_depth') or {'0': 0}).keys()))} clicks"))
    s.table(rows)
    return s


def search_performance(inst) -> Section:
    s = Section("Search Performance (Search Console & Bing)")
    d, why = got(inst, "gsc_inspect")
    s.label("Google index state (URL Inspection)")
    if why:
        s.table([("Google index state", "na", why)])
    else:
        r = (d.get("results") or [{}])[0]
        idx = r.get("verdict") == "PASS"
        rows = [("Indexed", "pass" if idx else "fail",
                 f"{r.get('coverage')} - last crawled {str(r.get('last_crawled'))[:10]} as {r.get('crawled_as')}"
                 if idx else ([str(r.get("coverage"))], "Fix what the coverage state names, then request indexing."))]
        if r.get("canonical_agrees") is False:
            rows.append(("Google's canonical", "fail",
                         ([f"Google chose {r.get('google_canonical')}, the page declares {r.get('user_canonical')}"],
                          "Make the declared canonical, internal links and sitemap agree on one URL.")))
        elif r.get("canonical_agrees"):
            rows.append(("Google's canonical", "pass", "agrees with the declared canonical"))
        s.table(rows)
    d, why = got(inst, "gsc_queries")
    s.label("Google queries for this page (90 days)")
    if why:
        s.table([("Google queries", "na", why)])
    elif not d.get("rows"):
        s.table([("Google queries", "info", "no impressions in the window")])
    else:
        edge = d.get("data_edge") or {}
        s.note(f"Complete through {edge.get('last_complete_day')} ({edge.get('source')}).")
        s.data_table(["Query", "Clicks", "Impressions", "CTR", "Position"],
                     [[r["keys"][0], int(r.get("clicks") or 0), int(r.get("impressions") or 0),
                       f"{(r.get('ctr') or 0):.1%}", f"{(r.get('position') or 0):.1f}"]
                      for r in d["rows"][:12]])
    d, why = got(inst, "bing_urlinfo")
    s.label("Bing")
    if why:
        s.table([("Bing index record", "na", why)])
    else:
        s.table([("Bing index record", "pass" if d.get("known_to_bing") else "warn",
                  f"discovered {d.get('discovered')}, last crawled {d.get('last_crawled')}"
                  if d.get("known_to_bing") else
                  (["Bing has no record of this exact URL"], "Submit it (IndexNow or Bing Webmaster).") )])
    d, why = got(inst, "bing_pagequeries")
    if not why and d.get("by_query"):
        s.data_table(["Bing query", "Clicks", "Impressions", "Avg position", "Weeks"],
                     [[q.get("query"), q.get("clicks"), q.get("impressions"), q.get("avg_position"),
                       q.get("weeks")] for q in d["by_query"][:10]])
    return s


def ai_visibility(inst) -> Section:
    s = Section("AI Answer Visibility")
    d, why = got(inst, "geo_ask")
    if why:
        s.table([("AI answer engines", "na", why)])
        return s
    s.label(f"Question asked: \"{d.get('query')}\"")
    rows = []
    for r in d.get("results") or []:
        if r.get("state") != "answered" or not r.get("has_answer"):
            continue
        cited, ment = r.get("cited"), bool(r.get("sentences_naming_us"))
        rows.append([r.get("engine"), "yes" if cited else "no", "yes" if ment else "no",
                     "; ".join(r.get("sentences_backed_by_us") or [])[:140] or "-",
                     ", ".join((r.get("competitors_cited") or [])[:4])])
    if rows:
        s.data_table(["Engine", "Cites us", "Names us", "Sentence our page backs", "Cited instead"], rows)
    cited_by = d.get("cited_by") or []
    s.table([("Cited by AI answers", "pass" if cited_by else "warn",
              f"cited by {', '.join(cited_by)}" if cited_by else
              ([f"not cited by {', '.join(d.get('not_cited_by') or [])}",
                "one run per engine - a single observation, not a rate"],
               "Run geo.py sweep with --runs 3+ on the keyword set before acting; study the pages cited instead."))]
            + ([("Engines not asked", "info", ", ".join(d.get("could_not_ask")))]
               if d.get("could_not_ask") else []))
    return s


def pagespeed_fallback(inst) -> str | None:
    """Lighthouse lab numbers from seo-manager's pagecheck, which reaches PSI
    through the service account when no PSI API key is set. Used for
    {{pagespeed_checks_html}} only when check-pagespeed.py refused for want of
    a key. Only the Performance category is available on this route."""
    d, why = got(inst, "pagecheck")
    v = (d or {}).get("vitals") or {}
    if why or not v.get("ok") or v.get("performance_score") is None:
        return None
    score = round(v["performance_score"] * 100)
    st = "pass" if score >= 90 else ("warn" if score >= 50 else "fail")
    lab = v.get("lab") or {}
    cards = "".join(f'<div class="metric-card"><span class="label">{e(n)}</span><span class="value">'
                    f'{e((lab.get(k) or {}).get("display"))}</span></div>' for k, n in (("fcp", "FCP"), ("lcp", "LCP"), ("tbt", "TBT"),
                                                     ("cls", "CLS"), ("speed_index", "Speed Index")))
    return (f'<div class="subsection-label">Lighthouse lab run ({e(v.get("strategy"))}) - via {e(v.get("credential"))}</div>'
            f'<table class="check-table"><tbody><tr><td>Performance score</td><td><span class="status '
            f'status-{st}">{LABEL[st]}</span></td><td>{score} / 100 - Accessibility, Best Practices and SEO '
            f'categories are not returned on this route</td></tr></tbody></table>'
            f'<div class="metric-grid">{cards}</div>'
            f'<p class="inventory-note">Diagnose at https://pagespeed.web.dev/ - lab is one synthetic run; '
            f'the Field Core Web Vitals section is what real users got.</p>')


def coverage(run: dict) -> Section:
    s = Section("Measurement Coverage")
    s.note("Every advanced module below came from a seo-manager instrument. A module that did not "
           "run is shown as Not measured, never as passed.")
    rows = []
    for name, r in sorted((run.get("instruments") or {}).items()):
        st = r.get("state")
        rows.append([name, st, f"{r.get('seconds')}s" if r.get("seconds") is not None else "-",
                     str(r.get("reason") or "")[:140]])
    s.data_table(["Instrument", "State", "Time", "Note"], rows)
    return s


SECTIONS = [("indexability_html", indexability), ("ai_readiness_html", ai_readiness),
            ("international_html", international), ("field_vitals_html", field_vitals),
            ("structured_data_html", structured_data), ("content_quality_html", content_quality),
            ("link_graph_html", link_graph), ("search_performance_html", search_performance),
            ("ai_visibility_html", ai_visibility)]


def render(run: dict) -> dict:
    inst = run.get("instruments") or {}
    frags, summary, card = {}, {"critical": [], "warnings": [], "passing": []}, []
    if run.get("state") == "not_installed":
        for key, _fn in SECTIONS:
            frags[key] = ('<table class="check-table"><tbody><tr><td>Advanced module</td><td>'
                          '<span class="status status-na">Not measured</span></td><td>'
                          + e(run.get("note")) + "</td></tr></tbody></table>")
        frags["coverage_html"] = frags[SECTIONS[0][0]]
        frags["scorecard_html"] = ""
        return {"fragments": frags, "summary": summary, "scorecard": []}
    for key, fn in SECTIONS:
        sec = fn(inst)
        frags[key] = sec.html()
        counts = {"fail": 0, "warn": 0, "pass": 0}
        for check, st, detail in sec.rows:
            if st in counts:
                counts[st] += 1
            item = f"{sec.title}: {check}"
            if st == "fail":
                summary["critical"].append(item)
            elif st == "warn":
                summary["warnings"].append(item)
            elif st == "pass":
                summary["passing"].append(item)
        card.append({"section": sec.title, "status": sec.status(), **counts})
    frags["coverage_html"] = coverage(run).html()
    fb = pagespeed_fallback(inst)
    extra = {"pagespeed_fallback_html": fb} if fb else {}
    frags["scorecard_html"] = (
        '<div class="table-scroll"><table class="check-table"><thead><tr><th>Area</th><th>Status</th>'
        '<th>Fail</th><th>Warn</th><th>Pass</th></tr></thead><tbody>'
        + "".join(f'<tr><td>{e(c["section"])}</td><td><span class="status status-{c["status"]}">'
                  f'{LABEL[c["status"]]}</span></td><td>{c["fail"]}</td><td>{c["warn"]}</td>'
                  f'<td>{c["pass"]}</td></tr>' for c in card)
        + "</tbody></table></div>")
    return {"fragments": frags, "summary": summary, "scorecard": card, **extra}


def main():
    ap = argparse.ArgumentParser(description="advanced report sections from run-seo-manager.py JSON")
    ap.add_argument("run_json")
    ap.add_argument("--out")
    ap.add_argument("--fill", help="report HTML (a template copy) to fill in place")
    a = ap.parse_args()
    try:
        run = json.loads(Path(a.run_json).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        print(json.dumps({"ok": False, "error": f"cannot read {a.run_json}: {ex}"}))
        sys.exit(2)
    out = {"ok": True, **render(run)}
    if a.fill:
        doc = Path(a.fill).read_text(encoding="utf-8")
        missing = [k for k in out["fragments"] if "{{" + k + "}}" not in doc]
        if missing:
            print(json.dumps({"ok": False, "error": "placeholders not in the report - is it a copy of "
                                                    "the current template?", "missing": missing}))
            sys.exit(2)
        for k, v in out["fragments"].items():
            doc = doc.replace("{{" + k + "}}", v)
        Path(a.fill).write_text(doc, encoding="utf-8")
        out["filled"] = a.fill
    text = json.dumps(out, indent=2, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(json.dumps({"ok": True, "out": a.out, "filled": out.get("filled"),
                          "scorecard": out["scorecard"]}, indent=2))
    else:
        print(text)


if __name__ == "__main__":
    main()
