#!/usr/bin/env python3
"""directives.py - robots directives (meta robots + X-Robots-Tag), read the way
Google reads them. One parser, shared by sitecheck.py and contract.py, which
each had their own and each got a different part wrong.

Rules, from developers.google.com/search/docs/crawling-indexing/robots-meta-tag:
  - directives are comma-separated, case-insensitive tokens
  - `none` == `noindex, nofollow`; `max-image-preview:none` is NOT `none`
  - `<meta name="robots">` binds every crawler, `<meta name="googlebot">` binds
    Google only
  - an X-Robots-Tag rule with no crawler name binds every crawler; one written
    `otherbot: noindex, nofollow` binds `otherbot` only, for the rest of that
    header line

    X-Robots-Tag: noindex                      -> everyone: noindex
    X-Robots-Tag: otherbot: noindex, nofollow  -> otherbot: noindex, nofollow
    X-Robots-Tag: unavailable_after: 25 Jun... -> everyone (a value, not a name)

    python3 directives.py control
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls  # noqa: E402

# Directives that carry a `name: value`, so their colon is not a crawler prefix.
VALUE_DIRECTIVES = ("max-snippet", "max-image-preview", "max-video-preview",
                    "unavailable_after")

_SCOPED = re.compile(r"\s*([a-z0-9_.-]+)\s*:\s*(.*)$", re.S)


def _token(raw: str) -> str | None:
    t = re.sub(r"\s+", " ", raw.strip().lower())
    if not t:
        return None
    m = _SCOPED.match(t)
    if m and m.group(1) in VALUE_DIRECTIVES:
        return f"{m.group(1)}:{m.group(2).strip()}" if m.group(1) != "unavailable_after" \
            else f"unavailable_after: {m.group(2).strip()}"
    return t


def _content(value: str | None) -> list[str]:
    return [t for t in (_token(p) for p in (value or "").split(",")) if t]


def parse_header(value: str | None) -> list[tuple[str | None, str]]:
    """X-Robots-Tag -> [(crawler or None, directive)]. Repeated header lines
    arrive newline-joined (providers.header_dict), and a crawler prefix
    scopes only the rest of its own line."""
    out = []
    for line in (value or "").splitlines():
        agent = None
        for part in line.split(","):
            m = _SCOPED.match(part.strip().lower())
            if m and m.group(1) not in VALUE_DIRECTIVES:
                agent, part = m.group(1), m.group(2)
            t = _token(part)
            if t:
                out.append((agent, t))
    return out


def applicable(meta: dict | None = None, header: str | None = None,
               agent: str = "googlebot") -> set[str]:
    """Every directive that binds `agent`, with `none` expanded."""
    agent = agent.lower()
    meta = {k.lower(): v for k, v in (meta or {}).items() if v}
    got = _content(meta.get("robots")) + _content(meta.get(agent))
    got += [t for a, t in parse_header(header) if a is None or a == agent]
    out = set(got)
    if "none" in out:
        out |= {"noindex", "nofollow"}
    return out


def noindex(meta: dict | None = None, header: str | None = None,
            agent: str = "googlebot") -> bool:
    return "noindex" in applicable(meta, header, agent)


def run_control() -> dict:
    """Both directions on every rule: a parser that never says noindex passes
    the negatives, and one that always says it passes the positives."""
    c = Controls("directives-control")
    c.check("CONTROL_an_unscoped_header_noindex_is_found", noindex(header="noindex"))
    c.check("CONTROL_meta_none_is_found", noindex(meta={"robots": "follow,none"}))
    c.check("CONTROL_meta_googlebot_is_found", noindex(meta={"googlebot": "noindex"}))
    c.check("another_crawlers_header_does_not_bind_google",
            not noindex(header="otherbot: noindex, nofollow"))
    c.check("max_image_preview_none_is_not_none",
            not noindex(meta={"robots": "max-image-preview:none"}))
    c.check("a_scope_ends_with_its_line", noindex(header="otherbot: nofollow\nnoindex"))
    return c.verdict()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("control", help="offline: both directions of every rule")
    ap.parse_args()
    out = run_control()
    print(json.dumps(out, indent=2))
    sys.exit(0 if out.get("ok") else 1)


if __name__ == "__main__":
    main()
