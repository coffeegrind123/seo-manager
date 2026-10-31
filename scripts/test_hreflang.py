#!/usr/bin/env python3
"""Regression tests for the hreflang mesh audit - the CONTROL for a clean pass.

The live site this was built against passes the audit outright: 22 locales, a
complete return-tag mesh, every advertised alternate a 200. That is a good
result and a WORTHLESS test. A checker that returns "pass" unconditionally
produces exactly the same output, and there is no way to tell the two apart
from the run itself.

So every rule the audit can emit is fired here against synthetic markup, with
no network at all. If a rule stops firing, this fails - and a real "pass"
becomes trustworthy again, because the instrument has been shown to
discriminate on the same code path.

Run: python3 test_hreflang.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hreflang  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name} {detail}")


def page(alts, *, canonical=None, lang="en", body_alts=(), title="T", words=40):
    """Minimal document carrying exactly the annotations a case needs."""
    head = [f'<link rel="canonical" href="{canonical}">'] if canonical else []
    head += [f'<link rel="alternate" hreflang="{c}" href="{h}">' for c, h in alts]
    body = [f'<link rel="alternate" hreflang="{c}" href="{h}">' for c, h in body_alts]
    return (f'<html lang="{lang}"><head><title>{title}</title>'
            + "".join(head) + "</head><body>" + "".join(body)
            + "<h1>H</h1>" + ("word " * words) + "</body></html>")


def run(pages: dict, *, statuses: dict | None = None, **kw) -> dict:
    """Drive audit() with a fake network. `pages` maps url -> html or None."""
    statuses = statuses or {}

    def fake_fetch(url, timeout=25):
        st = statuses.get(url, 200 if pages.get(url) else 404)
        out = {"url": url, "status": st, "location": statuses.get(url + "#loc"),
               "error": None, "ms": 1}
        if st == 200 and pages.get(url):
            out["doc"] = pages[url]
        return out

    def fake_fetch_many(urls, workers=8, timeout=25):
        return {u: fake_fetch(u) for u in urls}

    real_f, real_fm = hreflang.fetch, hreflang.fetch_many
    hreflang.fetch, hreflang.fetch_many = fake_fetch, fake_fetch_many
    try:
        return hreflang.audit(list(kw.pop("seeds", pages.keys())), **kw)
    finally:
        hreflang.fetch, hreflang.fetch_many = real_f, real_fm


def rules(res) -> set:
    return {f["rule"] for f in res.get("findings", [])}


# ---------------------------------------------------------------------------

print("parser control")
c = hreflang._control()
check("control passes on the built-in fixture", c["ok"], str(c))

print("\nthe happy path must be CLEAN (or every test below is meaningless)")
EN, DE = "https://x.test/p", "https://x.test/de/p"
good = run({
    EN: page([("en", EN), ("de", DE), ("x-default", EN)], canonical=EN, lang="en"),
    DE: page([("en", EN), ("de", DE), ("x-default", EN)], canonical=DE, lang="de"),
})
check("a correct mesh yields no findings", good["verdict"] == "pass" and not good["findings"],
      str(rules(good)))
check("both pages counted as carrying hreflang", good["pages_with_hreflang"] == 2)

print("\nevery rule must actually fire")

r = run({
    EN: page([("de", DE)], canonical=EN),           # no self-reference
    DE: page([("en", EN), ("de", DE)], canonical=DE),
})
check("missing_self_reference fires", "missing_self_reference" in rules(r), str(rules(r)))

r = run({
    EN: page([("en", EN), ("de", DE)], canonical=EN),
    DE: page([("de", DE)], canonical=DE),           # never points back at EN
})
check("missing_return_tag fires", "missing_return_tag" in rules(r), str(rules(r)))

r = run({EN: page([("en", EN), ("de", DE)], canonical=EN)},
        statuses={DE: 404}, seeds=[EN])
check("alternate_dead fires on a 404 alternate", "alternate_dead" in rules(r), str(rules(r)))

r = run({EN: page([("en", EN), ("de", DE)], canonical=EN)},
        statuses={DE: 301, DE + "#loc": "https://x.test/de/p/"}, seeds=[EN])
check("alternate_redirects fires on a 301 alternate", "alternate_redirects" in rules(r), str(rules(r)))

r = run({EN: page([("en", EN), ("eng", DE)], canonical=EN)}, seeds=[EN], check_status=False)
check("invalid_code fires on ISO 639-2", "invalid_code" in rules(r), str(rules(r)))

r = run({EN: page([("en", EN), ("en-uk", DE)], canonical=EN)}, seeds=[EN], check_status=False)
check("invalid_code fires on en-uk", "invalid_code" in rules(r), str(rules(r)))

r = run({EN: page([("en", EN)], canonical="https://x.test/other")}, seeds=[EN],
        check_status=False)
check("hreflang_on_non_canonical fires", "hreflang_on_non_canonical" in rules(r), str(rules(r)))

# Google, managing-multi-regional-sites (read 2026-10-25): for same-language
# duplicates "pick a preferred version and use the rel="canonical" element
# and hreflang tags". An /at/ page canonical to the /de/ page in its own
# cluster is that recommendation, not a high-severity defect.
AT, DEP = "https://x.test/at/p", "https://x.test/de/p"
cluster = [("de-AT", AT), ("de-DE", DEP)]
r = run({AT: page(cluster, canonical=DEP, lang="de"), DEP: page(cluster, canonical=DEP, lang="de")},
        seeds=[AT], check_status=False)
sev = {f["rule"]: f["severity"] for f in r.get("findings", []) if f["url"] == AT}
check("same-language canonical inside the cluster is info, not high",
      sev.get("hreflang_same_language_canonical") == "info" and "hreflang_on_non_canonical" not in sev, sev)
FR = "https://x.test/fr/p"
r = run({FR: page([("fr", FR), ("de-DE", DEP)], canonical=DEP, lang="fr"),
         DEP: page([("fr", FR), ("de-DE", DEP)], canonical=DEP, lang="de")}, seeds=[FR], check_status=False)
check("CONTROL: a cross-language canonical is still high",
      "hreflang_on_non_canonical" in {f["rule"] for f in r["findings"] if f["url"] == FR}, rules(r))

r = run({EN: page([("en", EN)], canonical=EN, body_alts=[("de", DE)])}, seeds=[EN],
        check_status=False)
check("hreflang_outside_head fires", "hreflang_outside_head" in rules(r), str(rules(r)))

r = run({EN: page([("en", EN), ("de", DE), ("de", "https://x.test/de2/p")], canonical=EN)},
        seeds=[EN], check_status=False)
check("duplicate_code_conflict fires", "duplicate_code_conflict" in rules(r), str(rules(r)))

r = run({EN: page([("en", EN), ("x-default", EN), ("x-default", DE)], canonical=EN)},
        seeds=[EN], check_status=False)
check("multiple_x_default fires", "multiple_x_default" in rules(r), str(rules(r)))

r = run({EN: page([("en", EN), ("de", "/de/p")], canonical=EN)}, seeds=[EN],
        check_status=False)
check("relative_alternate_href fires", "relative_alternate_href" in rules(r), str(rules(r)))

r = run({EN: page([("en", EN), ("de", "http://x.test/de/p")], canonical=EN)},
        seeds=[EN], check_status=False)
check("mixed_protocol fires", "mixed_protocol" in rules(r), str(rules(r)))

r = run({EN: page([("en", EN)], canonical=EN, lang="fr")}, seeds=[EN], check_status=False)
check("html_lang_mismatch fires", "html_lang_mismatch" in rules(r), str(rules(r)))

print("\nabsence must never be manufactured")

r = run({EN: None}, statuses={EN: 500}, seeds=[EN])
check("an unreadable seed is not counted as 'no hreflang'",
      r["pages_without_hreflang"] == 0, str(r))

r = run({EN: page([], canonical=EN)}, seeds=[EN])
check("a READ page with no annotations is counted as without",
      r["pages_with_hreflang"] == 0 and r["pages_without_hreflang"] == 1, str(r))

saved = hreflang._FIXTURE
try:
    hreflang._FIXTURE = "<html><head></head><body></body></html>"   # break the control
    r = hreflang.audit([EN])
    check("a failed parser control REFUSES a verdict",
          r.get("ok") is False and r.get("control_ok") is False, str(r)[:160])
finally:
    hreflang._FIXTURE = saved

print("\nthe <a hreflang> trap (measured on a real site: 21 switcher links vs 23 annotations)")
doc = page([("en", EN), ("de", DE)], canonical=EN) + '<a href="/fr/p" hreflang="fr">FR</a>'
p = hreflang.parse_page(doc, EN)
check("<a hreflang> excluded from annotations",
      [a["code"] for a in p["alternates"]] == ["en", "de"], str(p["alternates"]))
check("<a hreflang> still reported as switcher_links", p["switcher_links"] == ["fr"],
      str(p["switcher_links"]))

print("\nsystematic collapsing (21/21 locales tripping one rule is ONE fact, not 21)")
many = [{"severity": "low", "rule": "length_ratio_outlier", "url": f"u{i}", "detail": "d"}
        for i in range(21)]
one = [{"severity": "medium", "rule": "h1_not_localised", "url": "u_id", "detail": "d"}]
kept, syst = hreflang._collapse_systematic(many + one, 21)
check("the 21-locale rule is collapsed", len(syst) == 1 and syst[0]["affected"] == 21, str(syst))
check("the single-locale finding survives collapsing",
      [f["rule"] for f in kept] == ["h1_not_localised"], str(kept))
check("collapsed row keeps the per-locale urls", len(syst[0]["urls"]) == 21)

kept, syst = hreflang._collapse_systematic(
    [{"severity": "low", "rule": "r", "url": f"u{i}", "detail": "d"} for i in range(2)], 21)
check("a 2-of-21 rule is NOT collapsed", not syst and len(kept) == 2, str(syst))

kept, syst = hreflang._collapse_systematic(
    [{"severity": "low", "rule": "r", "url": "u", "detail": "d"}], 2)
check("a tiny population never collapses", not syst and len(kept) == 1)

print("\ncode validation")
check("bare region-only code rejected", not hreflang.validate_code("gb")["valid"])
check("es-419 (UN M.49) accepted", hreflang.validate_code("es-419")["valid"])
check("zh-Hans accepted", hreflang.validate_code("zh-Hans")["valid"])
check("zh-Hans-US accepted (language+script+region)",
      hreflang.validate_code("zh-Hans-US")["valid"])
check("x-default accepted", hreflang.validate_code("x-default")["valid"])
check("'be' is VALID (Belarusian) but carries the Belgium note",
      hreflang.validate_code("be")["valid"] and "Belgium" in (hreflang.validate_code("be")["note"] or ""))
check("'en' carries no confusable note", not hreflang.validate_code("en").get("note"))

# Automatic language redirects (Google multi-regional page, read 2026-10-25:
# avoid automatically redirecting; Googlebot sends no Accept-Language and
# crawls mostly from the US).
C = hreflang.classify_redirect_probe
ok200 = {"status": 200, "location": None}
to_en = {"status": 302, "location": "https://x.test/en/p"}
check("a foreign Accept-Language redirected away is auto_redirect_by_language",
      [f["rule"] for f in C("https://x.test/de/p", "de", ok200, to_en, ok200)] == ["auto_redirect_by_language"])
check("Googlebot redirected away is googlebot_redirected_away (high)",
      [(f["rule"], f["severity"]) for f in C("https://x.test/de/p", "de", ok200, ok200, to_en)]
      == [("googlebot_redirected_away", "high")])
check("CONTROL: an alternate everyone gets as 200 is clean", C("https://x.test/de/p", "de", ok200, ok200, ok200) == [])
check("a forged Googlebot refused (403) is unknown, not a redirect finding",
      [f["rule"] for f in C("https://x.test/de/p", "de", ok200, ok200, {"status": 403, "location": None})]
      == ["googlebot_probe_refused"])
check("CONTROL: a redirect to the same URL (trailing slash) is not a language redirect",
      C("https://x.test/de/p", "de", ok200, {"status": 301, "location": "https://x.test/de/p/"}, ok200) == [])

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all hreflang tests passed")
