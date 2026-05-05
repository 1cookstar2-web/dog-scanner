#!/usr/bin/env python3
"""Batch-audit every adapter: which ones are missing dogs that AI can find?

Flow:
  1. Read /tmp/adapter_urls.json (name, url, kind)
  2. Run live scanner.scan() once, bucket dogs by source
  3. For each adapter (skipping json_api):
     - Fetch the list page HTML
     - Ask Claude (Sonnet) to extract every dog
     - Diff AI results vs live adapter results
     - For "AI-found but live-missed" dogs, ask Haiku for a breed verdict
  4. Print a ranked table: adapters with the most AI-confirmed missed dogs

Output: /tmp/ai_audit_report.json with full per-adapter details.
"""
from __future__ import annotations

import concurrent.futures as fut
import json
import os
import sys
import time
from pathlib import Path

# Load env
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
# Sites known to 403 the generic Chrome UA — use a Safari UA for those.
SAFARI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}
SAFARI_UA_HOSTS = {
    "www.dogsblog.com", "www.wiccaweys.co.uk", "sprockerassist.org",
    "margaretgreenanimalrescue.org.uk", "www.teckelsanimalsanctuary.co.uk",
    "www.eglr.org.uk", "www.fostbc.org.uk", "fostbc.org.uk",
}

SKIP_KINDS = {"json_api"}   # HTML-less — nothing for AI to work with
REPORT_PATH = Path("/tmp/ai_audit_report.json")


def _norm_name(s: str) -> str:
    return (s or "").strip().lower()


def audit_one(entry: dict, live_by_source: dict[str, list]) -> dict:
    name = entry["name"]
    url = entry["url"]
    kind = entry["kind"]

    result = {
        "name": name, "url": url, "kind": kind,
        "http_status": None, "html_bytes": 0,
        "live_count": len(live_by_source.get(name, [])),
        "ai_count": 0,
        "ai_missed_by_live_total": 0,
        "ai_confirmed_missed": [],   # dogs AI extracted AND breed-checker says match=True
        "ai_rejected_missed": [],    # dogs AI extracted but breed-checker says match=False
        "live_missed_by_ai": [],     # dogs live had that AI didn't — paginated content etc.
        "error": None,
    }

    if kind in SKIP_KINDS:
        result["error"] = f"skipped kind={kind}"
        return result

    from urllib.parse import urlparse
    host = urlparse(url).netloc
    headers = SAFARI_HEADERS if host in SAFARI_UA_HOSTS else HEADERS

    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=20)
            result["http_status"] = r.status_code
            result["html_bytes"] = len(r.text)
            # 403 with Chrome? try Safari once
            if r.status_code == 403 and headers is HEADERS:
                r = requests.get(url, headers=SAFARI_HEADERS, timeout=20)
                result["http_status"] = r.status_code
                result["html_bytes"] = len(r.text)
            if r.status_code != 200 or len(r.text) < 500:
                result["error"] = f"bad response HTTP {r.status_code} size={len(r.text)}"
                return result
            break
        except Exception as e:
            last_err = e
            time.sleep(1)
    else:
        result["error"] = f"fetch error after retries: {type(last_err).__name__}: {last_err}"
        return result

    ai_dogs = ai_extract_dogs(r.text, name, url)
    result["ai_count"] = len(ai_dogs)

    live_names = {_norm_name(d.name) for d in live_by_source.get(name, [])}
    ai_names = {_norm_name(d.name) for d in ai_dogs}

    for d in ai_dogs:
        if _norm_name(d.name) in live_names:
            continue
        result["ai_missed_by_live_total"] += 1
        # Only breed-check if breed field is populated
        check_text = f"{d.name}. {d.breed}. {d.age}. {d.location}".strip(". ")
        if len(check_text) < 20:
            # Too little info to judge — put in rejected so we don't count it
            result["ai_rejected_missed"].append({"name": d.name, "breed": d.breed, "url": d.url, "reason": "insufficient info"})
            continue
        verdict = ai_breed_check(check_text)
        dog_row = {
            "name": d.name, "breed_ai_saw": d.breed, "url": d.url,
            "verdict_match": verdict.match, "verdict_breed": verdict.breed,
            "verdict_conf": verdict.confidence,
        }
        if verdict.match:
            result["ai_confirmed_missed"].append(dog_row)
        else:
            result["ai_rejected_missed"].append(dog_row)

    for d in live_by_source.get(name, []):
        if _norm_name(d.name) not in ai_names:
            result["live_missed_by_ai"].append({"name": d.name, "breed": d.breed, "url": d.url})

    return result


def main() -> int:
    url_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/adapter_urls.json"
    entries = json.loads(Path(url_file).read_text())
    print(f"Loaded {len(entries)} adapters from {url_file}", file=sys.stderr)

    print("Running live scanner.scan() once for cross-reference…", file=sys.stderr)
    t0 = time.time()
    live_dogs = scanner.scan()
    print(f"  live scan returned {len(live_dogs)} total dogs in {time.time()-t0:.1f}s", file=sys.stderr)

    live_by_source: dict[str, list] = {}
    for d in live_dogs:
        live_by_source.setdefault(d.source, []).append(d)

    # Parallel audit with modest fan-out to avoid both rate-limits and HTTP bans
    print(f"Starting parallel AI audit (max 6 concurrent)…", file=sys.stderr)
    t0 = time.time()
    results = []
    with fut.ThreadPoolExecutor(max_workers=6) as pool:
        fut_map = {pool.submit(audit_one, e, live_by_source): e["name"] for e in entries}
        for f in fut.as_completed(fut_map):
            nm = fut_map[f]
            try:
                r = f.result()
                results.append(r)
                print(
                    f"  [{nm}] live={r['live_count']} ai={r['ai_count']} "
                    f"confirmed_missed={len(r['ai_confirmed_missed'])} "
                    f"rejected={len(r['ai_rejected_missed'])}"
                    + (f" ERR={r['error']}" if r["error"] else ""),
                    file=sys.stderr,
                )
            except Exception as e:
                print(f"  [{nm}] AUDIT CRASHED: {e}", file=sys.stderr)
                results.append({"name": nm, "error": f"crash: {e}"})

    dur = time.time() - t0
    results.sort(key=lambda x: -len(x.get("ai_confirmed_missed", [])))
    REPORT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nReport written to {REPORT_PATH} ({dur:.0f}s total)", file=sys.stderr)

    # Print ranked summary
    print("\n=== ADAPTERS RANKED BY AI-CONFIRMED MISSED DOGS ===")
    shown = 0
    for r in results:
        n_miss = len(r.get("ai_confirmed_missed", []))
        if n_miss == 0:
            continue
        shown += 1
        print(f"\n[{r['name']}] live={r['live_count']} ai_total={r['ai_count']} "
              f"confirmed_missed={n_miss} rejected={len(r.get('ai_rejected_missed', []))}")
        for m in r["ai_confirmed_missed"]:
            print(f"  ✓ {m['name']!r} ({m['breed_ai_saw']!r}) — "
                  f"AI verdict: {m['verdict_breed']!r} conf={m['verdict_conf']}")
            print(f"    {m['url']}")
    if shown == 0:
        print("(no adapters have AI-confirmed missed dogs)")

    print("\n=== ADAPTERS WITH ERRORS ===")
    errs = [r for r in results if r.get("error")]
    if errs:
        for r in errs:
            print(f"  [{r['name']}] {r['error']}")
    else:
        print("(none)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
