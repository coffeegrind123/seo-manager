#!/usr/bin/env python3
"""Controls for keywords.py's band analysers.

Focused on `cmd_bing`, because its two departures from the GSC path are exactly
where a confident wrong answer comes from:

  1. Bing returns ONE ROW PER MARKET, so the same query string appears several
     times. Summing impressions is obvious; the position must be
     IMPRESSION-WEIGHTED or a 3-impression market moves the headline position as
     much as a 3,000-impression one.
  2. A single blended CTR hides a language segment earning most of the clicks.
     On the site this was built against, Chinese queries were 15% of queries and
     73% of clicks at 30.3% CTR against 2.5% for everything else.
"""
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import keywords as kw  # noqa: E402

FAILS = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILS.append(name)


def run_bing(rows, **flags):
    """Invoke cmd_bing over stdin and return the parsed payload."""
    payload = json.dumps({"queries": rows})
    argv = [sys.executable, os.path.join(HERE, "keywords.py"), "bing", "-"]
    for k, v in flags.items():
        argv.append("--" + k.replace("_", "-"))
        argv.extend(v if isinstance(v, list) else [str(v)])
    p = subprocess.run(argv, input=payload, capture_output=True, text=True)
    return json.loads(p.stdout)


print("script detection")
check("chinese -> cjk", kw.script_of("cs1.6网页版") == "cjk")
check("japanese kana -> kana", kw.script_of("カウンターストライク") == "kana")
check("korean -> hangul", kw.script_of("카운터스트라이크") == "hangul")
check("russian -> cyrillic", kw.script_of("контр страйк") == "cyrillic")
check("arabic -> arabic", kw.script_of("كاونتر سترايك") == "arabic")
check("plain english -> latin", kw.script_of("cs 1.6 online") == "latin")
check("mixed cjk+latin counts as cjk", kw.script_of("cs1.6 网页版") == "cjk")

print("\nper-market rows aggregate, position is impression-weighted")
d = run_bing([
    {"query": "cs 1.6", "impressions": 3000, "clicks": 60, "avg_position": 6.0},
    {"query": "cs 1.6", "impressions": 3, "clicks": 0, "avg_position": 60.0},
], min_impressions=1)
r = d["results"][0]
check("rows collapsed to one query", d["count"] == 1)
check("impressions summed", r["impressions"] == 3003)
check("clicks summed", r["clicks"] == 60)
# unweighted mean would be 33.0 and would band this as 'page3-5'
check("position impression-weighted, not a plain mean", 6.0 <= r["position"] <= 6.1)
check("so the band is page1, not page3-5", r["band"] == "page1")

print("\nbanding")
bands = run_bing([
    {"query": "a", "impressions": 100, "clicks": 1, "avg_position": 2},
    {"query": "b", "impressions": 100, "clicks": 1, "avg_position": 7},
    {"query": "c", "impressions": 100, "clicks": 1, "avg_position": 15},
    {"query": "d", "impressions": 100, "clicks": 1, "avg_position": 40},
    {"query": "e", "impressions": 100, "clicks": 1, "avg_position": 80},
], min_impressions=1)
got = {r["keyword"]: r["band"] for r in bands["results"]}
check("pos 2 -> top3", got["a"] == "top3")
check("pos 7 -> page1", got["b"] == "page1")
check("pos 15 -> striking-distance", got["c"] == "striking-distance")
check("pos 40 -> page3-5", got["d"] == "page3-5")
check("pos 80 -> deep", got["e"] == "deep")

print("\nby_script segmentation surfaces a hidden earner")
d = run_bing(
    [{"query": f"query {i}", "impressions": 1000, "clicks": 20, "avg_position": 6} for i in range(10)]
    + [{"query": f"网页版{i}", "impressions": 200, "clicks": 60, "avg_position": 2} for i in range(5)],
    min_impressions=1)
bs = d["by_script"]
check("both scripts reported", set(bs) == {"latin", "cjk"})
check("latin CTR ~2%", abs(bs["latin"]["ctr"] - 0.02) < 0.001)
check("cjk CTR ~30%", abs(bs["cjk"]["ctr"] - 0.30) < 0.001)
check("cjk is the minority of impressions", bs["cjk"]["impressions"] < bs["latin"]["impressions"])
check("yet a large share of clicks",
      bs["cjk"]["clicks"] / (bs["cjk"]["clicks"] + bs["latin"]["clicks"]) > 0.55)
check("--script filters", len(run_bing(
    [{"query": "x", "impressions": 100, "clicks": 1, "avg_position": 3},
     {"query": "网页版", "impressions": 100, "clicks": 1, "avg_position": 3}],
    min_impressions=1, script=["cjk"])["results"]) == 1)

print("\nctr_underperformer: a snippet problem, not a ranking problem")
d = run_bing([
    {"query": "ranks well, nobody clicks", "impressions": 2238, "clicks": 24, "avg_position": 8},
    {"query": "ranks well, clicked",       "impressions": 2238, "clicks": 700, "avg_position": 8},
    {"query": "buried so CTR is expected", "impressions": 2238, "clicks": 5,  "avg_position": 40},
    {"query": "too few impressions",       "impressions": 40,   "clicks": 0,  "avg_position": 5},
], min_impressions=1)
flag = {r["keyword"]: r["ctr_underperformer"] for r in d["results"]}
check("top-10 + real impressions + <2% CTR is flagged", flag["ranks well, nobody clicks"] is True)
check("a healthy CTR is not flagged", flag["ranks well, clicked"] is False)
check("a deep ranking is NOT a CTR problem", flag["buried so CTR is expected"] is False)
check("thin impressions are not judged", flag["too few impressions"] is False)

print("\ninput shapes and refusals")
p = subprocess.run([sys.executable, os.path.join(HERE, "keywords.py"), "bing", "-"],
                   input=json.dumps([{"query": "bare list", "impressions": 50,
                                      "clicks": 1, "avg_position": 4}]),
                   capture_output=True, text=True)
check("a bare list is accepted", json.loads(p.stdout).get("count") == 1)
p = subprocess.run([sys.executable, os.path.join(HERE, "keywords.py"), "bing", "-"],
                   input=json.dumps({"nope": 1}), capture_output=True, text=True)
check("a wrong shape is refused, not silently empty", json.loads(p.stdout).get("ok") is False)
d = run_bing([{"query": "no position", "impressions": 500, "clicks": 5}], min_impressions=1)
check("a missing position bands as unknown, never as 0", d["results"][0]["band"] == "unknown")
check("and reports position None rather than inventing one",
      d["results"][0]["position"] is None)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("all keywords tests passed")
