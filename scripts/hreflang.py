#!/usr/bin/env python3
"""The hreflang mesh: does the site's own internationalisation actually hold up.

Two jobs, both keyless and both deterministic:

  audit    the MESH. Self-reference, RETURN TAGS, x-default, code validity,
           canonical alignment, protocol/host consistency, and - the one that
           catches real damage - the HTTP STATUS of every URL the site
           advertises as an alternate.
  parity   the CONTENT behind the mesh. Is the title actually localised or
           still English, is the body the expected length for that language,
           does the section structure match, is the schema localised.

Why this exists as a measurement and not a checklist: hreflang fails SILENTLY
and BIDIRECTIONALLY. A missing return tag invalidates the annotation for BOTH
pages, and nothing in Search Console has reported it since the International
Targeting report was removed in 2022. The site keeps serving 200s and the
signal is simply discarded.

TWO TRAPS, both measured on a real site while this was written:

1. `<a hreflang="es" href="...">` is a LANGUAGE SWITCHER, not an annotation.
   Only `<link rel="alternate" hreflang=...>` is the hreflang signal. On the
   page this was built against, a naive `grep hreflang` counted 43 carriers
   where the real annotation set was 23 - it would have reported every locale
   as a duplicate. This parser scopes to `<link rel~=alternate>` and reports
   the `<a>` set separately as `switcher_links`, which is information, not a
   finding.

2. An annotation outside `<head>` is not honoured. Splitting on `</head>` is
   not pedantry - a set that parses perfectly and sits in the body is a set
   Google ignores, and it looks identical in a `grep`.

CONTROL. Every "absent" verdict here is gated on a built-in fixture parse
(`_control()`): the parser is run against markup with a known-good answer
before any run reports that a page has no hreflang. If the fixture disagrees
with its expected result the run returns `control_failed` and REFUSES a
verdict, because at that point "this page has no hreflang" and "this parser is
broken" are the same output. That distinction cost a whole finding elsewhere in
this skill and is not re-learned here.

Stdlib only.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import html as htmllib
import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import http, BROWSER_UA  # noqa: E402

# --------------------------------------------------------------------- codes

# ISO 639-1. The two-letter set is the ONLY one hreflang accepts; the three
# letter ISO 639-2 forms ("eng", "deu", "fra") are the single most common
# invalid code in the wild and they look completely reasonable.
ISO639_1 = set("""
aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co
cr cs cu cv cy da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl
gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja jv ka kg
ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk
ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps
pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta
te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za
zh zu
""".split())

# ISO 15924 script subtags. Optional, and the documented mechanism for
# zh-Hans / zh-Hant. Only the ones that appear on real sites are listed; an
# unknown four-letter title-case subtag is reported as `script_unrecognised`
# (info), never as invalid - the register is long and this list is not it.
ISO15924 = {"Hans", "Hant", "Latn", "Cyrl", "Arab", "Hebr", "Grek", "Jpan",
            "Kore", "Deva", "Thai", "Beng", "Guru", "Taml", "Telu", "Knda",
            "Mlym", "Sinh", "Mymr", "Khmr", "Laoo", "Ethi", "Geor", "Armn",
            "Cans", "Tfng", "Vaii", "Adlm", "Orya", "Gujr"}

# ISO 3166-1 alpha-2. Region is optional, but when present it must be from
# THIS list. `UK` is the canonical wrong answer (the code is `GB`); `EU`,
# `UN` and `LA`-for-Latin-America are the next three.
ISO3166_1 = set("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL
BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV
CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD
GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM
IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK
LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW
MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR
PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS
ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY
UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())

# The codes people reach for that are wrong, and what they meant. Naming the
# intended value turns "invalid" into a fix.
KNOWN_BAD = {
    "en-uk": "en-GB (UK is not an ISO 3166-1 region; GB is)",
    "en-eu": "no such region - EU is not a country; pick the country, or use bare `en`",
    "es-la": "no such region - Latin America is not a country; use es-419 (UN M.49) "
             "or a specific country such as es-MX",
    "eng": "en (ISO 639-2 three-letter codes are not valid for hreflang)",
    "deu": "de", "fra": "fr", "spa": "es", "por": "pt", "zho": "zh", "jpn": "ja",
    "jp": "ja (jp is the COUNTRY code for Japan, not the language)",
    "uk-ua": "uk is Ukrainian; if you meant the United Kingdom the code is en-GB",
    "gb": "region-only is invalid - a region cannot be given without a language",
    "cn": "region-only is invalid - for Chinese use zh, zh-Hans or zh-CN",
    "gr": "el (Greek); GR is the country code for Greece",
    "dk": "da (Danish); DK is the country code for Denmark",
    "il": "he (Hebrew); IL is the country code for Israel",
}

# The nastiest class: a code that IS a valid ISO 639-1 language and is ALSO a
# different country's ISO 3166-1 code. These pass every validator - including
# this one - while silently targeting the wrong audience. Google's own
# documentation uses `be` as its worked example of the mistake. Reported as
# info with the alternative spelled out, never as invalid, because the code is
# legal and may well be intended.
CONFUSABLE = {
    "uk": ("Ukrainian", "the United Kingdom", "en-GB"),
    "be": ("Belarusian", "Belgium", "nl-BE / fr-BE"),
    "se": ("Northern Sami", "Sweden", "sv-SE"),
    "ie": ("Interlingue", "Ireland", "en-IE"),
    "ca": ("Catalan", "Canada", "en-CA / fr-CA"),
    "br": ("Breton", "Brazil", "pt-BR"),
    "sq": ("Albanian", None, None),
    "id": ("Indonesian", None, None),
}

# Rough expansion of a translation relative to English, used ONLY to flag a
# suspicious ratio for human review. These are conventional editorial
# rules-of-thumb from localisation practice, NOT measurements, and the script
# labels them that way wherever it prints one. A German page shorter than its
# English source is the signal worth having; the exact multiplier is not.
EXPANSION = {"de": (1.10, 1.50), "nl": (1.10, 1.45), "fr": (1.00, 1.40),
             "es": (1.00, 1.40), "pt": (1.00, 1.40), "it": (1.00, 1.40),
             "ru": (0.95, 1.35), "pl": (0.95, 1.35), "uk": (0.95, 1.35),
             "tr": (0.85, 1.25), "ja": (0.55, 1.00), "zh": (0.50, 0.95),
             "ko": (0.60, 1.05), "th": (0.70, 1.20), "ar": (0.80, 1.25)}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


# -------------------------------------------------------------------- parsing


LINK_RE = re.compile(r"<link\b[^>]*>", re.I)
A_HREFLANG_RE = re.compile(r"<a\b[^>]*\bhreflang\s*=\s*[\"']([^\"']+)[\"'][^>]*>", re.I)
ATTR_RE = re.compile(r"""([a-zA-Z:-]+)\s*=\s*("([^"]*)"|'([^']*)'|([^\s">]+))""")
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript|template)\b.*?</\1>", re.I | re.S)


def _attrs(tag: str) -> dict:
    out = {}
    for m in ATTR_RE.finditer(tag):
        val = m.group(3) if m.group(3) is not None else (
            m.group(4) if m.group(4) is not None else m.group(5))
        out[m.group(1).lower()] = htmllib.unescape(val or "")
    return out


def _rel_tokens(a: dict) -> set:
    return {t.lower() for t in (a.get("rel") or "").split()}


def split_head(doc: str) -> tuple[str, str]:
    """(head, rest). An hreflang link in the body is not honoured."""
    m = re.search(r"</head\s*>", doc, re.I)
    if not m:
        return doc, ""
    return doc[:m.start()], doc[m.end():]


def parse_page(doc: str, url: str) -> dict:
    """Everything hreflang and parity need, from one pass over the markup."""
    head, body = split_head(doc)

    alternates, in_body, canonical, robots = [], [], None, None
    for scope, chunk in (("head", head), ("body", body)):
        for tag in LINK_RE.findall(chunk):
            a = _attrs(tag)
            rel = _rel_tokens(a)
            if "alternate" in rel and a.get("hreflang"):
                rec = {"code": a["hreflang"].strip(), "href": a.get("href", "").strip()}
                (alternates if scope == "head" else in_body).append(rec)
            if scope == "head" and "canonical" in rel and a.get("href"):
                canonical = a["href"].strip()

    for tag in re.findall(r"<meta\b[^>]*>", head, re.I):
        a = _attrs(tag)
        if (a.get("name") or "").lower() == "robots":
            robots = (a.get("content") or "").strip()

    mh = re.search(r"<html\b([^>]*)>", doc, re.I)
    html_lang = _attrs("<html" + mh.group(1) + ">").get("lang") if mh else None

    mt = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
    title = htmllib.unescape(TAG_RE.sub("", mt.group(1))).strip() if mt else None

    desc = None
    for tag in re.findall(r"<meta\b[^>]*>", head, re.I):
        a = _attrs(tag)
        if (a.get("name") or "").lower() == "description":
            desc = (a.get("content") or "").strip()

    text_src = SCRIPT_STYLE_RE.sub(" ", body or doc)
    text = htmllib.unescape(TAG_RE.sub(" ", text_src))
    words = len([w for w in re.split(r"\s+", text) if w.strip()])

    return {
        "url": url,
        "alternates": alternates,
        "alternates_in_body": in_body,
        "switcher_links": sorted({c for c in A_HREFLANG_RE.findall(doc)}),
        "canonical": canonical,
        "html_lang": html_lang,
        "robots": robots,
        "title": title,
        "description": desc,
        "h1": [htmllib.unescape(TAG_RE.sub("", m)).strip()
               for m in re.findall(r"<h1[^>]*>(.*?)</h1>", doc, re.I | re.S)],
        "h2_count": len(re.findall(r"<h2\b", doc, re.I)),
        "h3_count": len(re.findall(r"<h3\b", doc, re.I)),
        "img_count": len(re.findall(r"<img\b", doc, re.I)),
        "schema_types": sorted(set(re.findall(r'"@type"\s*:\s*"([^"]+)"', doc))),
        "words": words,
    }


# -------------------------------------------------------------------- control


_FIXTURE = """<html lang="de"><head>
<link rel="canonical" href="https://x.test/de/p">
<link rel="alternate" hreflang="en" href="https://x.test/p">
<link rel="alternate" hreflang="de" href="https://x.test/de/p">
<link rel="alternate" hreflang="x-default" href="https://x.test/p">
<title>Titel</title><meta name="description" content="Beschreibung">
</head><body><a href="/p" hreflang="en">EN</a>
<link rel="alternate" hreflang="fr" href="https://x.test/fr/p">
<h1>Kopf</h1><h2>A</h2><h2>B</h2><p>ein zwei drei</p></body></html>"""


def _control() -> dict:
    """Prove the parser discriminates BEFORE any absence is reported.

    Checks four things the fixture is built to distinguish, each of which has
    silently broken a real parser: head vs body scoping, `<link>` vs `<a>`,
    canonical extraction, and x-default recognition.
    """
    p = parse_page(_FIXTURE, "https://x.test/de/p")
    checks = {
        "head_alternates_are_3": len(p["alternates"]) == 3,
        "body_alternate_excluded": [r["code"] for r in p["alternates_in_body"]] == ["fr"],
        "a_tag_not_counted_as_annotation":
            "en" in p["switcher_links"] and
            all(r["code"] != "fr" for r in p["alternates"]),
        "canonical_read": p["canonical"] == "https://x.test/de/p",
        "x_default_seen": any(r["code"] == "x-default" for r in p["alternates"]),
        "html_lang_read": p["html_lang"] == "de",
    }
    return {"ok": all(checks.values()), "checks": checks}


# ------------------------------------------------------------------ code math


def validate_code(code: str) -> dict:
    """Is this a legal hreflang value, and if not, what was meant."""
    raw = (code or "").strip()
    low = raw.lower()
    out = {"code": raw, "valid": False, "severity": "high", "note": None}

    if not raw:
        out["note"] = "empty hreflang attribute"
        return out
    if low == "x-default":
        return {"code": raw, "valid": True, "severity": "info", "note": "fallback marker"}
    if low in KNOWN_BAD:
        out["note"] = f"invalid - did you mean {KNOWN_BAD[low]}"
        return out

    parts = raw.split("-")
    lang = parts[0].lower()

    if lang not in ISO639_1:
        if len(lang) == 2 and lang.upper() in ISO3166_1:
            out["note"] = (f"'{lang}' is a COUNTRY code, not a language - a region "
                           f"cannot be given without a language prefix")
        elif len(lang) == 3:
            out["note"] = f"'{lang}' looks like ISO 639-2; hreflang requires the two-letter ISO 639-1 code"
        else:
            out["note"] = f"'{lang}' is not an ISO 639-1 language code"
        return out

    script = region = None
    rest = parts[1:]
    if rest and len(rest[0]) == 4 and rest[0][:1].isalpha():
        script = rest[0][:1].upper() + rest[0][1:].lower()
        rest = rest[1:]
    if rest:
        cand = rest[0]
        # UN M.49 numeric regions are legal and es-419 is the correct way to
        # say "Latin America" - the thing es-LA gets wrong.
        if cand.isdigit():
            region = cand
        else:
            region = cand.upper()
            if region not in ISO3166_1:
                out["note"] = f"'{cand}' is not an ISO 3166-1 alpha-2 region code"
                return out
        rest = rest[1:]
    if rest:
        out["severity"] = "medium"
        out["note"] = f"unexpected trailing subtag(s): {'-'.join(rest)}"
        return out

    note = None
    sev = "info"
    if lang in CONFUSABLE and not region:
        language, country, instead = CONFUSABLE[lang]
        if country:
            note = (f"'{lang}' is the language {language}. It is ALSO the country code "
                    f"for {country} - if that is what you meant, the code is {instead}")
            sev = "info"
    if script and script not in ISO15924:
        note, sev = f"script subtag '{script}' not in this tool's list - verify against ISO 15924", "info"
    canonical_form = lang + (f"-{script}" if script else "") + (f"-{region}" if region else "")
    if region and raw != canonical_form:
        note = f"case convention is language-SCRIPT-REGION, i.e. {canonical_form}"
        sev = "low"
    return {"code": raw, "valid": True, "severity": sev, "note": note,
            "lang": lang, "script": script, "region": region}


def _norm(u: str) -> str:
    """Compare URLs the way a search engine does, without being clever."""
    if not u:
        return ""
    s = urllib.parse.urlsplit(u.strip())
    host = (s.hostname or "").lower()
    if s.port and not ((s.scheme == "https" and s.port == 443) or
                       (s.scheme == "http" and s.port == 80)):
        host = f"{host}:{s.port}"
    path = s.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urllib.parse.urlunsplit((s.scheme.lower(), host, path, s.query, ""))


# --------------------------------------------------------------------- fetch


def fetch(url: str, timeout: int = 25) -> dict:
    r = http(url, timeout=timeout, ua=BROWSER_UA, retries=1, follow=False)
    st = r.get("status")
    out = {"url": url, "status": st, "location": r.get("location"),
           "error": r.get("error"), "ms": r.get("ms")}
    if st == 200:
        out["doc"] = r.text()
    return out


def fetch_many(urls: list[str], workers: int = 8, timeout: int = 25) -> dict:
    got = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch, u, timeout): u for u in urls}
        for f in concurrent.futures.as_completed(futs):
            r = f.result()
            got[r["url"]] = r
    return got


def urls_from_sitemap(url: str, limit: int = 200, locale_only: bool = False) -> list[str]:
    """Flat or index sitemap -> a URL list. Index files are followed one level."""
    r = http(url, timeout=45, retries=1)
    if r.get("status") != 200:
        return []
    doc = r.text()
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", doc, re.I)
    if re.search(r"<sitemapindex", doc, re.I):
        out = []
        for child in locs[:20]:
            out.extend(urls_from_sitemap(child, limit=limit))
            if len(out) >= limit:
                break
        return out[:limit]
    if locale_only:
        locs = [u for u in locs if re.match(r"^https?://[^/]+/[a-z]{2}(/|$)", u)]
    return locs[:limit]


# --------------------------------------------------------------------- audit


def _finding(sev, rule, url, detail, fix=None, **extra):
    f = {"severity": sev, "rule": rule, "url": url, "detail": detail}
    if fix:
        f["fix"] = fix
    f.update(extra)
    return f


def audit(seeds: list[str], *, expand: bool = True, check_status: bool = True,
          max_urls: int = 120, workers: int = 8) -> dict:
    """Fetch the seed pages, expand to everything they declare, check the mesh."""
    ctl = _control()
    if not ctl["ok"]:
        return {"ok": False, "check": "hreflang-audit", "control_ok": False,
                "control": ctl,
                "error": "parser control failed - refusing a verdict. An 'absent' "
                         "result and a broken parser are the same output, so no "
                         "absence is reported from a run whose control did not pass."}

    pages: dict[str, dict] = {}
    fetched = fetch_many(seeds[:max_urls], workers=workers)
    frontier = []
    for u, r in fetched.items():
        if r.get("doc"):
            pages[_norm(u)] = {**parse_page(r["doc"], u), "http": r}
            frontier += [a["href"] for a in pages[_norm(u)]["alternates"] if a["href"]]

    if expand:
        todo = [u for u in dict.fromkeys(frontier)
                if _norm(u) not in pages][:max(0, max_urls - len(pages))]
        if todo:
            for u, r in fetch_many(todo, workers=workers).items():
                if r.get("doc"):
                    pages[_norm(u)] = {**parse_page(r["doc"], u), "http": r}
                else:
                    pages[_norm(u)] = {"url": u, "http": r, "unread": True}

    findings: list[dict] = []
    declared: dict[str, set] = {}     # normalised page -> normalised alternates
    with_tags = 0

    for key, p in pages.items():
        if p.get("unread"):
            continue
        alts = p.get("alternates") or []
        if alts:
            with_tags += 1
        declared[key] = {_norm(a["href"]) for a in alts if a["href"]}

        if p.get("alternates_in_body"):
            findings.append(_finding(
                "high", "hreflang_outside_head", p["url"],
                f"{len(p['alternates_in_body'])} alternate link(s) sit after </head>: "
                + ", ".join(a["code"] for a in p["alternates_in_body"][:5]),
                "Move them into <head>. Google does not honour hreflang in the body, "
                "and the markup looks correct in a grep either way."))

        if not alts:
            continue

        # --- codes
        seen_codes: dict[str, list] = {}
        for a in alts:
            v = validate_code(a["code"])
            seen_codes.setdefault(a["code"].lower(), []).append(a["href"])
            if not v["valid"]:
                findings.append(_finding(
                    v["severity"], "invalid_code", p["url"],
                    f"hreflang=\"{a['code']}\" -> {v['note']}",
                    "Correct the code. An invalid value invalidates that annotation; "
                    "Search Console has not reported this since the International "
                    "Targeting report was removed in 2022."))
            elif v.get("note") and v["severity"] != "info":
                findings.append(_finding(
                    v["severity"], "code_convention", p["url"],
                    f"hreflang=\"{a['code']}\" -> {v['note']}"))
        for code, hrefs in seen_codes.items():
            if len({_norm(h) for h in hrefs}) > 1:
                findings.append(_finding(
                    "high", "duplicate_code_conflict", p["url"],
                    f"hreflang=\"{code}\" is declared {len(hrefs)} times pointing at "
                    f"different URLs: {', '.join(sorted({_norm(h) for h in hrefs}))}",
                    "One code, one URL. Conflicting duplicates make the whole set "
                    "unreliable to the parser."))

        # --- self reference
        if key not in declared[key]:
            findings.append(_finding(
                "critical", "missing_self_reference", p["url"],
                "the page does not list itself among its own alternates",
                "Add a self-referencing alternate. Google discards the ENTIRE set "
                "when the self-reference is missing - this is not a per-tag defect."))

        # --- x-default
        xd = [a for a in alts if a["code"].strip().lower() == "x-default"]
        if len(xd) > 1:
            findings.append(_finding(
                "medium", "multiple_x_default", p["url"],
                f"{len(xd)} x-default entries: "
                + ", ".join(sorted({_norm(a['href']) for a in xd})),
                "Exactly one x-default per set."))

        # --- canonical alignment
        can = p.get("canonical")
        if can and _norm(can) != key:
            findings.append(_finding(
                "high", "hreflang_on_non_canonical", p["url"],
                f"canonical points elsewhere ({_norm(can)}) while this page carries "
                f"{len(alts)} hreflang annotations",
                "hreflang is only honoured on canonical URLs. Either make this page "
                "self-canonical or move the annotations to the canonical URL."))

        # --- html lang vs claimed code
        selfcodes = [a["code"] for a in alts if _norm(a["href"]) == key
                     and a["code"].lower() != "x-default"]
        hl = (p.get("html_lang") or "").strip().lower()
        if hl and selfcodes:
            claimed = selfcodes[0].lower()
            if claimed.split("-")[0] != hl.split("-")[0]:
                findings.append(_finding(
                    "medium", "html_lang_mismatch", p["url"],
                    f'<html lang="{p["html_lang"]}"> but this page is declared as '
                    f'hreflang="{selfcodes[0]}"',
                    "They should agree on the language. A mismatch is a reliable "
                    "sign the template renders one locale while claiming another."))

        # --- protocol / host consistency
        schemes = {urllib.parse.urlsplit(a["href"]).scheme for a in alts if a["href"]}
        if len(schemes - {""}) > 1:
            findings.append(_finding(
                "medium", "mixed_protocol", p["url"],
                f"alternate set mixes protocols: {sorted(schemes - {''})}",
                "Use one scheme, https, throughout the set."))
        rels = [a["href"] for a in alts if a["href"] and not urllib.parse.urlsplit(a["href"]).netloc]
        if rels:
            findings.append(_finding(
                "high", "relative_alternate_href", p["url"],
                f"{len(rels)} alternate href(s) are relative: {', '.join(rels[:4])}",
                "hreflang hrefs must be absolute, fully-qualified URLs."))

    # --- return tags (the bidirectional one)
    for key, alts in declared.items():
        if not alts:
            continue
        src = pages[key]
        for target in sorted(alts):
            if target == key:
                continue
            tp = pages.get(target)
            if tp is None or tp.get("unread"):
                continue
            back = declared.get(target, set())
            if key not in back:
                findings.append(_finding(
                    "critical", "missing_return_tag", src["url"],
                    f"declares {target} as an alternate, but {target} does not declare "
                    f"it back",
                    "hreflang must be reciprocal. A missing return tag invalidates the "
                    "relationship for BOTH pages, not just the one that is missing it.",
                    target=target))

    # --- do the advertised URLs actually exist
    status_map = {}
    if check_status:
        targets = sorted({t for alts in declared.values() for t in alts})
        unknown = [t for t in targets if t not in pages or pages[t].get("unread")]
        known = {t: pages[t]["http"] for t in targets
                 if t in pages and not pages[t].get("unread")}
        probed = fetch_many(unknown, workers=workers) if unknown else {}
        for t in targets:
            r = known.get(t) or probed.get(t) or {}
            status_map[t] = {"status": r.get("status"), "location": r.get("location"),
                             "error": r.get("error")}

        by_target: dict[str, set] = {}
        for key, alts in declared.items():
            for t in alts:
                by_target.setdefault(t, set()).add(pages[key]["url"])
        for t, st in status_map.items():
            code = st.get("status")
            srcs = sorted(by_target.get(t, []))
            if code == 200:
                continue
            if code is None:
                findings.append(_finding(
                    "medium", "alternate_unreachable", t,
                    f"could not be fetched ({st.get('error')}) - advertised by "
                    f"{len(srcs)} page(s)",
                    "Unreachable is not the same as missing. Re-run before acting; "
                    "if it persists it is a real defect.", advertised_by=srcs[:5]))
            elif 300 <= code < 400:
                findings.append(_finding(
                    "high", "alternate_redirects", t,
                    f"HTTP {code} -> {st.get('location')} - advertised by {len(srcs)} page(s)",
                    "Point the annotation at the final URL. An hreflang target that "
                    "redirects wastes the annotation.", advertised_by=srcs[:5]))
            else:
                findings.append(_finding(
                    "critical", "alternate_dead", t,
                    f"HTTP {code} - advertised as an alternate by {len(srcs)} page(s)",
                    "Remove the annotation or publish the page. Advertising a dead URL "
                    "as a language alternate is the failure mode that survives every "
                    "on-page check, because the page carrying the tag is a healthy 200.",
                    advertised_by=srcs[:5]))

    read = [p for p in pages.values() if not p.get("unread")]
    unread = [p["http"] for p in pages.values() if p.get("unread")]
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["rule"], f["url"]))
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1

    verdict = ("pass" if not findings else
               "fail" if any(f["severity"] in ("critical", "high") for f in findings)
               else "warn")

    return {
        "ok": True, "check": "hreflang-audit", "control_ok": True,
        "verdict": verdict,
        "pages_read": len(read), "pages_unread": len(unread),
        "pages_with_hreflang": with_tags,
        "pages_without_hreflang": len(read) - with_tags,
        "locales": sorted({a["code"] for p in read for a in p.get("alternates", [])}),
        "findings": findings, "counts": counts,
        "unread": unread[:20],
        "note": (
            "pages_without_hreflang counts pages that were READ and carry none. "
            "Unread pages are listed separately and are never counted as absent. "
            "`switcher_links` (<a hreflang>) are NOT annotations and are excluded."),
    }


# -------------------------------------------------------------------- parity


SYSTEMATIC_SHARE = 0.6


def _collapse_systematic(findings: list[dict], population: int) -> tuple[list[dict], list[dict]]:
    """One fact about the template, not N defects across N locales.

    Measured on a real 22-locale site: every single locale tripped
    `length_ratio_outlier` at ~0.42x, because the ENGLISH page carries sections
    the translations do not. Reported per-locale that is 21 low-severity rows
    that bury the one finding that mattered (a single locale whose h1 was still
    English). Reported once it is a sentence: "the reference page is ~2.3x every
    translation - sections are missing from the template, not from a locale".

    A rule tripping on most of the population is evidence about the SOURCE; a
    rule tripping on one or two is evidence about those locales. Collapsing at
    60% keeps both readable, and the per-locale detail is preserved inside the
    collapsed row rather than discarded.
    """
    if population < 4:
        return findings, []
    by_rule: dict[str, list[dict]] = {}
    for f in findings:
        by_rule.setdefault(f["rule"], []).append(f)

    kept, systematic = [], []
    for rule, group in by_rule.items():
        share = len(group) / population
        if len(group) >= 4 and share >= SYSTEMATIC_SHARE:
            systematic.append({
                "rule": rule,
                "severity": group[0]["severity"],
                "affected": len(group),
                "population": population,
                "share": round(share, 2),
                "detail": (f"{len(group)} of {population} locales trip `{rule}`. At this "
                           f"share it is a property of the SOURCE page or the template, "
                           f"not a per-locale defect - fix it once, upstream."),
                "examples": [{"url": f["url"], "detail": f["detail"]} for f in group[:3]],
                "urls": [f["url"] for f in group],
            })
        else:
            kept.extend(group)
    systematic.sort(key=lambda s: (SEVERITY_ORDER.get(s["severity"], 9), -s["affected"]))
    return kept, systematic


def parity(seed: str, *, reference: str | None = None, max_urls: int = 60,
           workers: int = 8) -> dict:
    """Is the content behind the mesh actually localised, or English in a /xx/ path."""
    ctl = _control()
    if not ctl["ok"]:
        return {"ok": False, "check": "hreflang-parity", "control_ok": False,
                "control": ctl, "error": "parser control failed - refusing a verdict"}

    first = fetch(seed)
    if not first.get("doc"):
        return {"ok": False, "check": "hreflang-parity", "url": seed,
                "status": first.get("status"), "error": first.get("error"),
                "detail": "seed page could not be read - this is a failed read, "
                          "not a parity result"}
    seedp = parse_page(first["doc"], seed)
    alts = [a for a in seedp["alternates"] if a["href"]
            and a["code"].strip().lower() != "x-default"]
    if not alts:
        return {"ok": True, "check": "hreflang-parity", "url": seed,
                "control_ok": True, "verdict": "n/a",
                "detail": "the seed page declares no hreflang alternates, so there is "
                          "no declared locale set to compare against"}

    docs = fetch_many([a["href"] for a in alts][:max_urls], workers=workers)
    by_url = {_norm(a["href"]): a["code"] for a in alts}

    rows, findings = [], []
    ref_code = (reference or "en").lower()
    ref_row = None

    for u, r in docs.items():
        code = by_url.get(_norm(u), "?")
        if not r.get("doc"):
            rows.append({"code": code, "url": u, "read": False,
                         "status": r.get("status"), "error": r.get("error")})
            continue
        p = parse_page(r["doc"], u)
        row = {"code": code, "url": u, "read": True, "words": p["words"],
               "title": p["title"], "description": p["description"],
               "h1": (p["h1"] or [None])[0], "h2_count": p["h2_count"],
               "h3_count": p["h3_count"], "img_count": p["img_count"],
               "schema_types": p["schema_types"], "html_lang": p["html_lang"]}
        rows.append(row)
        if code.lower().split("-")[0] == ref_code:
            ref_row = row

    if ref_row is None:
        ref_row = next((r for r in rows if r.get("read")), None)
    if ref_row is None:
        return {"ok": False, "check": "hreflang-parity", "url": seed,
                "error": "no locale version could be read"}

    for row in rows:
        if not row.get("read"):
            findings.append(_finding(
                "medium", "locale_unread", row["url"],
                f"{row['code']}: HTTP {row.get('status')} {row.get('error') or ''}".strip(),
                "Unread is not un-localised. Re-run before drawing a conclusion."))
            continue
        if row is ref_row:
            continue
        lang = row["code"].lower().split("-")[0]

        if row["title"] and ref_row["title"] and row["title"] == ref_row["title"]:
            findings.append(_finding(
                "high", "title_not_localised", row["url"],
                f"{row['code']} title is byte-identical to {ref_row['code']}: "
                f"{row['title'][:80]!r}",
                "Translate the title. An untranslated title under an hreflang claim is "
                "a near-duplicate of the reference page on a different URL - worse than "
                "not shipping the locale."))
        if (row["description"] and ref_row["description"]
                and row["description"] == ref_row["description"]):
            findings.append(_finding(
                "medium", "description_not_localised", row["url"],
                f"{row['code']} meta description is byte-identical to {ref_row['code']}"))
        if row["h1"] and ref_row["h1"] and row["h1"] == ref_row["h1"]:
            findings.append(_finding(
                "medium", "h1_not_localised", row["url"],
                f"{row['code']} h1 is byte-identical to {ref_row['code']}: {row['h1'][:80]!r}"))

        for field, sev in (("h2_count", "low"), ("h3_count", "low"), ("img_count", "low")):
            a, b = row[field], ref_row[field]
            if b and abs(a - b) > max(1, round(b * 0.34)):
                findings.append(_finding(
                    sev, "structure_drift", row["url"],
                    f"{row['code']} has {a} {field.replace('_count','')} vs "
                    f"{b} on {ref_row['code']}",
                    "A structural gap this size usually means a section was dropped in "
                    "translation rather than adapted."))

        if set(row["schema_types"]) != set(ref_row["schema_types"]):
            missing = sorted(set(ref_row["schema_types"]) - set(row["schema_types"]))
            if missing:
                findings.append(_finding(
                    "medium", "schema_missing_on_locale", row["url"],
                    f"{row['code']} is missing structured data present on "
                    f"{ref_row['code']}: {', '.join(missing)}"))

        if ref_row["words"] >= 120 and row["words"]:
            ratio = row["words"] / ref_row["words"]
            lo, hi = EXPANSION.get(lang, (0.55, 1.55))
            if not (lo <= ratio <= hi):
                findings.append(_finding(
                    "low", "length_ratio_outlier", row["url"],
                    f"{row['code']} is {ratio:.2f}x the length of {ref_row['code']} "
                    f"({row['words']} vs {ref_row['words']} words); the editorial "
                    f"rule-of-thumb band for {lang} is {lo:.2f}-{hi:.2f}x",
                    "Check for dropped or padded sections. The band is a localisation "
                    "convention, NOT a measurement - treat it as a prompt to look, "
                    "never as a defect on its own.", ratio=round(ratio, 2)))

        hl = (row["html_lang"] or "").lower().split("-")[0]
        if hl and hl != lang:
            findings.append(_finding(
                "medium", "html_lang_mismatch", row["url"],
                f'declared hreflang="{row["code"]}" but <html lang="{row["html_lang"]}">'))

    comparable = [r for r in rows if r.get("read") and r is not ref_row]
    findings, systematic = _collapse_systematic(findings, len(comparable))

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["rule"], f["url"]))
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return {
        "ok": True, "check": "hreflang-parity", "control_ok": True,
        "systematic": systematic,
        "seed": seed, "reference": ref_row["code"],
        "locales_declared": len(alts), "locales_read": sum(1 for r in rows if r.get("read")),
        "verdict": ("pass" if not findings else
                    "fail" if any(f["severity"] in ("critical", "high") for f in findings)
                    else "warn"),
        "rows": rows, "findings": findings, "counts": counts,
        "note": "Byte-identical title/h1 against the reference locale is the load-bearing "
                "check here; the length band is advisory and labelled as such.",
    }


# ---------------------------------------------------------------------- main


def _seed_urls(a) -> list[str]:
    urls = list(a.url or [])
    if a.sitemap:
        urls += urls_from_sitemap(a.sitemap, limit=a.max_urls, locale_only=a.locale_only)
    if a.urls_file:
        raw = (sys.stdin.read() if a.urls_file == "-"
               else Path(a.urls_file).read_text(encoding="utf-8"))
        urls += [x.strip() for x in raw.splitlines() if x.strip() and not x.startswith("#")]
    return list(dict.fromkeys(urls))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    au = sub.add_parser("audit", help="the hreflang mesh")
    au.add_argument("--url", action="append", help="seed URL (repeatable)")
    au.add_argument("--sitemap", help="sitemap or sitemap-index URL to seed from")
    au.add_argument("--urls-file", help="file of URLs, one per line ('-' for stdin)")
    au.add_argument("--locale-only", action="store_true",
                    help="when seeding from a sitemap, keep only /xx/ paths")
    au.add_argument("--no-expand", action="store_true",
                    help="do not fetch the alternates the seeds declare (skips return-tag checks)")
    au.add_argument("--no-status", action="store_true",
                    help="skip probing whether advertised alternates exist")
    au.add_argument("--max-urls", type=int, default=120)
    au.add_argument("--workers", type=int, default=8)

    pa = sub.add_parser("parity", help="is the content behind the mesh localised")
    pa.add_argument("url", help="one page; its declared alternates are the comparison set")
    pa.add_argument("--reference", default="en", help="reference locale (default en)")
    pa.add_argument("--max-urls", type=int, default=60)
    pa.add_argument("--workers", type=int, default=8)

    sub.add_parser("control", help="prove the parser discriminates (run this first if unsure)")

    co = sub.add_parser("codes", help="validate hreflang codes with no network at all")
    co.add_argument("code", nargs="+")

    a = p.parse_args()

    if a.cmd == "control":
        out = {"ok": _control()["ok"], "check": "hreflang-control", **_control()}
    elif a.cmd == "codes":
        out = {"ok": True, "check": "hreflang-codes",
               "results": [validate_code(c) for c in a.code]}
    elif a.cmd == "parity":
        out = parity(a.url, reference=a.reference, max_urls=a.max_urls, workers=a.workers)
    else:
        seeds = _seed_urls(a)
        if not seeds:
            out = {"ok": False, "check": "hreflang-audit",
                   "error": "no seed URLs - pass --url, --sitemap or --urls-file"}
        else:
            out = audit(seeds, expand=not a.no_expand, check_status=not a.no_status,
                        max_urls=a.max_urls, workers=a.workers)

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
