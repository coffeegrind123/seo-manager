#!/usr/bin/env python3
"""Regression tests for IndexNow batching and the key-file preflight.

The bugs these were written against (2026-10-25):
  - `ping` sent every URL under ONE `host`. A batch mixing www and apex URLs
    is answered 422 ("URLs which don't belong to the host",
    indexnow.org/documentation), so a mixed sitemap failed whole.
  - nothing checked the key file before submitting, so a key not yet deployed
    (or redirected, or with the wrong body) was discovered as a 403 after the
    fact - per open-seo's IndexNowService key-file states.

    python3 test_indexnow.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import indexnow as I  # noqa: E402

FAILS: list[str] = []
KEY = "0123456789abcdef0123456789abcdef"


def check(name: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' ' + str(detail)[:200]}")
    if not cond:
        FAILS.append(name)


def net(table):
    def fetch(url):
        st, body, loc = table.get(url, (404, "", None))
        return {"status": st, "body": body, "location": loc}
    return fetch


print("one batch per host")
urls = ["https://x.test/a", "https://www.x.test/b", "https://x.test/c", "https://www.x.test/d"]
ok_all = net({f"https://x.test/{KEY}.txt": (200, KEY + "\n", None),
              f"https://www.x.test/{KEY}.txt": (200, KEY, None)})
b = I.plan_batches(urls, KEY, fetch=ok_all)
check("www and apex URLs go in separate batches",
      sorted((x["host"], tuple(x["urls"])) for x in b)
      == [("www.x.test", ("https://www.x.test/b", "https://www.x.test/d")),
          ("x.test", ("https://x.test/a", "https://x.test/c"))], b)
check("each batch names its own keyLocation",
      all(x["key_location"] == f"https://{x['host']}/{KEY}.txt" for x in b), b)
check("CONTROL: a found key file is submittable", all(x["keyfile"] == "found" for x in b), b)

print("\nthe key file is checked before anything is sent")
cases = {
    "missing": net({}),
    "wrong_content": net({f"https://x.test/{KEY}.txt": (200, "<html>home</html>", None)}),
    "redirects": net({f"https://x.test/{KEY}.txt": (301, "", f"https://www.x.test/{KEY}.txt")}),
    "unreachable": lambda u: {"status": None, "body": "", "location": None},
}
for want, fetch in cases.items():
    got = I.plan_batches(["https://x.test/a"], KEY, fetch=fetch)[0]["keyfile"]
    check(f"a key file that is {want} is named {want}", got == want, got)

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all indexnow tests passed")
