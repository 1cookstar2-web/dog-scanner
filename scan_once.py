#!/usr/bin/env python3
"""One-shot scan entry point used by GitHub Actions.

Replicates the body of app.py's _watcher_loop() without the infinite loop
and without the Gradio UI. Reads/writes the same state files so behaviour
matches the VPS service exactly.

Usage:
    python scan_once.py            # live mode: sends Telegram, updates state
    python scan_once.py --dry-run  # computes new dogs but suppresses Telegram

Exit codes:
    0  scan completed (with or without new dogs)
    1  scan failed (exception in scan() or unrecoverable error)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from scanner import scan

STATE_DIR = Path(__file__).parent / "state"
KNOWN_FILE = STATE_DIR / "known_dogs.json"
PREV_SCAN_FILE = STATE_DIR / "prev_scan_urls.json"
FIRST_SEEN_FILE = STATE_DIR / "first_seen.json"
HEARTBEAT_FILE = STATE_DIR / "watcher_heartbeat.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL_SECS", "600"))


def _load_json(path: Path, default):
    try:
        with path.open() as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2, sort_keys=False)
    tmp.replace(path)


def _send_telegram(new_dogs) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[telegram] TELEGRAM_TOKEN or TELEGRAM_CHAT_ID unset — skipping send")
        return
    lines = [f"🐕 {len(new_dogs)} new matching dog(s) found!\n"]
    for d in new_dogs:
        dist = f"{d.distance_miles:g} mi" if d.distance_miles is not None else "dist unknown"
        lines.append(f"• {d.name} ({d.breed}) — {dist} — {d.source}")
        lines.append(f"  {d.url}")
    payload = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "\n".join(lines),
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data=payload,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Telegram returned {resp.status}")


def _update_first_seen(new_urls) -> None:
    if not new_urls:
        return
    existing = _load_json(FIRST_SEEN_FILE, {}) or {}
    now = datetime.now(timezone.utc).isoformat()
    for url in new_urls:
        existing.setdefault(url, now)
    _save_json_atomic(FIRST_SEEN_FILE, existing)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute new-dog list and update state but DO NOT send Telegram",
    )
    args = parser.parse_args()

    try:
        dogs = scan()
    except Exception as e:
        print(f"[error] scan() raised: {e}", file=sys.stderr)
        return 1

    current_urls = {d.url for d in dogs}
    print(f"[scan] returned {len(dogs)} dog(s), {len(current_urls)} unique URL(s)")

    prev_scan_urls = set(_load_json(PREV_SCAN_FILE, []) or [])
    known_pre = set(_load_json(KNOWN_FILE, []) or [])
    if prev_scan_urls:
        ui_new_urls = (current_urls - prev_scan_urls) - known_pre
    else:
        ui_new_urls = set()
    _save_json_atomic(PREV_SCAN_FILE, sorted(current_urls))
    if ui_new_urls:
        _update_first_seen(ui_new_urls)
        print(f"[new] {len(ui_new_urls)} URL(s) first seen this cycle")

    known = set(_load_json(KNOWN_FILE, []) or [])
    if not known:
        _save_json_atomic(KNOWN_FILE, sorted(current_urls))
        print(f"[alerts] seeded known-dog state with {len(current_urls)} URLs (no alerts sent)")
        new = []
    else:
        new = [d for d in dogs if d.url not in known]
        if new:
            _save_json_atomic(KNOWN_FILE, sorted(known | current_urls))

    if new:
        print(f"[alerts] {len(new)} new dog(s) detected")
        if args.dry_run:
            print("[alerts] DRY-RUN — Telegram suppressed; would have sent:")
            for d in new:
                print(f"  - {d.name} ({d.breed}) - {d.url}")
        else:
            try:
                _send_telegram(new)
                print(f"[alerts] telegram sent ({len(new)} dog(s))")
            except Exception as e:
                # Don't fail the run — state is already updated; retrying would
                # produce duplicates on next cycle.
                print(f"[alerts] telegram failed: {e}", file=sys.stderr)
    elif known:
        print("[alerts] no new dogs this cycle")

    _save_json_atomic(HEARTBEAT_FILE, {
        "last_cycle_utc": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "dogs_in_scan": len(dogs),
        "new_urls": len(ui_new_urls),
        "interval_secs": SCAN_INTERVAL,
        "source": "github-actions" if os.environ.get("GITHUB_ACTIONS") else "cli",
    })

    return 0


if __name__ == "__main__":
    sys.exit(main())
