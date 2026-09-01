#!/usr/bin/env python3
"""Regression tests for the content-brief assembler.

Every case is a bug this file shipped in its first hour, or a way it would state
somebody's assumptions back to them with a tool's authority.

    python3 test_brief.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from brief import (  # noqa: E402
    CONSTRAINTS, MIN_PAGES_FOR_SUBTOPICS, _section, build, corpus_overlap,
)

FAILURES: list[str] = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' - ' + str(detail)[:150]) if detail else ''}")
    if not cond:
        FAILURES.append(label)


def _contract(read: int) -> dict:
    return {"ok": True, "read": read, "contract": [{"subtopic": "recoil", "covered_by": 4}],
            "depth": {"median_words": 900}, "format": {},
            "gaps": [{"subtopic": "ratings", "covered_by": 1},
                     {"subtopic": "submission", "covered_by": 1}],
            "pages": [], "unread": []}


def main() -> int:
    print("it refuses rather than inventing the competitive read:")
    r = build("q", provider="not-a-provider", count=1)
    check("no readable page 1 refuses", r.get("control_failed") is True, str(r)[:120])
    check("no invented sections survive the refusal",
          not any(k in r for k in ("must_cover", "competition", "differentiators")),
          "a fabricated 'page 1 covers' produces a draft confidently wrong about "
          "the competition, and nobody re-opens the SERP")
    check("the refusal names the way forward", bool(r.get("what_to_do")))

    print("\nsubtopics are withheld when the sample cannot support them:")
    # Measured on "how to bunny hop in cs 1.6": 2 of 10 results readable, and the
    # gaps came back as `ratings, submission, score, favorite` - GameBanana and
    # mods.vg platform furniture read as subtopics. Printed as "differentiators"
    # that is an instruction to write a section about a ratings widget.
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "c.json"
        f.write_text(json.dumps(_contract(2)), encoding="utf-8")
        thin = build("q", contract_json=str(f))
        f.write_text(json.dumps(_contract(8)), encoding="utf-8")
        fat = build("q", contract_json=str(f))
    check("a 2-page contract withholds its gaps", thin["differentiators"] == [])
    check("the withheld list stays visible for review",
          "ratings" in thin["differentiators_withheld"]["raw_gaps"],
          "withholding must not mean hiding")
    check("an 8-page contract still reports them",
          [g["subtopic"] for g in fat["differentiators"]] == ["ratings", "submission"])
    check("the confidence verdict differs between the two",
          thin["subtopic_confidence"]["verdict"] != fat["subtopic_confidence"]["verdict"])
    check("the threshold is stated in the output",
          thin["subtopic_confidence"]["threshold"] == MIN_PAGES_FOR_SUBTOPICS)

    print("\ncannibalisation - a 'clear' from a metric that cannot fire is the")
    print("expensive failure: it green-lights a duplicate for a query we own:")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        # Headings that deliberately avoid the query words. The real
        # bunny-hop.html is written this way and scored 0.0 until the slug counted.
        (d / "bunny-hop.html").write_text(
            "<html><body><h2>What the engine is actually doing</h2>"
            "<p>Chaining jumps keeps speed above the run cap for a whole round.</p>"
            "</body></html>", encoding="utf-8")
        (d / "economy.html").write_text(
            "<html><body><h2>Round economy and buy timing</h2>"
            "<p>The loss bonus decides whether a full buy is affordable next round.</p>"
            "</body></html>", encoding="utf-8")
        hit = corpus_overlap("how to bunny hop", str(d))
        miss = corpus_overlap("zzq nonexistent topic 9f2b", str(d))
    check("the existing page is found", hit["verdict"] == "existing-page-may-already-target-this")
    check("good editorial headings do not hide a page",
          any("bunny-hop" in c["file"] for c in hit["candidates"]),
          "found via the slug, which is how the page is actually addressed")
    check("an unrelated query is clear", miss["verdict"] == "clear")
    check("the metric discriminates",
          hit["verdict"] != miss["verdict"],
          "jaccard scored BOTH at 0 - symmetric, so a 4-token query against a "
          "100-token page can never reach any threshold")
    check("the matching signal is reported",
          "slug" in hit["candidates"][0]["matched_in"],
          "a slug-only match is weaker than one the prose backs up, and the "
          "reader has to be able to tell")
    check("an unrelated sibling is not dragged in",
          not any("economy" in c["file"] for c in hit["candidates"]))

    print("\na failed contributing section is recorded, never fatal and never silent:")
    got, err = _section("boom", lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    check("the failure is captured", got is None and err["section"] == "boom")
    check("the reason survives", "RuntimeError" in err["reason"])

    print("\nthe house rules travel with the brief:")
    check("information gain is first", CONSTRAINTS[0].startswith("INFORMATION GAIN"))
    check("depth is stated as parity", any("PARITY" in c for c in CONSTRAINTS))
    check("fetched text is declared untrusted", any("UNTRUSTED" in c for c in CONSTRAINTS))
    check("the sameness and slop gates are named",
          any("sameness.py" in c for c in CONSTRAINTS) and any("slop.py" in c for c in CONSTRAINTS))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all brief tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
