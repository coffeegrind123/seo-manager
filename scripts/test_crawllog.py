#!/usr/bin/env python3
"""Controls for crawllog's two newest rules.

Same discipline as test_guards / test_hreflang: every rule is fired against
synthetic input, so a clean pass on a real log means something. Both rules here
exist because the honest answer and the broken answer looked identical:

  * detect_ua_spoofing - a forged user-agent inflates the ai_search / ai_user
    numbers, which is exactly the metric people read most eagerly right now.
  * expand_inputs      - a run with no --glob used to read stdin, find nothing,
    and report `0 referring domains` as though it had measured the site.

Run: python3 test_crawllog.py
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("crawllog", os.path.join(HERE, "crawllog.py"))
crawllog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crawllog)

failures = []


def check(name, got, want):
    if got != want:
        failures.append(f"{name}: got {got!r}, want {want!r}")


def bot(key, ips):
    return {"key": key, "bot": key, "category": "ai_training", "_all_ips": ips}


# --- detect_ua_spoofing -------------------------------------------------------

# 1. The real-world shape: one address claiming several companies' crawlers.
r = crawllog.detect_ua_spoofing([
    bot("claudebot", {"1.2.3.4": 30}),
    bot("gptbot", {"1.2.3.4": 20}),
    bot("perplexitybot", {"1.2.3.4": 10}),
])
check("spoof/flags the shared ip", r["flagged_ip_count"], 1)
check("spoof/counts every hit", r["spoofed_hits"], 60)
check("spoof/names all operators", r["flagged_ips"][0]["operators_claimed"],
      ["Anthropic", "OpenAI", "Perplexity"])

# 2. THE CONTROL. One operator legitimately runs many crawlers from one address;
#    flagging Google here would make the detector useless on every real log.
r = crawllog.detect_ua_spoofing([
    bot("googlebot", {"192.178.6.102": 500}),
    bot("googlebot-image", {"192.178.6.102": 200}),
    bot("googleother", {"192.178.6.102": 100}),
    bot("google-extended", {"192.178.6.102": 10}),
])
check("spoof/CONTROL one operator many bots is clean", r["flagged_ip_count"], 0)
check("spoof/CONTROL reports no forged hits", r["spoofed_hits"], 0)

# 3. Distinct IPs per operator is the normal world - never flag it.
r = crawllog.detect_ua_spoofing([
    bot("gptbot", {"5.5.5.5": 100}),
    bot("claudebot", {"6.6.6.6": 100}),
])
check("spoof/separate ips are clean", r["flagged_ip_count"], 0)

# 4. Must see PAST a top-5 truncation: a scanner spreading over many addresses
#    is precisely what a truncated view would hide.
many = {f"10.0.0.{i}": 1 for i in range(1, 20)}
r = crawllog.detect_ua_spoofing([bot("gptbot", many), bot("claudebot", many)])
check("spoof/sees every ip not just top-5", r["flagged_ip_count"], 19)

# 5. Unknown/generic user-agents prove nothing and must not be grouped.
r = crawllog.detect_ua_spoofing([
    bot("other-bot", {"7.7.7.7": 50}),
    bot("some-random-crawler", {"7.7.7.7": 50}),
])
check("spoof/unmapped uas are not evidence", r["flagged_ip_count"], 0)

# 6. An empty log must not crash, and must NOT claim a clean bill of health.
r = crawllog.detect_ua_spoofing([])
check("spoof/empty input is clean", r["flagged_ip_count"], 0)
if "CEILING" not in r["reading"]:
    failures.append("spoof/empty reading must state it is a ceiling, not a clean bill of health")

# 7. operator_of must not let a longer key fall through to a shorter one.
check("operator/google-extended", crawllog.operator_of("google-extended"), "Google")
check("operator/claude-searchbot", crawllog.operator_of("claude-searchbot"), "Anthropic")
check("operator/oai-searchbot", crawllog.operator_of("oai-searchbot"), "OpenAI")
check("operator/unknown stays unknown", crawllog.operator_of("nonesuch-bot"), None)


# --- expand_inputs / the no-input refusal -------------------------------------

class A:
    file = None
    glob = None
    remote = None


# A glob matching nothing must NOT silently become stdin.
a = A()
a.glob = ["/definitely/not/a/path/*.log"]
check("inputs/unmatched glob resolves to nothing", crawllog.expand_inputs(a), [])
err = crawllog.no_input_error(a)
check("inputs/refusal is not ok", err["ok"], False)
if "no verdict" not in err["error"]:
    failures.append("inputs/refusal must say no verdict is available")

# A real file still resolves normally.
with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
    fh.write('{"status":200}\n')
    tmp = fh.name
a = A()
a.file = [tmp]
check("inputs/CONTROL a real file resolves", crawllog.expand_inputs(a), [tmp])
os.unlink(tmp)

# End to end: the CLI must exit non-zero and print a refusal, not zeros.
p = subprocess.run([sys.executable, os.path.join(HERE, "crawllog.py"), "scan",
                    "--glob", "/definitely/not/a/path/*.log"],
                   capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
check("cli/refusal exits non-zero", p.returncode != 0, True)
try:
    payload = json.loads(p.stdout)
    check("cli/refusal payload is not ok", payload.get("ok"), False)
except Exception as exc:
    failures.append(f"cli/refusal must print JSON: {exc} :: {p.stdout[:200]}")


if failures:
    print("FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all crawllog tests passed")
