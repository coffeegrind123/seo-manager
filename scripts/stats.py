#!/usr/bin/env python3
"""stats.py - the small amount of statistics the measurements here need, exact.

Several instruments compare a RATE now with a rate before: AI-citation rate
(`geo.py`), CTR between two Search Console windows (`decay.py`), a remeasured
hypothesis (`remeasure.py`). Each used to call any movement a change. With
the sample sizes this skill actually sees - five questions, three runs, a page
with 40 impressions - most movements are noise, and a confident "cited rate
fell from 3/5 to 1/5" is a coin toss with a headline.

  wilson(x, n)                 95% score interval for one proportion
  newcombe_diff(x1,n1,x2,n2)   95% interval for p2 - p1 (Newcombe 1998, method 10)
  compare_rates(...)           a verdict that is "change" ONLY when that interval excludes 0
  mcnemar_exact(b, c)          paired before/after on the same items, exact binomial
  holm(pvalues)                step-down correction when several engines are tested
  stable_band(x, n, bands)     both ends of the interval must land in the same band
  small_sample(n)              the n below which a rate is labelled, not headlined

Every function returns None / "unknown" when the denominator is empty. An
empty denominator is the absence of a measurement, never a zero rate and never
"no detectable change" - the same rule as everywhere else in this skill.

The tests pin each function to a value published by the method's author
(test_stats.py), not to this file's own output.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls  # noqa: E402

Z95 = 1.959963984540054
# Below this, a rate is reported with its counts and labelled a small sample,
# never headlined. 20 is limelit-co/open's floor and the point where a Wilson
# interval for p=0.5 first narrows below +/-0.2.
SMALL_N = 20


def wilson(x: int, n: int, z: float = Z95) -> tuple[float | None, float | None]:
    if not n:
        return None, None
    p = x / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - half) / den), min(1.0, (centre + half) / den)


def newcombe_diff(x1: int, n1: int, x2: int, n2: int, z: float = Z95):
    """95% interval for p2 - p1 by the hybrid score method (Newcombe 1998,
    method 10). Coverage stays near nominal at small n and at 0 or n, where the
    Wald interval collapses to a point."""
    if not n1 or not n2:
        return None, None
    p1, p2 = x1 / n1, x2 / n2
    l1, u1 = wilson(x1, n1, z)
    l2, u2 = wilson(x2, n2, z)
    d = p2 - p1
    lo = d - math.sqrt((p2 - l2) ** 2 + (u1 - p1) ** 2)
    hi = d + math.sqrt((u2 - p2) ** 2 + (p1 - l1) ** 2)
    return lo, hi


def compare_rates(x1: int, n1: int, x2: int, n2: int) -> dict:
    before = {"x": x1, "n": n1, "rate": (x1 / n1) if n1 else None}
    after = {"x": x2, "n": n2, "rate": (x2 / n2) if n2 else None}
    lo, hi = newcombe_diff(x1, n1, x2, n2)
    if lo is None:
        return {"verdict": "unknown", "reason": "an empty denominator is no measurement",
                "before": before, "after": after, "point": None, "ci95": None}
    verdict = "increase" if lo > 0 else "decrease" if hi < 0 else "no_detectable_change"
    out = {"verdict": verdict, "point": after["rate"] - before["rate"],
           "ci95": [round(lo, 4), round(hi, 4)], "before": before, "after": after,
           "denominator_changed": n1 != n2}
    if small_sample(n1) or small_sample(n2):
        out["small_sample"] = True
    return out


def _binom_cdf_half(k: int, n: int) -> float:
    return sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar: b and c are the discordant pairs (yes->no,
    no->yes) on the SAME items. With 5 discordant pairs all one way p is
    0.0625 - unanimous and still not significant, which is the point."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * _binom_cdf_half(min(b, c), n))


def holm(pvalues: list[float]) -> list[float]:
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adj, running = [0.0] * m, 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adj[i] = running
    return adj


def small_sample(n: int) -> bool:
    return n < SMALL_N


def _band(v: float, bands) -> str:
    for floor, name in bands:
        if v >= floor:
            return name
    return bands[-1][1]


def stable_band(x: int, n: int, bands) -> dict:
    """`bands` is [(floor, name), ...] highest floor first. A label is STABLE
    only when the interval's low and high ends land in the same band - '3 of 3
    runs cited' has a Wilson low of 0.44 and does not stably mean 'strong'."""
    lo, hi = wilson(x, n)
    if lo is None:
        return {"band": None, "stable": False, "rate": None, "n": n, "ci": None,
                "low_band": None, "high_band": None}
    lb, hb = _band(lo, bands), _band(hi, bands)
    return {"band": _band(x / n, bands), "stable": lb == hb, "low_band": lb, "high_band": hb,
            "rate": x / n, "n": n, "ci": [round(lo, 4), round(hi, 4)]}


def run_control() -> dict:
    """Independent values only (see test_stats.py for the sources)."""
    c = Controls("stats-control")
    lo, hi = wilson(81, 263)
    c.check("wilson_matches_newcombe_table_I", abs(lo - 0.2553) < 5e-4 and abs(hi - 0.3662) < 5e-4)
    lo, hi = newcombe_diff(48, 80, 56, 70)      # paper's p1 - p2 = after - before here
    c.check("newcombe_method_10_matches_the_paper", abs(lo - 0.0524) < 5e-4 and abs(hi - 0.3339) < 5e-4)
    c.check("mcnemar_exact_matches_the_binomial", abs(mcnemar_exact(1, 9) - 22 / 1024) < 1e-12)
    c.check("holm_matches_holm_1979", holm([0.01, 0.04, 0.03]) == [0.03, 0.06, 0.06])
    c.check("an_empty_denominator_is_unknown", compare_rates(1, 0, 1, 5)["verdict"] == "unknown")
    c.check("CONTROL_a_real_difference_is_detected", compare_rates(5, 100, 40, 100)["verdict"] == "increase")
    c.check("CONTROL_noise_is_not_a_change", compare_rates(3, 5, 1, 5)["verdict"] == "no_detectable_change")
    return c.verdict()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("compare", help="two rates -> a verdict with its interval")
    for k in ("x1", "n1", "x2", "n2"):
        p.add_argument(k, type=int)
    p = sub.add_parser("wilson")
    p.add_argument("x", type=int)
    p.add_argument("n", type=int)
    p = sub.add_parser("mcnemar")
    p.add_argument("b", type=int)
    p.add_argument("c", type=int)
    sub.add_parser("control")
    a = ap.parse_args()
    if a.cmd == "compare":
        out = {"ok": True, **compare_rates(a.x1, a.n1, a.x2, a.n2)}
    elif a.cmd == "wilson":
        out = {"ok": True, "ci95": wilson(a.x, a.n), "small_sample": small_sample(a.n)}
    elif a.cmd == "mcnemar":
        out = {"ok": True, "p": mcnemar_exact(a.b, a.c)}
    else:
        out = run_control()
    print(json.dumps(out, indent=2))
    sys.exit(0 if out.get("ok") else 1)


if __name__ == "__main__":
    main()
