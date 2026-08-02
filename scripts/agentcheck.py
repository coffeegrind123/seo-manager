#!/usr/bin/env python3
"""Can an AI agent READ, UNDERSTAND and ACT ON this site - and is it allowed to.

`crawllog.py` measures which AI crawlers actually came. This measures whether
they are permitted to and what they get when they arrive. The pair answers a
question neither half can: an assistant that never cites you might be blocked,
might be allowed but served a page it cannot parse, or might simply not rate
you - and those need completely different fixes.

  policy     robots.txt resolved PER AI CRAWLER, in the ai_search / ai_user /
             ai_training taxonomy that decides whether a bot can ever cite you
  page       what an agent gets from one URL: agent-UX semantics, token budget,
             whether the content survives without JavaScript, WebMCP tools
  llms       /llms.txt and /llms-full.txt - presence and well-formedness, with
             no citation claim attached to either (see the note below)
  all        the three together for one origin

THE POLICY CHECK IS THE ONE THAT PAYS. Blocking `ai_search` while allowing
`ai_training` is the worst reachable configuration and it is easy to arrive at
by accident, because the "block AI scrapers" advice everywhere treats the two
as one thing. It means models are trained on the site and no assistant can ever
cite it. This tool names that combination explicitly instead of counting bots.

ON /llms.txt - the honest framing, because the myth is load-bearing elsewhere:
Google's own AI-optimization documentation states Google Search IGNORES it, and
a server-log study measured 0.1% of AI-bot requests touching it. It is checked
here for well-formedness and reported as OPTIONALITY, never scored as a
citation or ranking lever. It IS genuinely consumed by AI coding agents reading
library docs, which is a real but different use. Evidence and sources:
`references/agent-readiness.md`.

WHAT THIS CANNOT SEE, and says so rather than guessing: anything that needs a
rendered page. Tap-target size, computed `cursor`, transparent overlays and the
real accessibility tree are all layout facts, and this is a static fetch. Those
are reported `unmeasurable_statically` with the tool that does answer them
(Lighthouse's `agentic-browsing` category), never silently scored as passing.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import http, BROWSER_UA  # noqa: E402
from crawllog import BOTS  # noqa: E402  - one taxonomy, declared once

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

CATEGORY_MEANING = {
    "ai_search": "feeds an assistant that CITES sources - blocking this is what "
                 "makes you uncitable",
    "ai_user":   "a live fetch because a real person asked - blocking this breaks "
                 "the answer for someone already trying to reach you",
    "ai_training": "trains a model. Never cites, never sends traffic. Blocking it "
                   "costs no visibility.",
}


def _finding(sev, rule, detail, fix=None, **extra):
    f = {"severity": sev, "rule": rule, "detail": detail}
    if fix:
        f["fix"] = fix
    f.update(extra)
    return f


# -------------------------------------------------------------- robots.txt


def parse_robots(text: str) -> list[dict]:
    """robots.txt -> groups of {agents, rules}. Consecutive User-agent lines
    share one rule block, which is the part naive parsers get wrong."""
    groups, cur, expecting_agent = [], None, False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if cur is None or not expecting_agent:
                cur = {"agents": [], "rules": []}
                groups.append(cur)
                expecting_agent = True
            cur["agents"].append(value.lower())
        elif field in ("allow", "disallow"):
            if cur is None:
                continue          # directive before any user-agent: ignored
            expecting_agent = False
            cur["rules"].append((field, value))
    return groups


def _match_len(pattern: str, path: str) -> int:
    """Longest-match semantics with * and $, as Google implements them."""
    if pattern == "":
        return -1
    rx = re.escape(pattern).replace(r"\*", ".*")
    if rx.endswith(r"\$"):
        rx = rx[:-2] + "$"
    return len(pattern) if re.match(rx, path) else -1


def allowed(groups: list[dict], ua: str, path: str = "/") -> dict:
    """Google's rule: most-specific UA group wins; within it, longest match wins;
    a tie goes to Allow."""
    ua = ua.lower()
    best, best_len = None, -1
    for g in groups:
        for a in g["agents"]:
            if a == "*":
                if best_len < 0:
                    best, best_len = g, 0
            elif ua.startswith(a) or a in ua:
                if len(a) > best_len:
                    best, best_len = g, len(a)
    if best is None:
        return {"allowed": True, "matched_group": None, "reason": "no matching group"}

    win, win_len, win_dir = True, -1, None
    for field, value in best["rules"]:
        n = _match_len(value, path)
        if n > win_len or (n == win_len and field == "allow"):
            if n >= 0:
                win_len, win_dir = n, field
                win = (field == "allow")
    if win_dir is None:
        return {"allowed": True, "matched_group": best["agents"],
                "reason": "group has no matching rule"}
    return {"allowed": win, "matched_group": best["agents"],
            "reason": f"{win_dir}: matched {win_len} chars"}


def check_policy(origin: str, path: str = "/") -> dict:
    url = origin.rstrip("/") + "/robots.txt"
    r = http(url, timeout=20, ua=BROWSER_UA, retries=1)
    st = r.get("status")
    if st == 404:
        return {"ok": True, "check": "agent-policy", "robots_url": url, "status": 404,
                "verdict": "no_robots",
                "detail": "no robots.txt - everything is crawlable by default. That is "
                          "a valid posture, not a defect.",
                "findings": []}
    if st != 200:
        return {"ok": False, "check": "agent-policy", "robots_url": url, "status": st,
                "error": f"robots.txt returned HTTP {st}",
                "detail": "This is a FAILED READ, not an open policy. Google treats a "
                          "persistent 5xx on robots.txt as 'disallow everything', so an "
                          "unreadable robots.txt is the opposite of permissive."}

    body = r.text()
    ctype = (r.get("ctype") or "").lower()
    groups = parse_robots(body)
    findings = []

    if "html" in ctype or body.lstrip()[:1] == "<":
        findings.append(_finding(
            "critical", "robots_is_html",
            f"robots.txt is served as {ctype or 'HTML'} - a soft-404 page, not a rules file",
            "Serve it as text/plain. Crawlers parse this as garbage and fall back to "
            "crawling everything, or nothing."))

    rows, by_cat = [], {}
    for _key, name, cat, _dns in BOTS:
        if not cat.startswith("ai_"):
            continue
        verdict = allowed(groups, name, path)
        explicit = any(name.lower() in a or a in name.lower()
                       for g in groups for a in g["agents"] if a != "*")
        row = {"bot": name, "category": cat, "allowed": verdict["allowed"],
               "explicit_rule": explicit, "reason": verdict["reason"]}
        rows.append(row)
        by_cat.setdefault(cat, []).append(row)

    summary = {c: {"allowed": sum(1 for r_ in v if r_["allowed"]), "total": len(v)}
               for c, v in by_cat.items()}

    search_open = summary.get("ai_search", {}).get("allowed", 0)
    search_total = summary.get("ai_search", {}).get("total", 0)
    train_open = summary.get("ai_training", {}).get("allowed", 0)
    user_open = summary.get("ai_user", {}).get("allowed", 0)
    user_total = summary.get("ai_user", {}).get("total", 0)

    if search_total and search_open == 0:
        findings.append(_finding(
            "critical", "ai_search_fully_blocked",
            f"all {search_total} citing crawlers (ai_search) are disallowed"
            + (f" while {train_open} training crawlers are allowed" if train_open else ""),
            "These are the crawlers that build the index assistants CITE from. Blocking "
            "them makes the site permanently uncitable in ChatGPT Search, Perplexity, "
            "Claude and DuckAssist, and no amount of content work changes that."))
    elif search_total and search_open < search_total:
        blocked = [r_["bot"] for r_ in by_cat.get("ai_search", []) if not r_["allowed"]]
        findings.append(_finding(
            "high", "ai_search_partially_blocked",
            f"{len(blocked)} of {search_total} citing crawlers are disallowed: "
            + ", ".join(blocked),
            "Each blocked ai_search bot is one assistant that can never cite the site."))

    if search_total and search_open == 0 and train_open > 0:
        findings.append(_finding(
            "critical", "farmed_not_read",
            f"{train_open} training crawlers allowed, {search_total} citing crawlers "
            f"blocked - the worst of both",
            "The site feeds model training but can never be cited or sent traffic. If "
            "the intent was to block AI, block ai_training too; if it was to be "
            "reachable, unblock ai_search."))

    if user_total and user_open < user_total:
        blocked = [r_["bot"] for r_ in by_cat.get("ai_user", []) if not r_["allowed"]]
        findings.append(_finding(
            "high", "ai_user_blocked",
            f"live user-triggered fetchers blocked: {', '.join(blocked)}",
            "An ai_user fetch means a real person asked an assistant about you and it "
            "came to the page. Blocking it breaks the answer for someone already "
            "trying to reach you."))

    sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", body)
    if not sitemaps:
        findings.append(_finding(
            "medium", "no_sitemap_directive",
            "robots.txt declares no Sitemap:",
            "Add `Sitemap: <absolute url>`; it is the cheapest discovery hint there is."))

    if len(body.encode()) > 500 * 1024:
        findings.append(_finding(
            "high", "robots_too_large",
            f"{len(body.encode()) // 1024} KiB - Google stops parsing at 500 KiB"))

    for bad in ("noindex", "nofollow"):
        if re.search(rf"(?im)^\s*{bad}\s*:", body):
            findings.append(_finding(
                "medium", "unsupported_directive",
                f"`{bad}:` in robots.txt is ignored by Google",
                f"Use a meta robots tag or an X-Robots-Tag header for {bad}."))

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["rule"]))
    return {
        "ok": True, "check": "agent-policy", "robots_url": url, "status": 200,
        "path_tested": path,
        "verdict": ("fail" if any(f["severity"] in ("critical", "high") for f in findings)
                    else "warn" if findings else "pass"),
        "summary": summary, "category_meaning": CATEGORY_MEANING,
        "bots": rows, "sitemaps": sitemaps, "findings": findings,
        "note": "Resolved with Google's precedence rules: the most specific User-agent "
                "group wins, then the longest matching path rule, ties to Allow. "
                "Consecutive User-agent lines share one rule block.",
    }


# ----------------------------------------------------------------- llms.txt


def check_llms(origin: str) -> dict:
    out = {"ok": True, "check": "agent-llms", "origin": origin, "files": {}, "findings": []}
    for name in ("llms.txt", "llms-full.txt"):
        url = origin.rstrip("/") + "/" + name
        r = http(url, timeout=25, ua=BROWSER_UA, retries=1)
        st = r.get("status")
        rec = {"url": url, "status": st, "bytes": len(r.get("body") or b""),
               "content_type": (r.get("ctype") or "").split(";")[0] or None}
        if st == 200:
            body = r.text()
            is_html = "html" in (rec["content_type"] or "") or body.lstrip()[:1] == "<"
            rec.update({
                "html_served": is_html,
                "h1": bool(re.match(r"\s*#\s+\S", body)),
                "blockquote_summary": bool(re.search(r"(?m)^\s*>\s+\S", body)),
                "links": len(re.findall(r"\[[^\]]*\]\(([^)]+)\)", body)),
                "sections": len(re.findall(r"(?m)^##\s+\S", body)),
            })
            if is_html:
                out["findings"].append(_finding(
                    "high", "llms_txt_is_html", f"{name} is served as HTML, not markdown",
                    "It must be text/plain or text/markdown. HTML here usually means the "
                    "SPA catch-all answered and the file does not exist."))
            else:
                if not rec["h1"]:
                    out["findings"].append(_finding(
                        "medium", "llms_txt_no_h1",
                        f"{name} does not start with an H1 title (the format requires one)"))
                if name == "llms.txt" and not rec["blockquote_summary"]:
                    out["findings"].append(_finding(
                        "low", "llms_txt_no_summary",
                        "llms.txt has no `> summary` blockquote after the title"))
                if not rec["links"]:
                    out["findings"].append(_finding(
                        "medium", "llms_txt_no_links",
                        f"{name} lists no markdown links - an index with nothing in it"))
        out["files"][name] = rec

    present = [n for n, v in out["files"].items() if v.get("status") == 200]
    out["present"] = present
    if not present:
        out["findings"].append(_finding(
            "low", "no_llms_txt",
            "neither /llms.txt nor /llms-full.txt exists",
            "Optional. Ship one for optionality with AI coding agents if the site has "
            "docs; do NOT ship one expecting Google or citation benefit."))
    out["verdict"] = ("fail" if any(f["severity"] in ("critical", "high")
                                    for f in out["findings"])
                      else "warn" if out["findings"] else "pass")
    out["framing"] = (
        "Presence is reported as OPTIONALITY, never as a ranking or citation signal. "
        "Google's AI-optimization docs state Search ignores llms.txt; a 2025 server-log "
        "study measured 0.1% of AI-bot requests touching it. Its real consumer today is "
        "AI coding agents reading library documentation.")
    return out


# -------------------------------------------------------------- page reading


TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<(script|style|noscript|template)\b.*?</\1>", re.I | re.S)


def check_page(url: str) -> dict:
    r = http(url, timeout=30, ua=BROWSER_UA, retries=1)
    if r.get("status") != 200:
        return {"ok": False, "check": "agent-page", "url": url, "status": r.get("status"),
                "error": r.get("error") or f"HTTP {r.get('status')}",
                "detail": "failed read - not an agent-readiness verdict"}
    doc = r.text()
    # MARKUP = the document with comments, <script> and <style> removed. Every
    # STRUCTURAL check below must run on this, never on the raw `doc`.
    #
    # Measured on a real site 2026-08-01: a page whose only "<img>" was the literal
    # string inside an HTML comment explaining why the decorative art deliberately
    # uses NO <img> was reported as "1 of 1 <img> have no alt attribute". There was
    # no image element on the page at all.
    #
    # The false POSITIVE is the harmless half. The same bug runs the other way and
    # that is the dangerous one: `no_main_landmark` is a `re.search(r"<main\b")`, so
    # a comment or a JS template string merely MENTIONING <main> makes the check
    # pass on a page that has no <main> at all - a guard that silently stops
    # guarding. Same for <input> inside a script template (phantom unlabelled
    # fields) and <a> in a comment.
    #
    # token_budget deliberately keeps using the RAW doc: html_bytes is the transfer
    # cost an agent actually pays, comments and scripts included.
    markup = re.sub(r"<!--.*?-->", " ", doc, flags=re.S)
    markup = re.sub(r"<script\b.*?</script\s*>", " ", markup, flags=re.S | re.I)
    markup = re.sub(r"<style\b.*?</style\s*>", " ", markup, flags=re.S | re.I)
    findings = []

    text = re.sub(r"\s+", " ", TAG_RE.sub(" ", SCRIPT_RE.sub(" ", doc))).strip()
    html_bytes, text_bytes = len(doc.encode()), len(text.encode())
    ratio = (text_bytes / html_bytes) if html_bytes else 0
    # ~4 chars/token is the standard English approximation. Reported as an
    # ESTIMATE, and labelled one - no tokenizer is available here.
    est_tokens = round(len(text) / 4)

    if ratio < 0.05 and html_bytes > 20000:
        findings.append(_finding(
            "medium", "low_text_ratio",
            f"only {ratio:.1%} of {html_bytes // 1024} KiB is readable text",
            "An agent pays for the whole document and keeps a sliver. Heavy inline "
            "script/style is the usual cause."))
    if est_tokens > 30000:
        findings.append(_finding(
            "medium", "oversized_for_context",
            f"~{est_tokens:,} estimated tokens of text",
            "Long enough that an agent may truncate before reaching the answer. Split "
            "the page or front-load the conclusion."))

    # Does the content survive without JS? Agents overwhelmingly do not run it.
    body_only = re.sub(r"(?is).*?<body[^>]*>", "", doc)
    static_text = re.sub(r"\s+", " ", TAG_RE.sub(" ", SCRIPT_RE.sub(" ", body_only))).strip()
    if len(static_text) < 200 and len(doc) > 5000:
        findings.append(_finding(
            "critical", "requires_javascript",
            f"only {len(static_text)} characters of text in the raw HTML",
            "The content is assembled client-side. Most AI crawlers - including every "
            "ai_search bot - do not execute JavaScript, so they receive an empty page. "
            "Server-render or pre-render."))

    # --- agent-UX semantics, all statically decidable
    fake_buttons = re.findall(
        r"<(div|span)\b(?![^>]*\brole=)(?![^>]*\btabindex=)[^>]*\bon(?:click|mousedown)\s*=",
        markup, re.I)
    if fake_buttons:
        findings.append(_finding(
            "high", "div_onclick_without_role",
            f"{len(fake_buttons)} <div>/<span> with a click handler and no role/tabindex",
            "Use <button>/<a href>, or add role=\"button\" + tabindex=\"0\" + Enter/Space "
            "handlers. These are invisible in the accessibility tree, which is the "
            "cleanest signal an agent has."))

    anchors_nohref = re.findall(r"<a\b(?![^>]*\bhref=)[^>]*>", markup, re.I)
    if len(anchors_nohref) > 2:
        findings.append(_finding(
            "medium", "anchor_without_href",
            f"{len(anchors_nohref)} <a> elements with no href",
            "An anchor without href is not a link to any agent or crawler."))

    inputs = re.findall(r"<(input|select|textarea)\b[^>]*>", markup, re.I)
    labelled_ids = set(re.findall(r'<label\b[^>]*\bfor\s*=\s*"([^"]+)"', markup, re.I))
    unlabelled = 0
    for tag in re.findall(r"<(?:input|select|textarea)\b[^>]*>", markup, re.I):
        if re.search(r'\btype\s*=\s*"(hidden|submit|button|image)"', tag, re.I):
            continue
        has_aria = re.search(r"\baria-label(?:ledby)?\s*=", tag, re.I)
        mid = re.search(r'\bid\s*=\s*"([^"]+)"', tag, re.I)
        if not has_aria and not (mid and mid.group(1) in labelled_ids):
            unlabelled += 1
    if unlabelled:
        findings.append(_finding(
            "high", "unlabelled_input",
            f"{unlabelled} form field(s) with no <label for>, aria-label or aria-labelledby",
            "An agent reading the accessibility tree gets the field's purpose from its "
            "label. Without one the field is a void it cannot fill correctly."))

    landmarks = {t: bool(re.search(rf"<{t}\b", markup, re.I)) for t in ("main", "nav", "header", "footer")}
    if not landmarks["main"]:
        findings.append(_finding(
            "medium", "no_main_landmark",
            "no <main> element",
            "<main> is how an agent finds the primary content instead of guessing "
            "between nav, sidebar and footer."))

    imgs = re.findall(r"<img\b[^>]*>", markup, re.I)
    noalt = [t for t in imgs if not re.search(r"\balt\s*=", t, re.I)]
    if noalt:
        findings.append(_finding(
            "low", "img_without_alt",
            f"{len(noalt)} of {len(imgs)} <img> have no alt attribute",
            "Use alt=\"\" for decorative images so the omission is explicit."))

    # --- WebMCP: an opportunity, never a failure
    forms = re.findall(r"<form\b[^>]*>", doc, re.I)
    webmcp_forms = [f for f in forms if re.search(r"\btool(name|description)\s*=", f, re.I)]
    webmcp_js = bool(re.search(r"navigator\.modelContext|provideContext\s*\(", doc))

    # --- markdown availability (agents prefer a clean source)
    md_link = re.search(
        r'<link\b[^>]*rel="alternate"[^>]*type="text/(?:markdown|plain)"[^>]*>', doc, re.I)
    md_url = url.rstrip("/") + ".md"
    md = http(md_url, timeout=15, ua=BROWSER_UA)
    md_available = (md.get("status") == 200
                    and "html" not in (md.get("ctype") or "").lower())

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["rule"]))
    return {
        "ok": True, "check": "agent-page", "url": url,
        "verdict": ("fail" if any(f["severity"] in ("critical", "high") for f in findings)
                    else "warn" if findings else "pass"),
        "token_budget": {"html_bytes": html_bytes, "text_bytes": text_bytes,
                         "text_ratio": round(ratio, 4), "estimated_tokens": est_tokens,
                         "note": "tokens are a ~4-chars-per-token ESTIMATE, not a "
                                 "tokenizer count"},
        "structure": {"landmarks": landmarks, "forms": len(forms), "inputs": len(inputs),
                      "images": len(imgs), "images_without_alt": len(noalt)},
        "markdown": {"alternate_link": bool(md_link), "dot_md_url": md_url,
                     "dot_md_available": md_available},
        "webmcp": {"forms_with_tools": len(webmcp_forms), "js_api_referenced": webmcp_js,
                   "status": "proposed standard, Chrome origin trial - absence is an "
                             "opportunity, never a defect"},
        "findings": findings,
        "unmeasurable_statically": [
            {"what": "interactive target size (<24x24px)", "why": "needs layout"},
            {"what": "transparent overlays covering interactive nodes", "why": "needs layout"},
            {"what": "computed cursor:pointer on non-interactive elements", "why": "needs CSS cascade"},
            {"what": "the real accessibility tree", "why": "needs a browser"},
        ],
        "next": ("For the layout-dependent half, run Lighthouse's agentic-browsing "
                 "category: npx lighthouse@latest <url> --only-categories=agentic-browsing "
                 "(Chrome 150+; it reports a fractional pass-ratio, NOT a 0-100 score, and "
                 "the PageSpeed REST API does not return it)."),
    }


# ---------------------------------------------------------------------- main


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    po = sub.add_parser("policy", help="robots.txt resolved per AI crawler")
    po.add_argument("origin")
    po.add_argument("--path", default="/", help="path to test the rules against")

    pg = sub.add_parser("page", help="what an agent gets from one URL")
    pg.add_argument("url")

    lm = sub.add_parser("llms", help="/llms.txt and /llms-full.txt")
    lm.add_argument("origin")

    al = sub.add_parser("all", help="policy + llms + one page")
    al.add_argument("origin")
    al.add_argument("--page", help="page to sample (default: the origin itself)")

    a = p.parse_args()
    if a.cmd == "policy":
        out = check_policy(a.origin, a.path)
    elif a.cmd == "page":
        out = check_page(a.url)
    elif a.cmd == "llms":
        out = check_llms(a.origin)
    else:
        pol, llm = check_policy(a.origin), check_llms(a.origin)
        pg_ = check_page(a.page or a.origin)
        worst = [d.get("verdict") for d in (pol, llm, pg_)]
        out = {"ok": all(d.get("ok") for d in (pol, llm, pg_)),
               "check": "agent-all", "origin": a.origin,
               "verdict": ("fail" if "fail" in worst else
                           "warn" if "warn" in worst else "pass"),
               "policy": pol, "llms": llm, "page": pg_}

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
