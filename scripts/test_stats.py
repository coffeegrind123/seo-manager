#!/usr/bin/env python3
"""Regression tests for stats.py, pinned to PUBLISHED values.

Every expected number below comes from a source independent of the code - a
textbook table or the paper that defines the method - never from running the
implementation and copying its output (a control that agrees with the code
proves nothing; SKILL.md).

    python3 test_stats.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stats as ST  # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{(' ' + str(detail)) if detail and not cond else ''}")
    if not cond:
        FAILS.append(name)


def near(a, b, tol=5e-4):
    return a is not None and abs(a - b) <= tol


print("Wilson score interval")
# Newcombe (1998) Stat Med 17:857, Table I: 81/263 -> 0.2553 to 0.3662 (method 3).
lo, hi = ST.wilson(81, 263)
check("81/263 matches Newcombe 1998 Table I (0.2553, 0.3662)", near(lo, 0.2553) and near(hi, 0.3662), (lo, hi))
# Same table: 15/148 -> 0.0624 to 0.1605
lo, hi = ST.wilson(15, 148)
check("15/148 matches Newcombe 1998 Table I (0.0624, 0.1605)", near(lo, 0.0624) and near(hi, 0.1605), (lo, hi))
lo, hi = ST.wilson(0, 20)
check("0/20 has a lower bound of exactly 0 and a non-zero upper", lo == 0.0 and near(hi, 0.1611), (lo, hi))
check("n = 0 is unknown, never (0, 0)", ST.wilson(0, 0) == (None, None))

print("\nNewcombe hybrid score interval for a difference of proportions")
# Newcombe (1998) Stat Med 17:873, example (a): 56/70 - 48/80, method 10 -> 0.0524 to 0.3339.
# The paper reports p1 - p2; newcombe_diff(before, after) is after - before, so
# the paper's p1 is passed as AFTER.
lo, hi = ST.newcombe_diff(48, 80, 56, 70)
check("56/70 - 48/80 matches Newcombe 1998 method 10 (0.0524, 0.3339)",
      near(lo, 0.0524) and near(hi, 0.3339), (lo, hi))
# Example (c): 5/56 - 0/29 -> -0.0381 to 0.1926
lo, hi = ST.newcombe_diff(0, 29, 5, 56)
check("5/56 - 0/29 matches Newcombe 1998 method 10 (-0.0381, 0.1926)",
      near(lo, -0.0381) and near(hi, 0.1926), (lo, hi))

print("\nchange verdicts")
v = ST.compare_rates(48, 80, 56, 70)
check("an interval excluding 0 is a change", v["verdict"] == "increase", v)
v = ST.compare_rates(0, 29, 5, 56)
check("an interval spanning 0 is NOT a change, whatever the point estimate says",
      v["verdict"] == "no_detectable_change" and v["point"] > 0, v)
v = ST.compare_rates(3, 0, 2, 10)
check("an empty denominator is unknown, never 'no change'", v["verdict"] == "unknown", v)
check("the counts travel with the verdict", v["before"] == {"x": 3, "n": 0} or v["before"]["n"] == 0)

print("\nexact McNemar (paired before/after on the same items)")
# b=1, c=9: two-sided exact p = 2 * P(X<=1 | n=10, p=.5) = 2 * 11/1024 = 0.021484
check("b=1 c=9 -> p = 22/1024", near(ST.mcnemar_exact(1, 9), 22 / 1024, 1e-9), ST.mcnemar_exact(1, 9))
# 5 discordant pairs all one way: p = 2/32 = 0.0625 - unanimous and still not significant.
check("5 unanimous flips -> p = 0.0625 (cannot reach .05)", near(ST.mcnemar_exact(0, 5), 0.0625, 1e-9))
check("no discordant pairs -> p = 1", ST.mcnemar_exact(0, 0) == 1.0)

print("\nHolm step-down")
# Holm (1979): m=3, p = .01 .04 .03 -> adjusted .03 .06 .06
adj = ST.holm([0.01, 0.04, 0.03])
check("Holm adjusted values (.03, .06, .06), in input order",
      all(near(a, b, 1e-12) for a, b in zip(adj, [0.03, 0.06, 0.06])), adj)
check("Holm never exceeds 1", max(ST.holm([0.6, 0.7])) <= 1.0)

print("\nstable verdict - both interval ends must name the same band")
bands = [(0.5, "strong"), (0.0001, "cited"), (0.0, "invisible")]
check("3/3 with Wilson low ~0.44 is NOT stably 'strong'",
      ST.stable_band(3, 3, bands)["stable"] is False, ST.stable_band(3, 3, bands))
check("40/50 IS stably 'strong'", ST.stable_band(40, 50, bands) == {
    "band": "strong", "stable": True, "low_band": "strong", "high_band": "strong",
    "rate": 0.8, "n": 50, "ci": ST.stable_band(40, 50, bands)["ci"]})
check("n under the small-sample floor is labelled", ST.small_sample(19) and not ST.small_sample(20))

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all stats tests passed")
