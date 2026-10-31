#!/usr/bin/env python3
"""
Page-level SEO checks: H1, title tag, meta description, and canonical.
Uses Python stdlib html.parser — no BeautifulSoup required.
Outputs structured JSON to stdout for direct agent consumption.

Usage:
    python check-page.py https://example.com
    python check-page.py https://example.com --timeout 20

Output example (JSON):
    {
      "url": "https://example.com/",
      "final_url": "https://example.com/",
      "http_status": 200,
      "redirect_chain": [],
      "h1": {
        "status": "pass",
        "count": 1,
        "values": ["Best Running Shoes 2025"],
        "detail": "Single H1 found."
      },
      "title": {
        "status": "pass",
        "value": "Best Running Shoes 2025 | Free Shipping",
        "length": 42,
        "detail": "Length 42 chars — likely shown in full."
      },
      "meta_description": {
        "status": "pass",
        "value": "Shop the best running shoes...",
        "length": 138,
        "detail": "Length 138 chars — likely shown in full."
      },
      "canonical": {
        "status": "pass",
        "value": "https://example.com/",
        "matches_final_url": true,
        "detail": "Self-referencing canonical present."
      }
    }

Dependencies:
    (HTML parsing uses Python stdlib html.parser — no extra packages needed)
"""

import argparse
import ipaddress
import json
import socket
import sys
from pathlib import Path
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_net  # noqa: E402 - stdlib fetches; see audit_net.py


# ── HTTP fetch ────────────────────────────────────────────────────────────────

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 ClaudeSEO/1.2"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
}

# 关键词过滤用停用词表（替代长度阈值，避免误杀 SEO/AI/CRM 等短缩写词）
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "not", "no",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "as",
    "is", "are", "was", "were", "be",
    "it", "its", "this", "that",
})


def _fetch(url: str, timeout: int) -> tuple[Optional[int], Optional[str], str, list[dict], Optional[str]]:
    """Fetch a page, every redirect hop checked against private addresses.
    Returns (status_code, content, final_url, redirect_chain, error)."""
    r = audit_net.get(url, timeout)
    if r.error:
        return None, None, r.url, r.history, r.error
    return r.status_code, r.text, r.url, r.history, None


# ── HTML parser (stdlib, no external dependencies) ────────────────────────────

class _SEOParser(HTMLParser):
    """
    Lightweight SEO element extractor.
    Extracts <title>, <h1>, <meta name="description">, and <link rel="canonical">.
    Does not build a full DOM tree — single-pass scan only.
    """

    def __init__(self) -> None:
        super().__init__()
        # title state
        self.title: Optional[str] = None
        self._in_title = False
        self._title_buf = ""
        # h1 state
        self.h1_values: list[str] = []
        self._in_h1 = False
        self._h1_depth = 0  # tracks nesting depth to handle inline tags inside <h1>
        self._h1_buf = ""
        # meta description
        self.meta_description: Optional[str] = None
        # canonical
        self.canonical: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}

        if tag == "title" and self.title is None:
            self._in_title = True
            self._title_buf = ""

        elif tag == "h1":
            self._in_h1 = True
            self._h1_depth += 1
            self._h1_buf = ""

        elif tag == "meta":
            name = attrs_dict.get("name", "").lower()
            if name == "description" and self.meta_description is None:
                self.meta_description = attrs_dict.get("content", "")

        elif tag == "link":
            rel = attrs_dict.get("rel", "").lower()
            if "canonical" in rel and self.canonical is None:
                self.canonical = attrs_dict.get("href", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = self._title_buf.strip() or None

        elif tag == "h1" and self._in_h1:
            self._h1_depth -= 1
            if self._h1_depth <= 0:
                self._in_h1 = False
                self._h1_depth = 0
                text = self._h1_buf.strip()
                if text:
                    self.h1_values.append(text)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_buf += data
        if self._in_h1:
            self._h1_buf += data


# ── Check functions ───────────────────────────────────────────────────────────

def _check_h1(h1_values: list[str], keyword: Optional[str] = None) -> dict:
    """
    H1 checks — two-layer design:

    Layer 1 (script): mechanical checks
      - Uniqueness: exactly one H1
      - Non-empty content
      - Length: warn if < 5 chars (brand-only) or > 70 chars
      - Keyword match: full / partial / none / unverified

    Layer 2 (LLM, triggered by llm_review_required=True):
      - When keyword_match == "partial": agent must judge semantic intent alignment.
        Script cannot determine if "Best Personal AI" covers intent for "AI computer".
        Agent reads h1_text + keyword and makes the call.

    Output fields:
      keyword_match      : "full" | "partial" | "none" | "unverified"
      llm_review_required: True when keyword_match == "partial" — agent must do semantic review
    """
    count = len(h1_values)

    if count == 0:
        return {
            "status": "fail",
            "count": 0,
            "values": [],
            "keyword_match": "unverified",
            "llm_review_required": False,
            "detail": "No H1 tag found. Every page should have exactly one H1 containing the primary keyword.",
        }

    if count > 1:
        return {
            "status": "fail",
            "count": count,
            "values": h1_values,
            "keyword_match": "unverified",
            "llm_review_required": False,
            "detail": (
                f"{count} H1 tags found. Multiple H1s dilute heading hierarchy "
                f"and make it harder for crawlers to identify the primary topic. "
                f"Keep exactly one H1; include the primary keyword or a natural variant."
            ),
        }

    # Exactly one H1 — run content checks
    h1_text = h1_values[0]

    if not h1_text.strip():
        return {
            "status": "fail",
            "count": 1,
            "values": h1_values,
            "keyword_match": "unverified",
            "llm_review_required": False,
            "detail": "H1 tag present but content is empty.",
        }

    length = len(h1_text)
    issues: list[str] = []

    # Length thresholds
    if length < 5:
        issues.append(
            f'H1 is very short ({length} chars): "{h1_text}". '
            "Likely brand-name only — add the primary keyword to signal page topic."
        )
    elif length > 70:
        issues.append(
            f"H1 is {length} characters — consider trimming to under 70 for readability."
        )

    # Keyword match — script does string-level detection only
    if keyword:
        h1_lower = h1_text.lower()
        kw_lower = keyword.lower().strip()
        kw_words = [w for w in kw_lower.split() if w not in _STOP_WORDS]
        full_match = kw_lower in h1_lower
        partial_match = not full_match and any(w in h1_lower for w in kw_words)

        if full_match:
            keyword_match = "full"
            llm_review_required = False
            keyword_note = f'Primary keyword "{keyword}" found in H1 (full match).'
        elif partial_match:
            # Script stops here — agent must decide if this is a valid natural variant
            keyword_match = "partial"
            llm_review_required = True
            keyword_note = (
                f'Partial string match for "{keyword}" in H1: "{h1_text}". '
                "Script cannot determine semantic intent alignment. "
                "LLM review required: does this H1 cover the search intent of the keyword?"
            )
            issues.append(keyword_note)
        else:
            keyword_match = "none"
            llm_review_required = False
            issues.append(
                f'Primary keyword "{keyword}" not found in H1: "{h1_text}". '
                "Homepage best practice: brand + primary keyword. "
                "Other pages: primary keyword or a natural variant."
            )
    else:
        keyword_match = "unverified"
        llm_review_required = False
        issues.append(
            "No --keyword provided. Keyword presence in H1 not checked. "
            "Pass --keyword <term> to enable this check."
        )

    if issues:
        return {
            "status": "warn",
            "count": 1,
            "values": h1_values,
            "keyword_match": keyword_match,
            "llm_review_required": llm_review_required,
            "detail": " | ".join(issues),
        }

    return {
        "status": "pass",
        "count": 1,
        "values": h1_values,
        "keyword_match": keyword_match,
        "llm_review_required": llm_review_required,
        "detail": f'Single H1 found: "{h1_text}". {keyword_note if keyword else ""}'.strip(),
    }


# Rough desktop display widths, used ONLY to say "the end may be cut". Google
# truncates by pixel width, not characters, and publishes no number.
DISPLAY_TITLE_CHARS = 60
DISPLAY_SNIPPET_CHARS = 160


def _check_title(title: Optional[str], keyword: Optional[str] = None) -> dict:
    """
    Title tag checks — two-layer design:

    Layer 1 (script): mechanical checks
      - Presence
      - Length: a display note only (Google sets no limit); < 10 chars is a placeholder
      - Keyword match: full / partial / none / unverified
      - Keyword position: "start" (within first 30 chars) / "middle" / "absent"

    Layer 2 (LLM, triggered by llm_review_required=True):
      - Is the title grammatically correct and naturally readable?
      - If keyword_match == "partial": does it semantically cover the intent?
      - If keyword_position != "start": should it be moved to the front?
        Best practice: lead with primary keyword for highest SEO weight.

    Output fields:
      keyword_match      : "full" | "partial" | "none" | "unverified"
      keyword_position   : "start" | "middle" | "absent" | "unverified"
      llm_review_required: True when quality or semantic judgment is needed
    """
    if not title:
        return {
            "status": "fail",
            "value": None,
            "length": 0,
            "keyword_match": "unverified",
            "keyword_position": "unverified",
            "llm_review_required": False,
            "detail": "No <title> tag found. Title is a critical on-page SEO element.",
        }

    length = len(title)
    issues: list[str] = []
    notes: list[str] = []

    # Length check. Google: "there's no limit on how long a <title> element
    # can be"; the title link is truncated "typically to fit the device width"
    # (developers.google.com/search/docs/appearance/title-link, 2026-10-25).
    # So length is never a defect on its own - only a placeholder is.
    if length < 10:
        issues.append(f"Title is only {length} characters — likely a placeholder.")
    elif length > DISPLAY_TITLE_CHARS:
        notes.append(f"Length {length} chars — Google sets no limit, but cuts the title link to the "
                     f"device width (roughly {DISPLAY_TITLE_CHARS} chars on desktop), so the end may "
                     "not show. Keep the words that matter before it.")
    else:
        notes.append(f"Length {length} chars — likely shown in full.")

    # Keyword checks
    llm_review_required = False
    if keyword:
        title_lower = title.lower()
        kw_lower = keyword.lower().strip()
        kw_words = [w for w in kw_lower.split() if w not in _STOP_WORDS]

        full_match = kw_lower in title_lower
        partial_match = not full_match and any(w in title_lower for w in kw_words)

        if full_match:
            keyword_match = "full"
            # Check position: keyword should appear in first 30 chars (high SEO weight zone)
            pos = title_lower.find(kw_lower)
            if pos <= 30:
                keyword_position = "start"
                notes.append(f'Keyword "{keyword}" leads the title — good SEO positioning.')
            else:
                keyword_position = "middle"
                issues.append(
                    f'Keyword "{keyword}" found at position {pos} — '
                    "best practice is to lead with the primary keyword (within first 30 chars)."
                )
                llm_review_required = True
        elif partial_match:
            keyword_match = "partial"
            keyword_position = "middle"
            llm_review_required = True
            issues.append(
                f'Partial match for "{keyword}" in title: "{title}". '
                "Script cannot determine semantic intent alignment. "
                "LLM review required: does this title cover the keyword's search intent? "
                "Is it grammatically natural?"
            )
        else:
            keyword_match = "none"
            keyword_position = "absent"
            issues.append(
                f'Primary keyword "{keyword}" not found in title. '
                "Lead with the primary keyword for strongest SEO signal."
            )
    else:
        keyword_match = "unverified"
        keyword_position = "unverified"
        llm_review_required = True
        issues.append(
            "No --keyword provided. Keyword presence and position in title not checked. "
            "LLM review required: verify title starts with the primary keyword and reads naturally."
        )

    detail_parts = issues + notes
    status = "fail" if length < 10 else ("warn" if issues else "pass")

    return {
        "status": status,
        "value": title,
        "length": length,
        "keyword_match": keyword_match,
        "keyword_position": keyword_position,
        "llm_review_required": llm_review_required,
        "detail": " | ".join(detail_parts),
    }


def _check_meta_description(meta_desc: Optional[str], keyword: Optional[str] = None) -> dict:
    """
    Meta description checks — two-layer design:

    Layer 1 (script): mechanical checks
      - Presence
      - Length: a display note only (Google sets no limit)
      - Keyword match: full / partial / none / unverified

    Layer 2 (LLM, always required when content is present):
      Script cannot judge writing quality. LLM must evaluate:
        - Is it 1-2 complete sentences (not fragments)?
        - Does it mention a concrete result, not vague fluff?
          Good: "Cut design time by 60% with AI-powered templates"
          Bad:  "The best tool for all your design needs"
        - Does it naturally include the primary keyword or a synonym?
        - Is keyword usage natural — not stuffed?
          Rule: keyword or close synonym should appear once, not repeated.
        - Is it more specific than what a typical competitor would write?

    Output fields:
      keyword_match      : "full" | "partial" | "none" | "unverified"
      llm_review_required: always True when content is present
    """
    if meta_desc is None:
        return {
            "status": "fail",
            "value": None,
            "length": 0,
            "keyword_match": "unverified",
            "llm_review_required": False,
            "detail": "No <meta name='description'> found. Missing meta descriptions reduce SERP snippet quality.",
        }

    if not meta_desc.strip():
        return {
            "status": "warn",
            "value": "",
            "length": 0,
            "keyword_match": "unverified",
            "llm_review_required": False,
            "detail": "Meta description tag present but content is empty.",
        }

    length = len(meta_desc)
    issues: list[str] = []
    notes: list[str] = []

    # Length check. Google: "There's no limit on how long a meta description
    # can be"; the snippet is truncated "typically to fit the device width"
    # (developers.google.com/search/docs/appearance/snippet, 2026-10-25).
    # Length is a display note, never a defect; quality is the LLM review.
    if length > DISPLAY_SNIPPET_CHARS:
        notes.append(f"Length {length} chars — Google sets no limit, but cuts the snippet to the "
                     f"device width (roughly {DISPLAY_SNIPPET_CHARS} chars on desktop), so the end "
                     "may not show.")
    else:
        notes.append(f"Length {length} chars — likely shown in full.")

    # Keyword match check
    if keyword:
        desc_lower = meta_desc.lower()
        kw_lower = keyword.lower().strip()
        kw_words = [w for w in kw_lower.split() if w not in _STOP_WORDS]

        full_match = kw_lower in desc_lower
        partial_match = not full_match and any(w in desc_lower for w in kw_words)

        if full_match:
            keyword_match = "full"
            notes.append(f'Keyword "{keyword}" present in meta description.')
        elif partial_match:
            keyword_match = "partial"
            issues.append(
                f'Partial match for "{keyword}" in meta description. '
                "LLM review required: check if a synonym covers the intent naturally."
            )
        else:
            keyword_match = "none"
            issues.append(
                f'Keyword "{keyword}" not found in meta description. '
                "Include the primary keyword or a natural synonym once."
            )
    else:
        keyword_match = "unverified"
        notes.append("No --keyword provided. Keyword presence not checked.")

    # Quality judgment is always LLM-only — script always sets this flag when content exists
    llm_review_required = True
    notes.append(
        "LLM review required: (1) complete sentence? (2) mentions concrete result not vague fluff? "
        "(3) keyword used naturally, not stuffed? (4) more specific than a typical competitor?"
    )

    status = "warn" if issues else "pass"
    detail_parts = issues + notes

    return {
        "status": status,
        "value": meta_desc,
        "length": length,
        "keyword_match": keyword_match,
        "llm_review_required": llm_review_required,
        "detail": " | ".join(detail_parts),
    }


def _check_canonical(canonical: Optional[str], final_url: str) -> dict:
    """Canonical tag check: presence and whether it points to the correct URL."""
    if not canonical:
        return {
            "status": "warn",
            "value": None,
            "matches_final_url": False,
            "detail": (
                "No <link rel='canonical'> found. "
                "Without a canonical tag, duplicate content issues may arise."
            ),
        }

    # Normalize comparison by stripping trailing slashes
    canonical_norm = canonical.rstrip("/")
    final_norm = final_url.rstrip("/")
    matches = canonical_norm == final_norm

    if matches:
        return {
            "status": "pass",
            "value": canonical,
            "matches_final_url": True,
            "detail": "Self-referencing canonical present.",
        }

    # Canonical points to a different URL — may be cross-domain canonical or misconfiguration
    return {
        "status": "warn",
        "value": canonical,
        "matches_final_url": False,
        "detail": (
            f"Canonical points to a different URL: {canonical}. "
            f"Final page URL is: {final_url}. "
            "Verify this is intentional (cross-domain canonical) and not a misconfiguration."
        ),
    }


def _check_url_slug(url: str, keyword: Optional[str] = None) -> dict:
    """
    URL slug checks — two-layer design:

    Layer 1 (script): mechanical checks
      - Homepage detection: skip check if path is "/" or empty
      - Lowercase only (no uppercase letters)
      - Hyphens as word separator (not underscores or spaces)
      - No special characters (only a-z, 0-9, hyphens, slashes)
      - Stop word presence: a, the, and, of, or, in, on, at, to, for, with, by
      - Repeated slug words (keyword stuffing signal)
      - Segment length: warn if any segment > 60 chars

    Layer 2 (LLM, triggered by llm_review_required=True):
      - Does the slug contain the primary keyword or a natural variant?
      - Does the path hierarchy make sense? (/category/primary-keyword)
      - Is it human-readable and concise?
      Best practice: /category/primary-keyword — hierarchical, short, no stop words.

    Output fields:
      slug             : extracted path (e.g. "/blog/best-running-shoes")
      is_homepage      : True if path is "/" or "" — check skipped
      keyword_match    : "full" | "partial" | "none" | "unverified"
      llm_review_required: True when keyword or readability judgment is needed
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"

    # Homepage — no slug to check
    if path in ("/", ""):
        return {
            "status": "pass",
            "slug": "/",
            "is_homepage": True,
            "keyword_match": "unverified",
            "llm_review_required": False,
            "detail": "Homepage detected — URL slug check not applicable.",
        }

    slug = path
    segments = [s for s in path.split("/") if s]
    issues: list[str] = []
    notes: list[str] = []

    # Lowercase check
    if slug != slug.lower():
        issues.append(
            f'Slug contains uppercase letters: "{slug}". '
            "Use all-lowercase for canonical URL consistency."
        )

    slug_lower = slug.lower()

    # Underscore check (hyphens preferred)
    if "_" in slug_lower:
        issues.append(
            "Underscores found in slug — use hyphens instead. "
            "Google treats hyphens as word separators; underscores join words."
        )

    # Special characters (allow only a-z, 0-9, hyphens, slashes)
    import re as _re
    if _re.search(r"[^a-z0-9\-/]", slug_lower):
        issues.append(
            "Slug contains special characters (spaces, %, ?, etc.). "
            "Use only lowercase letters, numbers, and hyphens."
        )

    # Stop words
    slug_stop_words = {"a", "the", "and", "of", "or", "in", "on", "at", "to", "for", "with", "by", "from"}
    all_slug_words: list[str] = []
    for seg in segments:
        all_slug_words.extend(seg.split("-"))
    found_stop = [w for w in all_slug_words if w in slug_stop_words]
    if found_stop:
        issues.append(
            f"Stop words in slug: {found_stop}. "
            "Remove unnecessary stop words (a, the, and, of…) to keep the slug concise."
        )

    # Repeated words (stuffing signal)
    word_counts: dict[str, int] = {}
    for w in all_slug_words:
        if len(w) > 2:
            word_counts[w] = word_counts.get(w, 0) + 1
    repeated = [w for w, c in word_counts.items() if c > 1]
    if repeated:
        issues.append(
            f"Repeated words in slug: {repeated}. "
            "Each meaningful word should appear once — avoid keyword stuffing in URLs."
        )

    # Segment length
    long_segments = [s for s in segments if len(s) > 60]
    if long_segments:
        issues.append(
            f"Slug segment(s) too long (> 60 chars): {long_segments}. "
            "Keep each path segment short and focused."
        )

    # Keyword match
    llm_review_required = False
    if keyword:
        kw_lower = keyword.lower().strip()
        kw_words = [w for w in kw_lower.split() if w not in _STOP_WORDS]
        full_match = kw_lower.replace(" ", "-") in slug_lower or kw_lower in slug_lower
        partial_match = not full_match and any(w in slug_lower for w in kw_words)

        if full_match:
            keyword_match = "full"
            notes.append(f'Primary keyword "{keyword}" found in slug.')
        elif partial_match:
            keyword_match = "partial"
            llm_review_required = True
            issues.append(
                f'Partial match for "{keyword}" in slug: "{slug}". '
                "LLM review required: does the slug reflect the keyword's primary intent? "
                "Best practice: /category/primary-keyword."
            )
        else:
            keyword_match = "none"
            llm_review_required = True
            issues.append(
                f'Primary keyword "{keyword}" not found in slug: "{slug}". '
                "Include the primary keyword in the slug. "
                "Recommended structure: /category/primary-keyword."
            )
    else:
        keyword_match = "unverified"
        llm_review_required = True
        notes.append(
            "No --keyword provided. Keyword presence in slug not checked. "
            "LLM review required: verify slug contains the primary keyword and follows "
            "/category/primary-keyword hierarchy."
        )

    status = "warn" if issues else "pass"
    detail_parts = issues + notes

    return {
        "status": status,
        "slug": slug,
        "is_homepage": False,
        "keyword_match": keyword_match,
        "llm_review_required": llm_review_required,
        "detail": " | ".join(detail_parts),
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run page-level SEO checks (H1, title, meta description, canonical) and output JSON."
    )
    parser.add_argument("url", help="Target page URL")
    parser.add_argument("--timeout", "-t", type=int, default=20, help="Request timeout in seconds")
    parser.add_argument("--keyword", "-k", help="Primary keyword to verify in H1 and title (optional)")
    args = parser.parse_args()

    url = args.url
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    status_code, content, final_url, redirect_chain, error = _fetch(url, args.timeout)

    base_result: dict = {
        "url": url,
        "final_url": final_url,
        "http_status": status_code,
        "redirect_chain": redirect_chain,
    }

    if error:
        base_result["error"] = error
        print(json.dumps(base_result, indent=2, ensure_ascii=False))
        sys.exit(1)

    if status_code != 200:
        base_result["error"] = f"Page returned HTTP {status_code} — cannot perform on-page checks."
        print(json.dumps(base_result, indent=2, ensure_ascii=False))
        sys.exit(1)

    if not content:
        base_result["error"] = "Page returned empty body."
        print(json.dumps(base_result, indent=2, ensure_ascii=False))
        sys.exit(1)

    # Parse HTML and run all checks
    seo_parser = _SEOParser()
    seo_parser.feed(content)

    output = {
        **base_result,
        "url_slug": _check_url_slug(final_url, keyword=args.keyword),
        "title": _check_title(seo_parser.title, keyword=args.keyword),
        "meta_description": _check_meta_description(seo_parser.meta_description, keyword=args.keyword),
        "h1": _check_h1(seo_parser.h1_values, keyword=args.keyword),
        "canonical": _check_canonical(seo_parser.canonical, final_url),
    }

    print(json.dumps(output, indent=2, ensure_ascii=False))

    # Exit with code 1 if any check is fail
    has_failure = any(
        output[key]["status"] == "fail"
        for key in ("url_slug", "title", "meta_description", "h1", "canonical")
    )
    sys.exit(1 if has_failure else 0)


if __name__ == "__main__":
    main()
