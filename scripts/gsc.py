#!/usr/bin/env python3
"""Google Search Console - the one engine that has no submission lever, read directly.

This is the counterpart to `bing.py`. It exists because three instruments in
this skill needed Search Console and none of them could reach it: `decay.py`
opens with "INPUT: search-analytics rows, from the `search-console` skill" and
made the operator export two JSON files by hand; `indexnow.py google-steps`
printed a paste-ready list because Google has never joined IndexNow; and the
post-deploy sequence lived in a per-project shell script that shelled out to a
different skill for its Google half. One capability, three places, none of them
able to run unattended.

  sites            verified properties, and the AUTH CONTROL - run it first
                   when anything else looks wrong. `permissionLevel` decides
                   whether `sitemap-submit` will work at all
  sitemaps         every sitemap Google holds, when it last DOWNLOADED each one,
                   and the warning/error counts
  sitemap-submit   ask Google to re-download a sitemap. Dry run until --yes
  inspect          URL Inspection: is this page indexed, when was it crawled,
                   which canonical did Google pick
  query            search-analytics rows, emitted in Google's OWN shape so
                   `decay.py` and `seostate.py` consume them unchanged
  decay-export     both windows `decay.py compare` wants, in one command

WHAT THIS CANNOT DO, stated up front because the internet lies about it:
there is no force-index API for ordinary pages. The Indexing API is restricted
to JobPosting and BroadcastEvent, and "Request indexing" is a human clicking a
button. `sitemap-submit` is the only programmatic nudge that exists, and it
asks Google to re-read a FEED, not to index a URL.

⚠ Three ways this API returns confident nonsense, each guarded here:

  * **`contents[].indexed` is stuck at 0** on properties where thousands of
    URLs are demonstrably indexed. It is reported as `null` with a note, never
    as a number, because "0 of 5,388 indexed" is the single most alarming and
    most wrong thing this endpoint can say.
  * **Every count is a STRING** (`"5388"`, `"0"`). Compared with `>` unconverted,
    `"9" > "5388"` is true and every threshold silently inverts.
  * **Search Console data lags 2-3 days.** A window ending today comes back
    thin or empty, and that is the LAG, not a collapse in traffic. Any window
    whose end is inside the lag is flagged, and `query` refuses a window that
    is entirely inside it rather than returning an honest-looking zero.

AUTH is a service-account JSON key at ~/.gsc_service_account.json (0600), whose
email has been added to the property in Search Console → Users and permissions.
GCP roles grant nothing here; only that does, and it must be **Full** or
`sitemap-submit` 403s. The RS256 JWT is signed in pure Python - no `openssl`
binary, no `cryptography` wheel - so this runs on a bare container like the
rest of the skill. The signer proves itself offline in `control`.

Stdlib only.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEY_PATH = Path(os.environ.get("GSC_SERVICE_ACCOUNT",
                               os.path.expanduser("~/.gsc_service_account.json")))
V3 = "https://searchconsole.googleapis.com/webmasters/v3"
V1 = "https://searchconsole.googleapis.com/v1"
SCOPE = "https://www.googleapis.com/auth/webmasters"

# Google's own documented processing delay. A window ending inside it is not a
# measurement of nothing; it is a measurement not taken yet.
DATA_LAG_DAYS = 3

# ---------------------------------------------------------------------------
# RS256, in stdlib. PKCS#8 -> RSAPrivateKey -> EMSA-PKCS1-v1_5 -> pow().
# ---------------------------------------------------------------------------

SHA256_DIGESTINFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _b64u(b: bytes) -> bytes:
    return base64.urlsafe_b64encode(b).rstrip(b"=")


def _der(buf: bytes, i: int = 0):
    """One DER TLV -> (tag, content, next_index). Enough for a PKCS#8 RSA key."""
    tag = buf[i]
    i += 1
    n = buf[i]
    i += 1
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(buf[i:i + k], "big")
        i += k
    return tag, buf[i:i + n], i + n


def rsa_key_from_pem(pem: str):
    """(n, e, d) from a PKCS#8 `BEGIN PRIVATE KEY` PEM.

    Service-account keys are PKCS#8, not PKCS#1, so the RSAPrivateKey sits
    wrapped inside a PrivateKeyInfo OCTET STRING. A parser written for
    `BEGIN RSA PRIVATE KEY` reads the version integer as the modulus and
    produces a signature that is merely wrong rather than an error.
    """
    body = "".join(l for l in pem.strip().splitlines() if "-----" not in l)
    der = base64.b64decode(body)
    _t, seq, _ = _der(der)
    _t, _ver, i = _der(seq)
    _t, _alg, i = _der(seq, i)
    _t, pk, i = _der(seq, i)
    _t, rsa, _ = _der(pk)
    vals, j = [], 0
    while j < len(rsa) and len(vals) < 4:
        _t, v, j = _der(rsa, j)
        vals.append(int.from_bytes(v, "big"))
    if len(vals) < 4:
        raise ValueError("not an RSAPrivateKey: fewer than 4 integers")
    _version, n, e, d = vals
    return n, e, d


def rs256(msg: bytes, n: int, d: int) -> bytes:
    k = (n.bit_length() + 7) // 8
    t = SHA256_DIGESTINFO + hashlib.sha256(msg).digest()
    if k < len(t) + 11:
        raise ValueError("modulus too small for RS256")
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")


def rs256_verify(msg: bytes, sig: bytes, n: int, e: int) -> bool:
    """Public-exponentiate the signature back and check it reconstructs.

    This is the control: it needs no network and no third party, so a broken
    signer is caught here rather than as an opaque `invalid_grant` from Google.
    """
    k = (n.bit_length() + 7) // 8
    try:
        em = pow(int.from_bytes(sig, "big"), e, n).to_bytes(k, "big")
    except (ValueError, OverflowError):
        return False
    return em.endswith(SHA256_DIGESTINFO + hashlib.sha256(msg).digest())


# ---------------------------------------------------------------------------


def out(payload, code=0):
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    sys.exit(code)


def refuse(reason, **extra):
    """`cannot ask` is never `the answer is no`."""
    out({"ok": False, "check": "gsc", "state": "cannot_ask", "reason": reason, **extra}, 2)


def load_service_account():
    if not KEY_PATH.exists():
        return None, (f"no service-account key at {KEY_PATH}. This is `no_key`, not "
                      "`no data`: nothing here has been measured. Create one in GCP, "
                      "enable searchconsole.googleapis.com, and add its client_email "
                      "to the property in Search Console -> Users and permissions "
                      "(Full, or sitemap-submit will 403).")
    try:
        sa = json.loads(KEY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"{KEY_PATH} is not JSON ({exc})"
    missing = [k for k in ("client_email", "private_key", "token_uri") if not sa.get(k)]
    if missing:
        return None, f"{KEY_PATH} is missing {missing}"
    return sa, None


_TOKEN_CACHE = {}


def access_token(timeout=30):
    sa, err = load_service_account()
    if err:
        refuse(err)
    hit = _TOKEN_CACHE.get("t")
    if hit and hit[1] > time.time() + 60:
        return hit[0]
    try:
        n, e, d = rsa_key_from_pem(sa["private_key"])
    except Exception as exc:                     # noqa: BLE001 - reported, not raised
        refuse(f"could not parse the private key ({exc})")
    now = int(time.time())
    hdr = {"alg": "RS256", "typ": "JWT"}
    if sa.get("private_key_id"):
        hdr["kid"] = sa["private_key_id"]
    claim = {"iss": sa["client_email"], "scope": SCOPE, "aud": sa["token_uri"],
             "iat": now, "exp": now + 3600}
    si = _b64u(json.dumps(hdr, separators=(",", ":")).encode()) + b"." + \
        _b64u(json.dumps(claim, separators=(",", ":")).encode())
    jwt = si + b"." + _b64u(rs256(si, n, d))
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": jwt.decode()}).encode()
    try:
        with urllib.request.urlopen(
                urllib.request.Request(sa["token_uri"], data=body), timeout=timeout) as r:
            tok = json.load(r)
    except urllib.error.HTTPError as ex:
        detail = ex.read()[:400].decode("utf-8", "replace")
        refuse(f"token exchange failed ({ex.code})", detail=detail,
               hint=("invalid_grant with a valid key usually means the clock is skewed "
                     "or the key was revoked; unauthorized_client means the service "
                     "account exists but the API is not enabled on its project"))
    except urllib.error.URLError as ex:
        refuse(f"token endpoint unreachable ({ex.reason})")
    _TOKEN_CACHE["t"] = (tok["access_token"], time.time() + int(tok.get("expires_in", 3600)))
    return tok["access_token"]


def call(url, payload=None, method=None, timeout=90):
    """One API call. An HTTP error is a NAMED state, never an empty result."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + access_token())
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw.strip() else {}, None
    except urllib.error.HTTPError as ex:
        body = ex.read()[:500].decode("utf-8", "replace")
        why = {
            403: ("permission denied. The service account is not on this property, or is "
                  "`Restricted` where `Full` is required. This is NOT 'no data'."),
            404: "no such property or resource on this account",
            429: "quota exhausted - retry later; this is not an empty result",
        }.get(ex.code, f"HTTP {ex.code}")
        return None, {"http": ex.code, "reason": why, "detail": body}
    except urllib.error.URLError as ex:
        return None, {"http": None, "reason": f"unreachable ({ex.reason})"}


def prop(a):
    """The property id. `sc-domain:example.com` and `https://example.com/` are
    DIFFERENT properties holding different data - never normalise one into the
    other."""
    p = a.property or os.environ.get("GSC_PROPERTY")
    if not p:
        cfg = Path(a.root or ".") / ".seo" / "config.json"
        if cfg.exists():
            try:
                p = json.loads(cfg.read_text(encoding="utf-8")).get("gsc_property")
            except json.JSONDecodeError:
                p = None
    if not p:
        refuse("no property given: pass --property, set GSC_PROPERTY, or put "
               "`gsc_property` in .seo/config.json. Run `gsc.py sites` to list them.")
    return p


def enc(s):
    return urllib.parse.quote(s, safe="")


def _int(v):
    """GSC returns counts as STRINGS. `\"9\" > \"5388\"` is True."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def lag_note(end: str):
    """Is this window's end inside Google's processing delay?"""
    try:
        e = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        return None
    edge = date.today() - timedelta(days=DATA_LAG_DAYS)
    if e > edge:
        return (f"window ends {end}, inside Google's {DATA_LAG_DAYS}-day processing "
                f"delay (complete only to {edge}). Thin or missing rows near the end "
                "are the LAG, not a traffic collapse.")
    return None


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_sites(a):
    d, err = call(f"{V3}/sites")
    if err:
        out({"ok": False, "check": "gsc-sites", "state": "cannot_ask", **err}, 2)
    rows = [{"property": s.get("siteUrl"), "permission": s.get("permissionLevel"),
             "can_submit_sitemap": s.get("permissionLevel") in ("siteOwner", "siteFullUser")}
            for s in d.get("siteEntry") or []]
    out({"ok": True, "check": "gsc-sites", "properties": rows, "count": len(rows),
         "reading": ("this is also the AUTH control - if it returns properties, the key, "
                     "the JWT and the API are all working, and a later failure is about "
                     "that one call. `can_submit_sitemap: false` means `Restricted`, and "
                     "sitemap-submit will 403 however correct the request is.")})


def cmd_sitemaps(a):
    p = prop(a)
    d, err = call(f"{V3}/sites/{enc(p)}/sitemaps")
    if err:
        out({"ok": False, "check": "gsc-sitemaps", "property": p, "state": "cannot_ask", **err}, 2)
    rows = []
    for s in d.get("sitemap") or []:
        contents = []
        for c in s.get("contents") or []:
            contents.append({
                "type": c.get("type"),
                "submitted": _int(c.get("submitted")),
                # NOT reported as a number. Measured on a property with
                # thousands of indexed URLs, this field reads "0" - it is a
                # known-stuck counter, and printing it would be a finding about
                # the site manufactured from a defect in the source.
                "indexed": None,
                "indexed_note": ("Google's `indexed` count on a sitemap is unreliable and "
                                 "commonly stuck at 0. Judge indexation with `inspect`, or "
                                 "with impressions from `query` - never from this field."),
            })
        rows.append({
            "path": s.get("path"),
            "last_submitted": s.get("lastSubmitted"),
            "last_downloaded": s.get("lastDownloaded"),
            "is_pending": s.get("isPending"),
            "is_sitemap_index": s.get("isSitemapsIndex"),
            "warnings": _int(s.get("warnings")),
            "errors": _int(s.get("errors")),
            "contents": contents,
        })
    out({"ok": True, "check": "gsc-sitemaps", "property": p, "sitemaps": rows,
         "count": len(rows),
         "reading": ("`last_downloaded` is the one that matters: `last_submitted` only "
                     "records that you asked. A download timestamp older than your last "
                     "deploy means Google has not seen the new feed yet.")})


def cmd_sitemap_submit(a):
    p = prop(a)
    url = a.sitemap
    if not url.startswith("http"):
        refuse("--sitemap must be the sitemap's absolute URL")
    if not a.yes:
        out({"ok": True, "check": "gsc-sitemap-submit", "state": "dry_run",
             "property": p, "sitemap": url,
             "would_call": f"PUT {V3}/sites/{enc(p)}/sitemaps/{enc(url)}",
             "note": ("This is the ONLY programmatic nudge Google offers, and it asks for a "
                      "FEED re-read, not for a URL to be indexed. Re-run with --yes."),
             "before_you_do": ("submit only after the deploy has settled - a submit inside a "
                               "release window points Google at a site that is 404ing, and "
                               "the CDN can cache those 404s for hours.")})
    _d, err = call(f"{V3}/sites/{enc(p)}/sitemaps/{enc(url)}", method="PUT")
    if err:
        out({"ok": False, "check": "gsc-sitemap-submit", "property": p, "sitemap": url,
             "state": "failed", **err}, 2)
    # Read it back. A 204 says the request was accepted, not that Google now
    # holds the feed - and "accepted" is exactly what a wrong URL also returns.
    back, berr = call(f"{V3}/sites/{enc(p)}/sitemaps/{enc(url)}")
    out({"ok": True, "check": "gsc-sitemap-submit", "property": p, "sitemap": url,
         "submitted": True,
         "confirmed": None if berr else {
             "last_submitted": back.get("lastSubmitted"),
             "last_downloaded": back.get("lastDownloaded"),
             "is_pending": back.get("isPending"),
             "warnings": _int(back.get("warnings")),
             "errors": _int(back.get("errors"))},
         "confirm_error": berr,
         "reading": ("`confirmed` is a read-back, not an echo. Google typically re-downloads "
                     "within seconds, so a `last_downloaded` that has not moved on a later "
                     "run is the thing to chase - the 204 alone proves only that the call "
                     "was accepted.")})


def cmd_inspect(a):
    p = prop(a)
    results, errors = [], []
    for u in a.url:
        d, err = call(f"{V1}/urlInspection/index:inspect",
                      {"inspectionUrl": u, "siteUrl": p})
        if err:
            errors.append({"url": u, **err})
            continue
        idx = (d.get("inspectionResult") or {}).get("indexStatusResult") or {}
        results.append({
            "url": u,
            "verdict": idx.get("verdict"),
            "coverage": idx.get("coverageState"),
            "robots": idx.get("robotsTxtState"),
            "indexing": idx.get("indexingState"),
            "last_crawled": idx.get("lastCrawlTime"),
            "fetch": idx.get("pageFetchState"),
            "google_canonical": idx.get("googleCanonical"),
            "user_canonical": idx.get("userCanonical"),
            "canonical_agrees": (idx.get("googleCanonical") == idx.get("userCanonical")
                                 if idx.get("googleCanonical") else None),
            "crawled_as": idx.get("crawledAs"),
            "referring_urls": idx.get("referringUrls") or [],
        })
    payload = {"ok": bool(results) or not errors, "check": "gsc-inspect", "property": p,
               "results": results, "errors": errors,
               "reading": ("`canonical_agrees: false` is the finding to act on - Google chose a "
                           "different canonical from the one the page declares, so the page's "
                           "signals are being credited elsewhere. "
                           "'Discovered - currently not indexed' is normal on a large "
                           "generated silo and is not fixed by resubmitting.")}
    out(payload, 0 if results else 2)


def _sa_query(p, start, end, dimensions, row_limit, start_row=0, dimension_filters=None,
              search_type="web", data_state=None):
    body = {"startDate": start, "endDate": end, "dimensions": dimensions,
            "rowLimit": row_limit, "startRow": start_row, "type": search_type}
    if dimension_filters:
        body["dimensionFilterGroups"] = [{"filters": dimension_filters}]
    if data_state:
        body["dataState"] = data_state
    return call(f"{V3}/sites/{enc(p)}/searchAnalytics/query", body)


def cmd_query(a):
    p = prop(a)
    edge = date.today() - timedelta(days=DATA_LAG_DAYS)
    try:
        s_d = datetime.strptime(a.start, "%Y-%m-%d").date()
    except ValueError:
        refuse("--start must be YYYY-MM-DD")
    if s_d > edge:
        refuse(f"the whole window starts {a.start}, after the last complete day ({edge}). "
               f"Search Console lags {DATA_LAG_DAYS} days; this would return an empty "
               "result that reads as zero traffic.",
               last_complete_day=str(edge))
    dims = [d.strip() for d in a.dimensions.split(",") if d.strip()]
    rows, start_row = [], 0
    while True:
        d, err = call(f"{V3}/sites/{enc(p)}/searchAnalytics/query",
                      {"startDate": a.start, "endDate": a.end, "dimensions": dims,
                       "rowLimit": min(a.limit - len(rows), 25000), "startRow": start_row,
                       "type": a.search_type})
        if err:
            out({"ok": False, "check": "gsc-query", "property": p, "state": "cannot_ask",
                 "rows_before_failure": len(rows), **err}, 2)
        page = d.get("rows") or []
        rows.extend(page)
        start_row += len(page)
        if len(page) < 25000 or len(rows) >= a.limit:
            break
    payload = {
        # Google's OWN shape, deliberately: decay.py and seostate.py read
        # `rows[].keys` / clicks / impressions / ctr / position directly, and a
        # helpfully-renamed field here would silently break both.
        "rows": rows,
        "ok": True,
        "check": "gsc-query",
        "property": p,
        "start": a.start,
        "end": a.end,
        "dimensions": dims,
        "row_count": len(rows),
        "truncated": len(rows) >= a.limit,
        "lag_warning": lag_note(a.end),
        "zero_rows_means": ("no impressions were recorded for this window and these "
                            "dimensions. It is a real zero only if `lag_warning` is null - "
                            "otherwise the window is simply not processed yet."
                            if not rows else None),
    }
    out(payload)


def cmd_decay_export(a):
    """Both windows `decay.py compare` wants, fetched in one command.

    Written because the manual version - export A, export B, remember which was
    which - is the shape that produced this skill's fake '100% page-1 churn':
    two files gathered under different conditions and diffed as if they were
    one measurement. Here both come from the same code path, the same
    dimensions and the same property, minutes apart.
    """
    p = prop(a)
    days = a.window
    edge = date.today() - timedelta(days=DATA_LAG_DAYS)
    cur_end = edge
    cur_start = cur_end - timedelta(days=days - 1)
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    dims = [d.strip() for d in a.dimensions.split(",") if d.strip()]
    written = {}
    for label, (s, e) in (("previous", (prev_start, prev_end)),
                          ("current", (cur_start, cur_end))):
        rows, start_row = [], 0
        while True:
            d, err = _sa_query(p, str(s), str(e), dims, min(a.limit - len(rows), 25000),
                               start_row, search_type=a.search_type)
            if err:
                out({"ok": False, "check": "gsc-decay-export", "window": label,
                     "state": "cannot_ask", **err}, 2)
            page = d.get("rows") or []
            rows.extend(page)
            start_row += len(page)
            if len(page) < 25000 or len(rows) >= a.limit:
                break
        path = Path(a.out_dir) / f"gsc-{label}.json"
        path.write_text(json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")
        written[label] = {"file": str(path), "start": str(s), "end": str(e),
                          "rows": len(rows)}
    out({"ok": True, "check": "gsc-decay-export", "property": p, "windows": written,
         "equal_length": True, "dimensions": dims,
         "next": (f"decay.py compare --previous {written['previous']['file']} "
                  f"--current {written['current']['file']} "
                  f"--previous-start {written['previous']['start']}"),
         "reading": ("both windows are the same length and adjacent, and both end on or "
                     "before the last complete day - an unequal or lag-clipped pair makes "
                     "every page look like it decayed.")})


# ---------------------------------------------------------------------------

def run_control():
    sys.path.insert(0, str(HERE))
    from controls import Controls          # noqa: PLC0415
    c = Controls("gsc-control")

    # The signer, proved offline. A broken RS256 does not fail loudly - it
    # returns `invalid_grant`, which reads as a revoked key or a clock problem.
    der, want_n, want_e, want_d = _test_pkcs8()
    key = ("-----BEGIN PRIVATE KEY-----\n"
           + base64.b64encode(der).decode() + "\n-----END PRIVATE KEY-----")
    n, e, d = rsa_key_from_pem(key)
    sig = rs256(b"combat skirmish", n, d)
    c.check("rs256_signature_verifies_against_its_own_public_key",
            rs256_verify(b"combat skirmish", sig, n, e),
            "a wrong signature reaches Google as invalid_grant, which reads as a "
            "revoked key rather than as a bug here")
    c.check("CONTROL_a_signature_over_different_bytes_does_NOT_verify",
            not rs256_verify(b"combat skirmish!", sig, n, e),
            "without this the verifier could be returning True unconditionally")
    c.check("CONTROL_a_tampered_signature_does_NOT_verify",
            not rs256_verify(b"combat skirmish", sig[:-1] + bytes([sig[-1] ^ 1]), n, e))
    # The expectation comes from the ENCODER, not from the parser - the two are
    # independent directions, so this is not a control that agrees with the code
    # by construction. A PKCS#1 parser pointed at PKCS#8 reads the version
    # integer as the modulus and returns 0, which is a number and would pass any
    # "is it big enough" test written against a fixture of the wrong size.
    c.check("pkcs8_parse_recovers_exactly_what_was_encoded",
            (n, e, d) == (want_n, want_e, want_d),
            f"parsed a {n.bit_length()}-bit modulus, expected {want_n.bit_length()}-bit")
    c.check("CONTROL_a_PKCS1_shaped_parse_would_NOT_have_matched", n != 0 and n != 1,
            "0 or 1 is what the version integer yields, and is the failure this guards")
    sa_now, _err = load_service_account()
    if sa_now:
        try:
            rn, _re, _rd = rsa_key_from_pem(sa_now["private_key"])
            c.check("the_configured_service_account_key_is_at_least_2048_bits",
                    rn.bit_length() >= 2048, f"{rn.bit_length()} bits")
        except Exception as exc:                 # noqa: BLE001
            c.check("the_configured_service_account_key_parses", False, str(exc))

    # String counts.
    c.check("string_counts_are_coerced", _int("5388") == 5388)
    c.check("CONTROL_a_missing_count_is_None_not_zero", _int(None) is None,
            "a zero here would report an empty sitemap")
    c.check("CONTROL_the_raw_string_comparison_this_guards_against_really_is_wrong",
            "9" > "5388",
            "if this were False the coercion would be unnecessary and the guard "
            "would be cargo-culted")

    # The lag.
    today = date.today()
    c.check("a_window_ending_today_is_flagged_as_lagged",
            lag_note(str(today)) is not None)
    c.check("CONTROL_an_old_window_is_not_flagged",
            lag_note(str(today - timedelta(days=30))) is None,
            "if everything were flagged the warning would carry no information")
    c.check("a_window_ending_exactly_at_the_edge_is_clean",
            lag_note(str(today - timedelta(days=DATA_LAG_DAYS))) is None)

    # Property identity.
    c.check("domain_and_url_properties_are_not_interchangeable",
            enc("sc-domain:x.test") != enc("https://x.test/"),
            "they are different properties holding different data")
    c.check("the_property_id_is_fully_encoded",
            enc("sc-domain:x.test") == "sc-domain%3Ax.test",
            "an unencoded colon makes the path a different resource")

    res = c.verdict()
    out(res, 0 if res.get("ok") else 2)


def _test_pkcs8():
    """A small RSA key, generated here, wrapped as PKCS#8 - so the control
    exercises the REAL parser rather than a hand-fed (n, e, d) tuple."""
    # Deterministic 1024-bit primes; this key signs nothing but test bytes.
    p = 0xc9b1a5d3f0e7c4b98d2f6a3e5c7b1d9f4a6e8c2b0d5f7a9e3c1b5d7f9a3e5c7b1d9f4a6e8c2b0d5f7a9e3c1b5d7f9a3e5c7b
    q = 0xd7f3b1a9e5c7d3f1b9a7e5c3d1f9b7a5e3c1d9f7b5a3e1c9d7f5b3a1e9c7d5f3b1a9e7c5d3f1b9a7e5c3d1f9b7a5e3c1d9f7
    p = _next_prime(p)
    q = _next_prime(q)
    n = p * q
    e = 65537
    d = pow(e, -1, (p - 1) * (q - 1))

    def i(v):
        b = v.to_bytes((v.bit_length() + 8) // 8, "big")
        return b"\x02" + _len(len(b)) + b

    def _seq(b):
        return b"\x30" + _len(len(b)) + b

    dp = pow(d, 1, p - 1)
    dq = pow(d, 1, q - 1)
    qi = pow(q, -1, p)
    rsa = _seq(i(0) + i(n) + i(e) + i(d) + i(p) + i(q) + i(dp) + i(dq) + i(qi))
    alg = _seq(b"\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x01\x01\x05\x00")
    octet = b"\x04" + _len(len(rsa)) + rsa
    return _seq(i(0) + alg + octet), n, e, d


def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _next_prime(n: int) -> int:
    n |= 1
    while not _probably_prime(n):
        n += 2
    return n


def _probably_prime(n: int, rounds: int = 16) -> bool:
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    r, s = 0, n - 1
    while s % 2 == 0:
        r += 1
        s //= 2
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)[:rounds]:
        x = pow(a, s, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=".", help="repo root holding .seo/")
    p.add_argument("--property", help="sc-domain:example.com or https://example.com/")
    sub = p.add_subparsers(dest="action", required=True)

    sub.add_parser("control", help="prove the signer, the coercions and the lag guard")

    sub.add_parser("sites", help="verified properties (also the auth control)")
    sub.add_parser("sitemaps", help="every sitemap Google holds, and when it read each")

    s = sub.add_parser("sitemap-submit", help="ask Google to re-download a sitemap")
    s.add_argument("--sitemap", required=True, help="absolute sitemap URL")
    s.add_argument("--yes", action="store_true", help="actually submit (default: dry run)")

    s = sub.add_parser("inspect", help="URL Inspection for one or more URLs")
    s.add_argument("--url", action="append", required=True, help="repeatable")

    s = sub.add_parser("query", help="search-analytics rows, in Google's own shape")
    s.add_argument("--start", required=True)
    s.add_argument("--end", required=True)
    s.add_argument("--dimensions", default="page")
    s.add_argument("--limit", type=int, default=25000)
    s.add_argument("--search-type", default="web")

    s = sub.add_parser("decay-export", help="both windows decay.py compare wants")
    s.add_argument("--window", type=int, default=28, help="days per window")
    s.add_argument("--dimensions", default="page,query")
    s.add_argument("--limit", type=int, default=25000)
    s.add_argument("--search-type", default="web")
    s.add_argument("--out-dir", default=".")

    a = p.parse_args()
    {"control": lambda _a: run_control(), "sites": cmd_sites, "sitemaps": cmd_sitemaps,
     "sitemap-submit": cmd_sitemap_submit, "inspect": cmd_inspect, "query": cmd_query,
     "decay-export": cmd_decay_export}[a.action](a)


if __name__ == "__main__":
    main()
