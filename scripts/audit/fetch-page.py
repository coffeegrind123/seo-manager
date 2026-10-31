#!/usr/bin/env python3
"""
Fetch a web page for SEO analysis with proper headers, redirect tracking, and SSRF protection.

Usage:
    python fetch-page.py https://example.com
    python fetch-page.py https://example.com --output page.html
    python fetch-page.py https://example.com --googlebot
"""

import argparse
import ipaddress
import json
import socket
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_net  # noqa: E402 - stdlib fetches; see audit_net.py


# Default UA: simulates a real browser for standard page fetching
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 ClaudeSEO/1.2"
)

# Googlebot UA: used to detect dynamic rendering (Prerender.io / SSR).
# Comparing response body size between DEFAULT_USER_AGENT and GOOGLEBOT_USER_AGENT
# reveals whether a site uses a prerender service — a key SPA detection signal.
GOOGLEBOT_USER_AGENT = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)

DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


def fetch_page(
    url: str,
    timeout: int = 30,
    follow_redirects: bool = True,
    max_redirects: int = 5,
    user_agent: Optional[str] = None,
) -> dict:
    """
    Fetch a web page and return a structured result dict.

    Returns:
        {
            "url": final URL after redirects,
            "status_code": HTTP status code,
            "content": response body text,
            "headers": response headers dict,
            "redirect_chain": list of intermediate URLs,
            "redirect_details": list of {url, status_code} per hop,
            "error": error message string, or None on success
        }
    """
    result: dict = {
        "url": url,
        "status_code": None,
        "content": None,
        "headers": {},
        "redirect_chain": [],
        "redirect_details": [],
        "error": None,
    }

    # Prepend https:// if no scheme is present
    parsed = urlparse(url)
    if not parsed.scheme:
        url = f"https://{url}"
        parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        result["error"] = f"Invalid URL scheme: {parsed.scheme}"
        return result

    headers = {"User-Agent": user_agent} if user_agent else None
    r = audit_net.get(url, timeout, follow=follow_redirects, headers=headers,
                      max_redirects=max_redirects)
    if r.error:
        result["error"] = r.error
    else:
        result["url"] = r.url
        result["status_code"] = r.status_code
        result["content"] = r.text
        result["headers"] = dict(r.headers)
        if r.history:
            result["redirect_chain"] = [h["url"] for h in r.history]
            result["redirect_details"] = r.history

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a web page for SEO analysis")
    parser.add_argument("url", help="URL to fetch")
    parser.add_argument("--output", "-o", help="Save content to this file path")
    parser.add_argument("--timeout", "-t", type=int, default=30, help="Timeout in seconds")
    parser.add_argument("--no-redirects", action="store_true", help="Do not follow redirects")
    parser.add_argument("--user-agent", help="Custom User-Agent string")
    parser.add_argument(
        "--googlebot",
        action="store_true",
        help="Use Googlebot UA to detect dynamic rendering / prerender services",
    )
    parser.add_argument("--json", action="store_true", help="Output full result as JSON (excludes body)")

    args = parser.parse_args()

    ua: Optional[str] = args.user_agent
    if args.googlebot:
        ua = GOOGLEBOT_USER_AGENT

    result = fetch_page(
        args.url,
        timeout=args.timeout,
        follow_redirects=not args.no_redirects,
        user_agent=ua,
    )

    if result["error"]:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    # --json mode: output metadata only (no full HTML body) for consumption by other scripts
    if args.json:
        output = {k: v for k, v in result.items() if k != "content"}
        output["content_length"] = len(result["content"] or "")
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    # File output mode
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result["content"] or "")
        print(f"Saved to {args.output}")
    else:
        print(result["content"] or "")

    # Print metadata to stderr so it does not pollute stdout content
    print(f"\nURL: {result['url']}", file=sys.stderr)
    print(f"Status: {result['status_code']}", file=sys.stderr)
    if result["redirect_details"]:
        for rd in result["redirect_details"]:
            print(f"  {rd['status_code']} -> {rd['url']}", file=sys.stderr)
        print(f"  {result['status_code']} -> {result['url']} (final)", file=sys.stderr)


if __name__ == "__main__":
    main()
