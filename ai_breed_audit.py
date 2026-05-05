#!/usr/bin/env python3
"""Prototype: find dogs the regex-based scanner is missing.

For every dog returned by a live scan(), we already caught it via regex. The
value is in dogs that *aren't* returned — either because their breed field is
ambiguous ("Crossbreed", "Mixed"), or because a site describes the breed only
in prose.

This script takes a source_name as argument, fetches that adapter's list page,
asks Claude to extract every dog it can find (ignoring breed), then runs the
AI breed checker over each one. Prints a diff vs. what the live adapter
returned.

Usage:
    python3 ai_breed_audit.py "Dogs Trust"
    python3 ai_breed_audit.py --list      # show configured sources
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_env = Path(__file__).resolve().parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

import requests

import scanner
from ai_helpers import ai_extract_dogs, ai_breed_check

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Minimal source URL map for the prototype. Extend as needed.
SOURCE_URLS = {
    "Dogs Trust": "https://www.dogstrust.org.uk/rehoming/dogs",
    "Battersea": "https://www.battersea.org.uk/dogs/dog-rehoming-gallery",
    "Many Tears": "https://www.manytearsrescue.org/adopt/dogs/",
    "Blue Cross": "https://www.bluecross.org.uk/rehome/dog",
    "Dogsblog": "https://www.dogsblog.com/category/border-collie/",
    "FOSTBC": "https://fostbc.org.uk/dogs-for-adoption/",
    "Last Chance": "https://lastchanceanimalrescue.co.uk/adopt/dogs/",
    "Wiccaweys": "https://www.wiccaweys.co.uk/dogs-for-rehoming/",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", help="Source name, e.g. 'Dogs Trust'")
    ap.add_argument("--list", action="store_true", help="Print configured sources")
    args = ap.parse_args()

    if args.list or not args.source:
        print("Configured sources:")
        for s in sorted(SOURCE_URLS):
            print(f"  - {s}")
        return 0

    if args.source not in SOURCE_URLS:
        print(f"Unknown source {args.source!r}. Use --list to see options.", file=sys.stderr)
        return 1

    url = SOURCE_URLS[args.source]
    print(f"→ Fetching {url}")
    r = requests.get(url, headers=HEADERS, timeout=25)
    if r.status_code != 200:
        print(f"HTTP {r.status_code} — bailing", file=sys.stderr)
        return 1

    print(f"→ Live scan() for cross-reference…")
    live_dogs = scanner.scan()
    live_by_source = {d.name.lower(): d for d in live_dogs if d.source == args.source}
    print(f"   live adapter returned {len(live_by_source)} dog(s) for {args.source}")

    print(f"→ Asking Claude (sonnet-4-6) to extract all dogs from HTML…")
    ai_dogs = ai_extract_dogs(r.text, args.source, url)
    print(f"   AI extracted {len(ai_dogs)} dog(s)")

    if not ai_dogs:
        print("No AI results — either API unavailable or page empty.")
        return 0

    print("\n=== Delta: dogs AI found that live adapter didn't ===")
    missed = []
    for d in ai_dogs:
        if d.name.lower() not in live_by_source:
            missed.append(d)

    if not missed:
        print("(none — adapter and AI agree)")
    else:
        for d in missed:
            # Re-check breed with Haiku to build confidence
            breed_text = f"{d.name} — {d.breed}"
            verdict = ai_breed_check(breed_text + " — " + (d.age or ""))
            marker = "✓" if verdict.match else "✗"
            print(f"  {marker} {d.name!r} ({d.breed!r}) — {d.url}")
            print(f"     AI breed verdict: {verdict.breed!r} match={verdict.match} conf={verdict.confidence}")

    print("\n=== Dogs live adapter had but AI missed ===")
    ai_names = {d.name.lower() for d in ai_dogs}
    for nm, d in live_by_source.items():
        if nm not in ai_names:
            print(f"  - {d.name} ({d.breed}) — {d.url}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
