#!/usr/bin/env python3
"""Corpus sameness gate - does this draft read like the site's own back catalogue?

The single most valuable piece of the whole system, because nothing else checks
this:

  Template convergence across a site's OWN articles is the scaled-content-abuse
  fingerprint search engines discount. Every post sharing an intro pattern, a
  heading skeleton and the same stock phrases reads as one template wearing N
  keywords. It is INVISIBLE in per-article review - each one looks fine alone -
  and no commercial content tool checks it: Surfer, Clearscope and MarketMuse
  all benchmark a draft against COMPETITORS, never against your own catalogue.

Everything here is deterministic string math. No model judgement, no API. That
is the point: an agent grading its own prose for sameness is exactly the
vibes-based check this replaces.

Three signals:
  opening   an identical 6-word run in the first real paragraph
  headings  >60% of the same H2s once the KEYWORD TOKENS ARE STRIPPED. That
            strip is the whole trick - "What is <kw>?" and "What is <other>?"
            both collapse to "what is", and the template shows itself.
  phrases   >3 five-word shingles shared with MOST of the corpus. One shared
            phrase is a coincidence; half the catalogue is house style.

Usage:
    sameness.py check --draft new-post.md --corpus content/blog --keyword "rank tracker"
    sameness.py audit --corpus content/blog          # pairwise drift report
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from html import unescape
from pathlib import Path

OPENING_NGRAM = 6          # an identical 6-word opener is a template tell
HEADING_OVERLAP_LIMIT = 0.6  # >60% of the same normalized H2s
STOCK_PHRASE_LEN = 5       # 5-word shingles
STOCK_PHRASE_LIMIT = 3     # >3 shared across half the corpus
CORPUS_SIZE = 8            # recent guides to compare against

WORD = re.compile(r"[a-z0-9']+")


sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls  # noqa: E402


def tokenize(text: str) -> list[str]:
    return WORD.findall((text or "").lower())


def normalize_heading(heading: str, keyword_tokens: set[str]) -> str:
    return " ".join(w for w in tokenize(heading) if w not in keyword_tokens).strip()


def shingles(words: list[str], n: int) -> set[str]:
    return {" ".join(words[i : i + n]) for i in range(0, max(0, len(words) - n + 1))}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    shared = len(a & b)
    return shared / (len(a) + len(b) - shared)


# ------------------------------------------------------------- extraction


def strip_markdown(md: str) -> str:
    md = re.sub(r"^---\n.*?\n---", " ", md, flags=re.S)        # frontmatter
    md = re.sub(r"```.*?```", " ", md, flags=re.S)             # fenced code
    md = re.sub(r"`[^`]*`", " ", md)                           # inline code
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", md)              # images
    # Link ANCHOR TEXT goes too, on both sides of the comparison: the body
    # contract mandates 2-3 links to sibling guides, so anchors are sibling
    # TITLES, which also appear in every published page's related rail. Kept,
    # they flag a compliant draft for obeying its instructions.
    md = re.sub(r"\[[^\]]*\]\([^)]*\)", " ", md)
    md = re.sub(r"<[^>]+>", " ", md)                           # stray jsx/html
    return re.sub(r"[#*_>|]", " ", md)


def extract_markdown(md: str, keyword_tokens: set[str]) -> dict:
    headings = [
        h for h in (normalize_heading(m.group(1), keyword_tokens)
                    for m in re.finditer(r"^##\s+(.+)$", md, re.M))
        if h
    ]
    body = strip_markdown(md)
    first_para = next((p.strip() for p in re.split(r"\n\s*\n", body) if len(p.strip()) > 40), "")
    return {"opening": tokenize(first_para)[:12], "headings": headings, "words": tokenize(body)}


ENTITIES = [
    (re.compile(r"&(?:#0*39|#x0*27|apos|lsquo|rsquo);", re.I), "'"),
    (re.compile(r"&(?:#0*34|#x0*22|quot|ldquo|rdquo);", re.I), '"'),
    (re.compile(r"&(?:#0*160|nbsp);", re.I), " "),
    (re.compile(r"&(?:#0*38|#x0*26|amp);", re.I), "&"),
    (re.compile(r"&(?:#0*60|#x0*3c|lt);", re.I), "<"),
    (re.compile(r"&(?:#0*62|#x0*3e|gt);", re.I), ">"),
]


def decode_entities(s: str) -> str:
    # Decode the entities that actually appear in rendered prose, so HTML
    # tokenizes identically to the markdown side. Measured on a real guide:
    # &#x27; appears 27 times, and without this every "wasn't" becomes
    # "wasn"+"x27"+"t" and silently fails to match the same contraction in a
    # draft - a false PASS, the expensive direction for this gate.
    for pat, rep in ENTITIES:
        s = pat.sub(rep, s)
    return re.sub(r"&[a-z]+;|&#x?[0-9a-f]+;", " ", s, flags=re.I)


def extract_html(html_text: str, keyword_tokens: set[str]) -> dict:
    main = re.sub(r"<(script|style|nav|header|footer|aside)[\s\S]*?</\1>", " ", html_text, flags=re.I)
    main = re.sub(r"<!--[\s\S]*?-->", " ", main)

    def clean(chunk: str) -> str:
        # Anchors are stripped INSIDE an already-bounded chunk, never across
        # the document: a page-wide <a...>...</a> swallowed four real
        # paragraphs on a live guide, because the next </a> it found sat far
        # below the one it started from.
        chunk = re.sub(r"<a[^>]*>[\s\S]*?</a>", " ", chunk, flags=re.I)
        return re.sub(r"\s+", " ", decode_entities(re.sub(r"<[^>]+>", " ", chunk))).strip()

    headings = [
        h for h in (normalize_heading(clean(m.group(1)), keyword_tokens)
                    for m in re.finditer(r"<h2[^>]*>([\s\S]*?)</h2>", main, re.I))
        if h
    ]
    # PROSE ONLY - the <p> elements. Related-post rails, card grids and tag
    # chips live in divs and lists and pour every OTHER guide's title into
    # this one's word pool; measured against a real site that leak was 64 of
    # the 65 "shared phrases" the gate found. <pre> drops out for free, which
    # is right: the same install command SHOULD repeat across guides.
    paragraphs = [p for p in (clean(m.group(1)) for m in re.finditer(r"<p[^>]*>([\s\S]*?)</p>", main, re.I)) if p]
    first_para = next((p for p in paragraphs if len(p) > 40), "")
    return {
        "opening": tokenize(first_para)[:12],
        "headings": headings,
        "words": tokenize(" ".join(paragraphs)),
    }


def extract_any(path: Path, keyword_tokens: set[str]) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in (".html", ".htm"):
        return extract_html(text, keyword_tokens)
    return extract_markdown(text, keyword_tokens)


# Words too generic to be a topic - stripping them would collapse unrelated
# headings and manufacture matches.
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with", "your",
    "you", "how", "what", "why", "guide", "complete", "best", "using", "use", "is",
}


def own_keyword_tokens(path: Path, text: str, pages_map: dict[str, str] | None) -> set[str]:
    """The keyword tokens to strip from THIS document's headings.

    Critical detail, and the one that makes the whole check work: every corpus
    entry is stripped of ITS OWN keyword, never the draft's. That is what
    collapses "What is rank tracking?" and "What is keyword research?" to the
    same "what is" skeleton and exposes the template. Stripping the draft's
    keyword everywhere instead would find almost nothing.

    Source order: an explicit pages.json mapping (the recorded primary
    keyword), then frontmatter title, then the filename slug.
    """
    if pages_map:
        stem = path.stem.lower()
        for key, kw in pages_map.items():
            if key and (key == stem or key.endswith("/" + stem) or stem.endswith(key)):
                return {t for t in tokenize(kw) if t not in STOPWORDS}
    m = re.search(r"^---\n(.*?)\n---", text, re.S)
    if m:
        t = re.search(r"^\s*title\s*:\s*[\"']?(.+?)[\"']?\s*$", m.group(1), re.M)
        if t:
            return {tok for tok in tokenize(t.group(1)) if tok not in STOPWORDS}
    return {tok for tok in tokenize(path.stem.replace("-", " ").replace("_", " ")) if tok not in STOPWORDS}


def load_pages_map(pages_json: str | None) -> dict[str, str] | None:
    """slug -> primary_keyword, from a .seo/pages.json written by seostate.py."""
    if not pages_json:
        return None
    try:
        rows = json.loads(Path(pages_json).read_text(encoding="utf-8"))
    except Exception:
        return None
    out = {}
    for r in rows if isinstance(rows, list) else []:
        url = (r.get("url") or "").rstrip("/")
        kw = r.get("primary_keyword")
        if url and kw:
            out[url.rsplit("/", 1)[-1].lower()] = kw
    return out or None


def load_corpus(root: Path, limit: int, exclude: Path | None, pages_map: dict[str, str] | None) -> list[dict]:
    exts = {".md", ".mdx", ".markdown", ".html", ".htm"}
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts]
    if exclude:
        ex = exclude.resolve()
        files = [p for p in files if p.resolve() != ex]
    # Newest first: the gate compares against what the site published RECENTLY,
    # which is where template convergence actually lives.
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    entries = []
    for p in files[:limit]:
        text = p.read_text(encoding="utf-8", errors="replace")
        tokens = own_keyword_tokens(p, text, pages_map)
        feat = extract_html(text, tokens) if p.suffix.lower() in (".html", ".htm") else extract_markdown(text, tokens)
        if not feat["words"]:
            continue
        entries.append({**feat, "label": str(p), "stripped": sorted(tokens)})
    return entries


# ------------------------------------------------------------- comparison


def compare_to_corpus(draft: dict, corpus: list[dict]) -> dict:
    if not corpus:
        return {
            "pass": True,
            "compared_against": 0,
            "flags": [],
            "note": "Nothing published yet to compare against - nothing to converge on. Pass.",
        }
    flags = []

    draft_open = shingles(draft["opening"], OPENING_NGRAM)
    for entry in corpus:
        for gram in shingles(entry["opening"], OPENING_NGRAM):
            if gram in draft_open:
                flags.append({
                    "kind": "opening",
                    "detail": f'Opening repeats a {OPENING_NGRAM}-word run: "{gram}"',
                    "against": entry["label"],
                })
                break

    draft_heads = set(draft["headings"])
    for entry in corpus:
        overlap = jaccard(draft_heads, set(entry["headings"]))
        if overlap > HEADING_OVERLAP_LIMIT:
            shared = [h for h in entry["headings"] if h in draft_heads]
            flags.append({
                "kind": "headings",
                "detail": (
                    f"Heading skeleton is {round(overlap * 100)}% the same "
                    f"(limit {round(HEADING_OVERLAP_LIMIT * 100)}%). Shared once the keyword is "
                    f"stripped: " + ", ".join(f'"{h}"' for h in shared[:5])
                ),
                "against": entry["label"],
            })

    draft_shingles = shingles(draft["words"], STOCK_PHRASE_LEN)
    spread: dict[str, int] = {}
    for entry in corpus:
        for s in shingles(entry["words"], STOCK_PHRASE_LEN):
            if s in draft_shingles:
                spread[s] = spread.get(s, 0) + 1
    majority = max(2, -(-len(corpus) // 2))
    stock = sorted(p for p, n in spread.items() if n >= majority)
    if len(stock) > STOCK_PHRASE_LIMIT:
        flags.append({
            "kind": "phrases",
            "detail": (
                f"{len(stock)} stock phrases shared with most recent guides "
                f"(limit {STOCK_PHRASE_LIMIT}): " + ", ".join(f'"{s}"' for s in stock[:8])
            ),
            "against": f"{majority}+ of {len(corpus)} guides",
        })

    ok = not flags
    return {
        "pass": ok,
        "compared_against": len(corpus),
        "flags": flags,
        "note": (
            f"Reads as its own piece against the last {len(corpus)} guides."
            if ok
            else "Too close to what this site already published - rewrite the flagged elements "
                 "(never loosen the check) and re-run."
        ),
    }


def pair_score(a: dict, b: dict) -> float:
    headings = jaccard(set(a["headings"]), set(b["headings"]))
    body = jaccard(shingles(a["words"], STOCK_PHRASE_LEN), shingles(b["words"], STOCK_PHRASE_LEN))
    opening = jaccard(shingles(a["opening"], OPENING_NGRAM), shingles(b["opening"], OPENING_NGRAM))
    # Heading skeleton carries the most template signal; body shingles catch
    # house-style crutches; a shared opener is rare enough to be damning.
    return round(headings * 0.5 + body * 0.3 + opening * 0.2, 4)


# ------------------------------------------------------------------- main


def run_control() -> dict:
    """Prove the extractor and the similarity metric still discriminate.

    Every check is a way this gate has been, or would be, silently wrong. The
    expensive direction here is a false PASS - a gate that reports "nothing is
    converging" because its extractor read nothing at all."""
    c = Controls("sameness-control")

    same_a = "the bomb site is covered from the doors and from the ramp above it"
    doc_a = {"opening": tokenize(same_a), "headings": ["setup", "timings"],
             "words": tokenize(same_a * 3)}
    doc_b = dict(doc_a)
    doc_c = {"opening": tokenize("recoil resets after a short pause between bursts"),
             "headings": ["recoil", "spray"],
             "words": tokenize("recoil resets after a short pause between bursts " * 3)}

    c.check("identical_docs_score_high", pair_score(doc_a, doc_b) > 0.8,
            f"got {pair_score(doc_a, doc_b)}")
    c.check("unrelated_docs_score_low", pair_score(doc_a, doc_c) < 0.2,
            f"got {pair_score(doc_a, doc_c)}")
    c.check("the_two_are_separated", pair_score(doc_a, doc_b) > pair_score(doc_a, doc_c) * 3)

    c.check("jaccard_identical_is_one", jaccard({"a", "b"}, {"a", "b"}) == 1.0)
    c.check("jaccard_disjoint_is_zero", jaccard({"a"}, {"b"}) == 0.0)
    c.check("empty_shingles_do_not_crash", jaccard(shingles([], 5), shingles([], 5)) == 0.0)

    # THE EXTRACTOR. A reader that returns nothing makes every draft unique,
    # which is a PASS - the direction that costs the most to be wrong in.
    html = ("<html><head><style>.x{}</style></head><body>"
            "<nav><a href='/g/1'>Other guide title here</a></nav>"
            "<h2>Holding the site</h2>"
            "<p>The bomb site is covered from the doors and from the ramp above "
            "it, so one player can watch both without moving.</p>"
            "<!-- a comment that is not prose -->"
            "<footer><a href='/g/2'>Another guide title</a></footer></body></html>")
    ex = extract_html(html, set())
    c.check("extractor_reads_paragraph_prose", len(ex["words"]) >= 15,
            f"got {len(ex['words'])} words - an empty extractor passes every draft")
    c.check("extractor_reads_h2", ex["headings"] == ["holding the site"], str(ex["headings"]))
    c.check("extractor_drops_nav_and_footer_anchors",
            "another" not in ex["words"] and "other" not in ex["words"], str(ex["words"])[:200])
    c.check("extractor_drops_comments", "comment" not in ex["words"])
    c.check("extractor_takes_an_opening", len(ex["opening"]) >= 5)

    # The &#x27; bug: an entity that does not decode splits a contraction and
    # silently fails to match the same word on the markdown side.
    ent = extract_html("<p>it &#x27;s the same phrase repeated across guides here</p>", set())
    c.check("entities_decode_rather_than_shatter_words",
            "x27" not in ent["words"], str(ent["words"]))

    # An anchor must be stripped inside its chunk, never across the document.
    wide = extract_html("<p>first real paragraph of prose here <a href='#'>link</a></p>"
                        "<p>second real paragraph of prose here too</p>", set())
    c.check("anchor_strip_does_not_swallow_later_paragraphs",
            "second" in wide["words"], str(wide["words"]))

    c.check("empty_corpus_passes_and_says_why",
            compare_to_corpus(doc_a, [])["pass"] is True)
    hit = compare_to_corpus(doc_a, [dict(doc_b, label="published")])
    c.check("a_repeated_opening_is_flagged", len(hit["flags"]) > 0)
    return c.verdict()


def cmd_check(a):
    keyword_tokens = set(tokenize(a.keyword or ""))
    draft_path = Path(a.draft)
    if a.draft == "-":
        text = sys.stdin.read()
        draft = extract_markdown(text, keyword_tokens)
    else:
        if not draft_path.exists():
            print(json.dumps({"ok": False, "error": f"draft not found: {draft_path}"}))
            sys.exit(1)
        draft = extract_any(draft_path, keyword_tokens)
    corpus_root = Path(a.corpus)
    if not corpus_root.exists():
        print(json.dumps({
            "ok": True, "pass": True, "compared_against": 0, "flags": [],
            "note": f"Corpus directory {corpus_root} does not exist - nothing to converge on. "
                    "Pass, but say so in the run report (corpus unreadable).",
        }, indent=2))
        return
    corpus = load_corpus(corpus_root, a.corpus_size,
                         draft_path if a.draft != "-" else None,
                         load_pages_map(a.pages))
    verdict = compare_to_corpus(draft, corpus)
    print(json.dumps({
        "ok": True,
        "keyword": a.keyword,
        "draft": a.draft,
        "corpus_dir": str(corpus_root),
        "draft_stats": {"opening_words": len(draft["opening"]), "h2_count": len(draft["headings"]),
                        "body_words": len(draft["words"])},
        **verdict,
        "on_fail": "REWRITE what was flagged - a genuinely different opening, different H2 wording "
                   "and order, kill the named phrases - then re-run. Never argue with a fail, never "
                   "ship past one, never 'fix' it by loosening the check. Bounded at THREE attempts: "
                   "if it still fails, the TOPIC duplicates an existing page - set the suggestion "
                   "back to pending and exit without a PR.",
    }, indent=2, ensure_ascii=False))
    if not verdict["pass"] and a.exit_code:
        sys.exit(3)


def cmd_audit(a):
    corpus_root = Path(a.corpus)
    entries = load_corpus(corpus_root, a.corpus_size, None, load_pages_map(a.pages))
    pairs = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            score = pair_score(entries[i], entries[j])
            if score >= a.threshold:
                pairs.append({"a": entries[i]["label"], "b": entries[j]["label"], "score": score})
    pairs.sort(key=lambda p: -p["score"])
    worst = pairs[:20]
    avg = round(sum(p["score"] for p in pairs) / len(pairs), 4) if pairs else 0.0
    print(json.dumps({
        "ok": True,
        "documents": len(entries),
        "pairs_over_threshold": len(pairs),
        "threshold": a.threshold,
        "mean_score_over_threshold": avg,
        "worst_pairs": worst,
        "reading": "0 = unrelated, 1 = identical. Anything over ~0.45 shares a real template; "
                   "over ~0.6 the two posts are the same article with the nouns swapped. Fix by "
                   "rewriting the newer one's heading skeleton and opening, not by deleting.",
    }, indent=2, ensure_ascii=False))


# ------------------------------------------------- corpus-scale (programmatic)

def cmd_tiers(a):
    """Index-bloat analysis across a WHOLE generated corpus.

    `audit` is pairwise and therefore O(n^2): fine for 60 hand-written guides,
    impossible for the 2,000-page silo a generator produces (3.5M comparisons).
    This is the O(n) form, and it measures the thing that actually matters at
    that scale - not "are these two similar" but "how much of this page exists
    ONLY on this page".

    Method: build a document-frequency map of every 5-word shingle in the
    corpus. A shingle carried by most documents is template boilerplate no
    matter how well written. Each page's unique ratio is the share of its
    shingles that are rare corpus-wide. That single number is what separates a
    generated page carrying real per-record content from one that is a header,
    a footer, and a swapped noun.

    WHAT THIS DOES NOT DECIDE. A low ratio is a RISK, not a verdict, and this
    is the mistake the thin-content literature invites: template share is a
    property of the writing, while indexation is a property of what Google
    chose to do about it. They come apart constantly - a page can be 3% unique
    prose and still be indexed, ranking and useful, because its unique content
    is an IMAGE, a data table, or a live server list that shingles cannot see.
    So the verdict here is deliberately conditional, and it names the index
    evidence you have to go and get before acting on it.
    """
    root = Path(a.corpus)
    if not root.is_dir():
        print(json.dumps({"ok": False, "error": f"{a.corpus} is not a directory"}))
        sys.exit(2)
    exts = {".md", ".mdx", ".markdown", ".html", ".htm"}
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts)
    if a.include:
        rx = re.compile(a.include)
        files = [p for p in files if rx.search(str(p))]
    if a.exclude_re:
        rx = re.compile(a.exclude_re)
        files = [p for p in files if not rx.search(str(p))]
    if not files:
        print(json.dumps({"ok": False, "error": "no documents matched",
                          "corpus": str(root), "include": a.include}))
        sys.exit(2)
    if a.limit and len(files) > a.limit:
        step = len(files) / a.limit
        files = [files[int(i * step)] for i in range(a.limit)]
        sampled = True
    else:
        sampled = False

    df: Counter = Counter()
    docs = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        feat = extract_html(text, set()) if p.suffix.lower() in (".html", ".htm") else extract_markdown(text, set())
        words = feat["words"]
        if not words:
            continue
        sh = shingles(words, STOCK_PHRASE_LEN)
        if not sh:
            continue
        docs.append({"path": str(p), "words": len(words), "shingles": sh,
                     "headings": feat["headings"]})
        df.update(sh)

    n = len(docs)
    if n < 2:
        print(json.dumps({"ok": False, "error": "need at least 2 readable documents", "read": n}))
        sys.exit(2)

    rare_cutoff = max(1, int(n * a.rare_below))
    buckets = Counter()
    rows = []
    sig_groups = defaultdict(list)
    for d in docs:
        rare = {s for s in d["shingles"] if df[s] <= rare_cutoff}
        ratio = len(rare) / len(d["shingles"])
        d["unique_ratio"] = round(ratio, 4)
        rows.append(d)
        buckets[min(int(ratio * 20), 19)] += 1
        # Exact-template siblings: pages whose entire rare set is empty, or
        # whose rare shingles are identical, are the same page twice.
        sig = hash(frozenset(sorted(rare)[: a.sig_size])) if rare else 0
        sig_groups[sig].append(d["path"])

    rows.sort(key=lambda d: d["unique_ratio"])
    ratios = sorted(d["unique_ratio"] for d in rows)
    median = ratios[n // 2]
    mean = round(sum(ratios) / n, 4)

    under_hard = [d for d in rows if d["unique_ratio"] < a.hard]
    under_warn = [d for d in rows if a.hard <= d["unique_ratio"] < a.warn]
    thin_words = [d for d in rows if d["words"] < a.min_words]
    dupe_groups = sorted(
        ([len(v), v[:4]] for k, v in sig_groups.items() if len(v) > 1),
        reverse=True,
    )[:10]

    if median < a.hard:
        verdict = "HIGH RISK"
        note = (f"median unique ratio {median:.1%} is below the {a.hard:.0%} hard line: most of this "
                f"corpus is template. That is the scaled-content-abuse shape.")
    elif median < a.warn:
        verdict = "WATCH"
        note = (f"median unique ratio {median:.1%} sits between the {a.hard:.0%} hard line and the "
                f"{a.warn:.0%} warning line - defensible only if each page carries unique NON-PROSE "
                f"value (an image, a dataset, a live feed) that shingles cannot measure.")
    else:
        verdict = "OK"
        note = f"median unique ratio {median:.1%} clears the {a.warn:.0%} line."

    print(json.dumps({
        "ok": True,
        "corpus": str(root),
        "documents": n,
        "sampled": sampled,
        "shingle_len": STOCK_PHRASE_LEN,
        "rare_cutoff_docs": rare_cutoff,
        "unique_ratio": {"median": median, "mean": mean,
                         "p10": ratios[int(n * 0.1)], "p90": ratios[int(n * 0.9)]},
        "thresholds": {"hard": a.hard, "warn": a.warn, "min_words": a.min_words},
        "verdict": verdict,
        "note": note,
        "counts": {
            "below_hard": len(under_hard),
            "between_hard_and_warn": len(under_warn),
            "under_min_words": len(thin_words),
            "exact_template_groups": len(dupe_groups),
        },
        "histogram_5pct_buckets": {f"{k*5}-{k*5+5}%": v for k, v in sorted(buckets.items())},
        "worst": [{"path": d["path"], "unique_ratio": d["unique_ratio"], "words": d["words"]}
                  for d in rows[: a.top]],
        "exact_template_groups": [{"count": c, "sample": s} for c, s in dupe_groups],
        "before_you_act": [
            "This is a RISK measurement, not an indexation verdict. Get the index evidence first:",
            "1. Search Console URL Inspection on 5-10 pages spread across the ratio range. "
            "'Submitted and indexed' on a 3%-unique page means Google has already judged it and "
            "kept it - the prose ratio is not what it is judging on.",
            "2. Coverage: a mass of 'Duplicate without user-selected canonical' or 'Crawled - "
            "currently not indexed' IS the confirmation. Its absence is a refutation.",
            "3. crawllog.py: if Googlebot re-crawls the tier at all, it has not written it off.",
            "Only when the index evidence AGREES with this measurement is consolidation or "
            "noindexing the right move. Acting on the ratio alone deletes pages Google was "
            "happy to rank.",
        ],
    }, indent=2, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("check", help="gate one draft against the published corpus")
    s.add_argument("--draft", required=True, help="path to the FINAL draft (post-humanizer), or -")
    s.add_argument("--corpus", required=True, help="directory of published guides (md/mdx/html)")
    s.add_argument("--keyword", default="", help="primary keyword - its tokens get stripped from headings")
    s.add_argument("--corpus-size", type=int, default=CORPUS_SIZE)
    s.add_argument("--pages", help=".seo/pages.json - maps each published slug to its own "
                                  "primary keyword, which is what gets stripped from ITS headings")
    s.add_argument("--exit-code", action="store_true", help="exit 3 on a fail (for CI)")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("audit", help="pairwise drift report across the whole corpus")
    s.add_argument("--corpus", required=True)
    s.add_argument("--corpus-size", type=int, default=60)
    s.add_argument("--pages", help=".seo/pages.json (per-page keyword stripping)")
    s.add_argument("--threshold", type=float, default=0.35)
    s.set_defaults(fn=cmd_audit)

    sub.add_parser("control", help="prove the extractor and the metric discriminate").set_defaults(fn=lambda a: print(json.dumps(run_control(), indent=2)))

    s = sub.add_parser("tiers", help="O(n) index-bloat analysis across a whole generated corpus")
    s.add_argument("--corpus", required=True, help="directory of generated pages")
    s.add_argument("--include", help="regex a path must match")
    s.add_argument("--exclude-re", help="regex a path must NOT match")
    s.add_argument("--limit", type=int, default=0, help="even-stride sample cap (0 = all)")
    s.add_argument("--rare-below", type=float, default=0.05,
                   help="a shingle in <= this share of docs counts as unique")
    s.add_argument("--hard", type=float, default=0.30, help="hard-stop unique ratio")
    s.add_argument("--warn", type=float, default=0.40, help="warning unique ratio")
    s.add_argument("--min-words", type=int, default=300)
    s.add_argument("--sig-size", type=int, default=8)
    s.add_argument("--top", type=int, default=15)
    s.set_defaults(fn=cmd_tiers)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
