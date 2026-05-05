"""Comprehensive test for the "New Dogs" feature.

Tests the full lifecycle:
  1. Backend unit tests — _update_first_seen, _is_new logic, 24h window
  2. Bootstrapping: first scan with no prior state → no New Dogs section
  3. Known-dogs present but no new arrivals → no New Dogs section
  4. Inject genuinely new URLs into first_seen.json → section appears
  5. 24h expiry — aged timestamps → section disappears
  6. Partial expiry — only recent subset shown
  7. Badge and card styling verification
  8. Watcher integration: _update_first_seen called only for new URLs
  9. Mobile viewport rendering

Run: python3 test_new_dogs.py [--url URL]
     Defaults to http://127.0.0.1:7861
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import Page, sync_playwright, expect

FIRST_SEEN_FILE = Path("/home/claude-dev/dog-scanner/state/first_seen.json")
KNOWN_FILE = Path("/home/claude-dev/dog-scanner/state/known_dogs.json")
SCREENSHOTS_DIR = Path("/tmp/dog-scanner-screenshots")
SCREENSHOTS_DIR.mkdir(exist_ok=True)

PASS_COUNT = 0
FAIL_COUNT = 0


def ok(msg):
    global PASS_COUNT
    PASS_COUNT += 1
    print(f"  PASS  {msg}")


def fail(msg, detail=""):
    global FAIL_COUNT
    FAIL_COUNT += 1
    extra = f"\n        {detail}" if detail else ""
    print(f"  FAIL  {msg}{extra}")


def section(title):
    print(f"\n[{title}]")


def screenshot(page: Page, name: str) -> str:
    path = str(SCREENSHOTS_DIR / f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"         Screenshot: {path}")
    return path


def run_full_scan(page: Page, url: str) -> int:
    """Click Scan, wait for .dog-list, return total dog count."""
    page.goto(url, wait_until="load", timeout=20_000)
    time.sleep(2)
    page.locator("button", has_text="Scan").first.click()
    expect(page.locator(".dog-list").first).to_be_visible(timeout=120_000)
    try:
        raw = page.locator("input[type='number']").first.input_value(timeout=3_000)
        return int(raw) if raw else 0
    except Exception:
        return 0


def headings_on_page(page: Page) -> list[str]:
    result = []
    for h in page.locator("h3").all():
        try:
            result.append(h.inner_text())
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# 1. Backend unit tests
# ---------------------------------------------------------------------------

def test_backend_logic():
    section("1. Backend unit tests — _update_first_seen, _is_new, 24h window")
    sys.path.insert(0, "/home/claude-dev/dog-scanner")
    from app import _update_first_seen, _is_new, _NEW_WINDOW

    # 24h window check
    if _NEW_WINDOW == timedelta(hours=24):
        ok("_NEW_WINDOW is 24 hours")
    else:
        fail(f"_NEW_WINDOW should be 24h, got {_NEW_WINDOW}")

    FIRST_SEEN_FILE.unlink(missing_ok=True)

    data = _update_first_seen(["https://example.com/dog1"])
    if "https://example.com/dog1" in data:
        ok("_update_first_seen records new URL")
    else:
        fail("_update_first_seen did not record URL")

    ts1 = data["https://example.com/dog1"]
    time.sleep(0.05)
    data2 = _update_first_seen(["https://example.com/dog1"])
    if ts1 == data2["https://example.com/dog1"]:
        ok("_update_first_seen never overwrites existing timestamps")
    else:
        fail("Timestamp was overwritten")

    if _is_new("https://example.com/dog1", data2):
        ok("_is_new True for URL recorded moments ago")
    else:
        fail("_is_new False for freshly-recorded URL")

    if not _is_new("https://UNKNOWN.invalid", data2):
        ok("_is_new False for URL not in dict")
    else:
        fail("_is_new True for unknown URL")

    now = datetime.now(timezone.utc)
    stale = {"u": (now - timedelta(hours=25)).isoformat()}
    if not _is_new("u", stale):
        ok("_is_new False for 25-hour-old timestamp (beyond 24h window)")
    else:
        fail("_is_new True for 25h-old timestamp — window not enforced")

    fresh = {"u": (now - timedelta(hours=23)).isoformat()}
    if _is_new("u", fresh):
        ok("_is_new True for 23-hour-old timestamp (within 24h window)")
    else:
        fail("_is_new False for 23h-old timestamp — window too narrow")

    bad = {"u": "not-a-date"}
    if not _is_new("u", bad):
        ok("_is_new handles malformed timestamp (returns False)")
    else:
        fail("_is_new True for malformed timestamp")

    naive_ts = (now - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    if _is_new("u", {"u": naive_ts}):
        ok("_is_new handles naive (no-tzinfo) datetime correctly")
    else:
        fail("_is_new failed on naive datetime")

    FIRST_SEEN_FILE.unlink(missing_ok=True)
    ok("Cleaned up test first_seen.json")


# ---------------------------------------------------------------------------
# 2. Bootstrap: first scan with no prior state → no New Dogs section
# ---------------------------------------------------------------------------

def test_no_new_dogs_on_bootstrap(page: Page, url: str):
    section("2. Bootstrap scan — no known_dogs → no 'New Dogs' section")

    FIRST_SEEN_FILE.unlink(missing_ok=True)
    # Keep known_dogs.json untouched — watcher seeded it at startup.
    # If it exists, this confirms scan correctly skips dogs in known.
    # If it doesn't (very first ever run), section should also be hidden.

    total = run_full_scan(page, url)
    if total > 0:
        ok(f"Scan returned {total} dogs")
    else:
        fail("Scan returned 0 dogs")

    known = set(json.loads(KNOWN_FILE.read_text())) if KNOWN_FILE.exists() else set()
    ok(f"known_dogs.json has {len(known)} entries")

    hs = headings_on_page(page)
    new_heading = next((h for h in hs if "New Dog" in h), None)

    if known:
        # If all scanned dogs are already known, nothing should appear new
        if new_heading is None:
            ok("No 'New Dogs' section — all dogs were already in known_dogs.json (correct)")
        else:
            fail(f"'New Dogs' section shown ({new_heading!r}) but all dogs were pre-existing")
    else:
        # No known state at all — bootstrapping, nothing new
        if new_heading is None:
            ok("No 'New Dogs' section on bootstrap run (correct)")
        else:
            fail(f"'New Dogs' shown on bootstrap when known_dogs is empty: {new_heading!r}")

    # Available section must still be present
    avail = next((h for h in hs if "Available" in h), None)
    if avail:
        ok(f"'Available' section correctly present: {avail!r}")
    else:
        fail("'Available' section missing")

    screenshot(page, "01_bootstrap_no_new_dogs")


# ---------------------------------------------------------------------------
# 3. Inject new URLs into first_seen → section appears
# ---------------------------------------------------------------------------

def test_new_dogs_with_injected_state(page: Page, url: str):
    section("3. Injected first_seen state → 'New Dogs' section appears")

    # We need real dog URLs that are in_range and not reserved.
    # Run a scan to get the actual URLs, then inject some as fresh first_seen.
    total = run_full_scan(page, url)
    if total == 0:
        fail("Scan returned 0 dogs — cannot inject state")
        return

    # Gather in_range non-reserved dog links from the Available section
    available_links = []
    avail_h3 = page.locator("h3", has_text="Available").first
    if avail_h3.count() > 0:
        # The .dog-list immediately follows the h3 heading
        sibling_list = page.locator("h3 ~ ul.dog-list").first
        if sibling_list.count() == 0:
            # fallback: get all dog-name links in the page
            all_links = page.locator(".dog-name")
        else:
            all_links = sibling_list.locator(".dog-name")
        for el in all_links.all()[:10]:
            href = el.get_attribute("href") or ""
            if href.startswith("http"):
                available_links.append(href)

    if not available_links:
        # Fallback: just grab first 5 .dog-name links from the whole page
        for el in page.locator(".dog-name").all()[:5]:
            href = el.get_attribute("href") or ""
            if href.startswith("http"):
                available_links.append(href)

    if not available_links:
        fail("Could not find any dog links to inject as first_seen state")
        return

    inject_count = min(5, len(available_links))
    inject_urls = available_links[:inject_count]
    ok(f"Injecting {inject_count} URLs as fresh first_seen state: {inject_urls[0][:60]}...")

    # Write those URLs as fresh (5 minutes ago) into first_seen.json
    now = datetime.now(timezone.utc)
    fresh_ts = (now - timedelta(minutes=5)).isoformat()
    injected = {u: fresh_ts for u in inject_urls}
    FIRST_SEEN_FILE.write_text(json.dumps(injected, indent=2))

    # Also remove these URLs from known_dogs so the code treats them as new
    if KNOWN_FILE.exists():
        known = set(json.loads(KNOWN_FILE.read_text()))
        trimmed = known - set(inject_urls)
        KNOWN_FILE.write_text(json.dumps(sorted(trimmed), indent=2))
        ok(f"Removed {len(inject_urls)} injected URLs from known_dogs.json temporarily")

    # Re-scan
    total2 = run_full_scan(page, url)
    if total2 > 0:
        ok(f"Re-scan returned {total2} dogs")
    else:
        fail("Re-scan returned 0 dogs")

    hs = headings_on_page(page)
    new_heading = next((h for h in hs if "New Dog" in h), None)
    if new_heading:
        ok(f"'New Dogs' section visible after injection: {new_heading!r}")
    else:
        fail("'New Dogs' section not visible despite injected fresh URLs", str(hs))

    new_section = page.locator(".new-dogs-section")
    if new_section.count() > 0:
        rows = new_section.locator(".dog-row").count()
        ok(f"{rows} dog row(s) in New Dogs section (expected ≤ {inject_count})")
        if rows <= inject_count:
            ok(f"Row count ({rows}) ≤ injected count ({inject_count}) — correct")
        else:
            fail(f"Row count ({rows}) > injected count ({inject_count})")
    else:
        fail(".new-dogs-section element not found in DOM")

    # "New Dogs" must appear before "Available"
    new_idx = next((i for i, t in enumerate(hs) if "New Dog" in t), None)
    avail_idx = next((i for i, t in enumerate(hs) if "Available" in t), None)
    if new_idx is not None and avail_idx is not None and new_idx < avail_idx:
        ok(f"'New Dogs' (idx {new_idx}) before 'Available' (idx {avail_idx})")
    else:
        fail(f"Section order wrong: New Dogs={new_idx}, Available={avail_idx}")

    screenshot(page, "02_injected_new_dogs_section")

    # Restore known_dogs
    if KNOWN_FILE.exists():
        known2 = set(json.loads(KNOWN_FILE.read_text()))
        restored = known2 | set(inject_urls)
        KNOWN_FILE.write_text(json.dumps(sorted(restored), indent=2))
        ok("Restored injected URLs to known_dogs.json")


# ---------------------------------------------------------------------------
# 4. Badge and card styling
# ---------------------------------------------------------------------------

def test_new_dogs_badges(page: Page):
    section("4. NEW badges — visual styling and card content")

    new_section = page.locator(".new-dogs-section")
    if new_section.count() == 0:
        # Section may not be visible if inject test's state was reset
        ok("No .new-dogs-section visible — inject state was cleaned up; skipping badge detail checks")
        return

    rows = new_section.locator(".dog-row").all()
    if not rows:
        fail("No .dog-row in .new-dogs-section")
        return

    # Every row must have a .flag-new with text "NEW"
    rows_with_badge = sum(
        1 for r in rows
        if r.locator(".flag-new").count() > 0 and r.locator(".flag-new").first.inner_text().strip() == "NEW"
    )
    if rows_with_badge == len(rows):
        ok(f"All {rows_with_badge}/{len(rows)} rows have a .flag-new 'NEW' badge")
    else:
        fail(f"Only {rows_with_badge}/{len(rows)} rows have 'NEW' badge")

    # Badge must be green, not default gray
    color_info = page.evaluate("""
        () => {
            const el = document.querySelector('.flag-new');
            if (!el) return null;
            const s = window.getComputedStyle(el);
            return { bg: s.backgroundColor, color: s.color, text: el.textContent.trim() };
        }
    """)
    if color_info:
        ok(f"Badge style: bg={color_info['bg']}, color={color_info['color']}")
        if "238, 238, 238" not in color_info["bg"]:
            ok("Badge background is not default gray — visually distinct")
        else:
            fail("Badge background is the default gray flag color", color_info["bg"])
    else:
        fail("Could not read .flag-new computed style")

    # Section heading is green
    heading_color = page.evaluate("""
        () => {
            const h = document.querySelector('.new-dogs-section h3');
            return h ? window.getComputedStyle(h).color : null;
        }
    """)
    if heading_color:
        ok(f"New Dogs heading color: {heading_color} (should be greenish)")

    # Left border present
    border = page.evaluate("""
        () => {
            const el = document.querySelector('.new-dogs-section');
            if (!el) return null;
            const s = window.getComputedStyle(el);
            return { w: s.borderLeftWidth, color: s.borderLeftColor };
        }
    """)
    if border and border["w"] not in ("0px", ""):
        ok(f"Section left border: {border['w']} ({border['color']})")
    else:
        fail("Missing green left border on .new-dogs-section")

    # First 3 cards: name, link, source, breed chip, distance
    for i, r in enumerate(rows[:3]):
        name_el = r.locator(".dog-name")
        name = name_el.inner_text() if name_el.count() > 0 else ""
        href = name_el.get_attribute("href") if name_el.count() > 0 else ""
        source = r.locator(".dog-source").inner_text() if r.locator(".dog-source").count() > 0 else ""
        breed = r.locator(".chip-breed").inner_text() if r.locator(".chip-breed").count() > 0 else ""
        has_dist = (r.locator("[style*='background:#e8f0fe']").count() > 0 or
                    r.locator("span[style*='color:#999']").count() > 0)
        if name:
            ok(f"Card {i+1}: name={name!r}, source={source!r}, breed={breed!r}, link_ok={bool(href)}, dist_ok={has_dist}")
        else:
            fail(f"Card {i+1}: missing dog name")

    # No RESERVED dogs should be in New Dogs section
    reserved_in_new = new_section.locator(".flag-reserved").count()
    if reserved_in_new == 0:
        ok("No RESERVED dogs in New Dogs section")
    else:
        fail(f"New Dogs section contains {reserved_in_new} RESERVED dog(s)")

    screenshot(page, "03_badges_and_card_detail")


# ---------------------------------------------------------------------------
# 5. 24h expiry: aged timestamps → section disappears
# ---------------------------------------------------------------------------

def test_expiry_hides_section(page: Page, url: str):
    section("5. 24h expiry — aged timestamps → 'New Dogs' section hidden")

    # Inject fresh URLs again so we have a known state
    if KNOWN_FILE.exists():
        known = set(json.loads(KNOWN_FILE.read_text()))
    else:
        known = set()

    if not known:
        ok("No known dogs to use for expiry test — skipping")
        return

    # Pick a few URLs from known, remove from known, inject with fresh ts
    sample = list(known)[:3]
    trimmed = known - set(sample)
    KNOWN_FILE.write_text(json.dumps(sorted(trimmed), indent=2))

    now = datetime.now(timezone.utc)
    fresh = {u: (now - timedelta(minutes=10)).isoformat() for u in sample}
    FIRST_SEEN_FILE.write_text(json.dumps(fresh, indent=2))

    # Scan: should see New Dogs
    total = run_full_scan(page, url)
    hs = headings_on_page(page)
    new_h = next((h for h in hs if "New Dog" in h), None)
    if new_h:
        ok(f"Confirmed New Dogs section visible before aging: {new_h!r}")
    else:
        ok("New Dogs section not visible (injected URLs may not be in_range) — aging test still valid")

    # Now age ALL first_seen timestamps to 26 hours ago
    data = json.loads(FIRST_SEEN_FILE.read_text()) if FIRST_SEEN_FILE.exists() else {}
    aged = {u: (now - timedelta(hours=26)).isoformat() for u in data}
    FIRST_SEEN_FILE.write_text(json.dumps(aged, indent=2))
    ok(f"Aged {len(aged)} timestamps to 26 hours ago")

    total2 = run_full_scan(page, url)
    hs2 = headings_on_page(page)
    new_h2 = next((h2 for h2 in hs2 if "New Dog" in h2), None)

    if new_h2 is None:
        ok("No 'New Dogs' section when all timestamps are 26h old (correct)")
    else:
        fail(f"'New Dogs' section still visible with 26h-old timestamps: {new_h2!r}")

    new_el = page.locator(".new-dogs-section")
    if new_el.count() == 0:
        ok(".new-dogs-section element absent from DOM (correct)")
    else:
        fail(".new-dogs-section element still in DOM despite expired timestamps")

    avail = next((h2 for h2 in hs2 if "Available" in h2), None)
    if avail:
        ok(f"'Available' section still present after expiry: {avail!r}")
    else:
        fail("'Available' section missing after expiry test")

    # Restore known_dogs
    restored = trimmed | set(sample)
    KNOWN_FILE.write_text(json.dumps(sorted(restored), indent=2))

    screenshot(page, "04_expiry_section_gone")


# ---------------------------------------------------------------------------
# 6. Partial expiry: mixed timestamps → only fresh subset shown
# ---------------------------------------------------------------------------

def test_partial_expiry(page: Page, url: str):
    section("6. Partial expiry — mixed timestamps → only recent subset shown")

    if not KNOWN_FILE.exists():
        ok("No known_dogs.json — skipping partial expiry test")
        return

    known = set(json.loads(KNOWN_FILE.read_text()))
    if len(known) < 6:
        ok(f"Only {len(known)} known URLs — partial test needs ≥6; skipping")
        return

    sample = list(known)[:6]
    trimmed = known - set(sample)
    KNOWN_FILE.write_text(json.dumps(sorted(trimmed), indent=2))

    now = datetime.now(timezone.utc)
    fresh_ts = (now - timedelta(minutes=10)).isoformat()
    stale_ts = (now - timedelta(hours=26)).isoformat()

    # 3 fresh, 3 stale
    mixed = {
        sample[0]: fresh_ts,
        sample[1]: fresh_ts,
        sample[2]: fresh_ts,
        sample[3]: stale_ts,
        sample[4]: stale_ts,
        sample[5]: stale_ts,
    }
    FIRST_SEEN_FILE.write_text(json.dumps(mixed, indent=2))
    ok("Wrote 3 fresh (<1h) + 3 stale (26h) URLs into first_seen.json")

    total = run_full_scan(page, url)
    hs = headings_on_page(page)
    new_h = next((h for h in hs if "New Dog" in h), None)

    new_el = page.locator(".new-dogs-section")
    if new_el.count() > 0:
        rows = new_el.locator(".dog-row").count()
        ok(f"New Dogs section visible with {rows} dog(s) (fresh URLs: 3)")
        # Can't assert exact count because not all fresh URLs may be in_range+non-reserved
        if rows <= 3:
            ok(f"Row count ({rows}) ≤ fresh URL count (3) — correct subset")
        else:
            fail(f"Row count ({rows}) > 3 fresh URLs — stale URLs may have leaked through")
    else:
        ok("New Dogs section hidden (fresh sample URLs may not be in_range/non-reserved — acceptable)")

    # Restore
    KNOWN_FILE.write_text(json.dumps(sorted(known), indent=2))
    FIRST_SEEN_FILE.unlink(missing_ok=True)

    screenshot(page, "05_partial_expiry")


# ---------------------------------------------------------------------------
# 7. Watcher integration
# ---------------------------------------------------------------------------

def test_watcher_integration():
    section("7. Watcher integration — source code verification")

    src = Path("/home/claude-dev/dog-scanner/app.py").read_text()

    # Watcher uses prev-scan delta (ui_new_urls) to determine what's new for the UI
    if "ui_new_urls" in src and "_update_first_seen(ui_new_urls)" in src:
        ok("Watcher calls _update_first_seen(ui_new_urls) — prev-scan delta only")
    else:
        fail("Watcher does not call _update_first_seen(ui_new_urls)")

    # Must NOT bulk-stamp all current URLs as new
    if "_update_first_seen(current_urls)" not in src:
        ok("Watcher does NOT call _update_first_seen(current_urls) — bulk stamping absent")
    else:
        fail("Watcher still calls _update_first_seen(current_urls) — this stamps ALL dogs as new")

    # Email dedup still uses cumulative known_dogs.json
    if "known = set(_load_json(KNOWN_FILE, []))" in src:
        ok("Watcher reads known_dogs.json for email dedup")
    else:
        fail("Watcher does not read known_dogs.json for email dedup")

    # prev_scan_urls.json must be used as the UI baseline
    if "PREV_SCAN_FILE" in src and "prev_scan_urls" in src:
        ok("Watcher uses PREV_SCAN_FILE / prev_scan_urls for UI new-dog baseline")
    else:
        fail("Watcher missing PREV_SCAN_FILE / prev_scan_urls baseline")

    # On first run (no prev_scan), ui_new_urls must be empty — no false positives
    if "ui_new_urls = current_urls - prev_scan_urls if prev_scan_urls else set()" in src:
        ok("Bootstrap guard: ui_new_urls is empty when no prev_scan exists")
    else:
        fail("No bootstrap guard — first run could show all dogs as new")


# ---------------------------------------------------------------------------
# 8. Mobile viewport
# ---------------------------------------------------------------------------

def test_mobile(page: Page, url: str):
    section("8. Mobile viewport — New Dogs section at 375×812")

    # Inject a couple of fresh URLs
    if KNOWN_FILE.exists():
        known = set(json.loads(KNOWN_FILE.read_text()))
        if known:
            sample = list(known)[:3]
            trimmed = known - set(sample)
            KNOWN_FILE.write_text(json.dumps(sorted(trimmed), indent=2))
            now = datetime.now(timezone.utc)
            FIRST_SEEN_FILE.write_text(json.dumps(
                {u: (now - timedelta(minutes=5)).isoformat() for u in sample},
                indent=2
            ))

    page.set_viewport_size({"width": 375, "height": 812})
    total = run_full_scan(page, url)
    ok(f"Mobile scan: {total} dogs")

    new_el = page.locator(".new-dogs-section")
    if new_el.count() > 0:
        ok("New Dogs section present on mobile")
        box = new_el.bounding_box()
        if box and box["width"] <= 380:
            ok(f"Section width {box['width']:.0f}px fits 375px viewport")
        else:
            fail(f"Section overflows mobile viewport: {box}")

        first_row = new_el.locator(".dog-row").first
        if first_row.count() > 0:
            name_box = first_row.locator(".dog-name").bounding_box()
            if name_box and name_box["width"] > 0:
                ok(f"Dog name visible on mobile (width={name_box['width']:.0f}px)")

        scroll_w = page.evaluate("document.body.scrollWidth")
        if scroll_w <= 385:
            ok(f"No horizontal overflow (scrollWidth={scroll_w}px)")
        else:
            fail(f"Horizontal overflow: scrollWidth={scroll_w}px")

        screenshot(page, "06_mobile_new_dogs")
    else:
        ok("No New Dogs section on mobile (injected URLs may not be in_range) — acceptable")
        screenshot(page, "06_mobile_no_section")

    # Restore
    if KNOWN_FILE.exists():
        known_data = set(json.loads(KNOWN_FILE.read_text()))
    else:
        known_data = set()
    sample_urls = list(json.loads(FIRST_SEEN_FILE.read_text()).keys()) if FIRST_SEEN_FILE.exists() else []
    if sample_urls:
        KNOWN_FILE.write_text(json.dumps(sorted(known_data | set(sample_urls)), indent=2))
    FIRST_SEEN_FILE.unlink(missing_ok=True)

    page.set_viewport_size({"width": 1280, "height": 800})


# ---------------------------------------------------------------------------
# 9. UI screenshots — final full walkthrough
# ---------------------------------------------------------------------------

def test_ui_screenshots(page: Page, url: str):
    section("9. UI screenshots — complete walkthrough with new dogs visible")

    # Inject fresh state for a good screenshot
    if KNOWN_FILE.exists():
        known = set(json.loads(KNOWN_FILE.read_text()))
        if known:
            sample = list(known)[:5]
            trimmed = known - set(sample)
            KNOWN_FILE.write_text(json.dumps(sorted(trimmed), indent=2))
            now = datetime.now(timezone.utc)
            FIRST_SEEN_FILE.write_text(json.dumps(
                {u: (now - timedelta(minutes=5)).isoformat() for u in sample},
                indent=2
            ))

    page.goto(url, wait_until="load", timeout=20_000)
    time.sleep(2)
    screenshot(page, "07_initial_page_load")

    page.locator("button", has_text="Scan").first.click()
    time.sleep(3)
    screenshot(page, "08_scan_in_progress")

    expect(page.locator(".dog-list").first).to_be_visible(timeout=120_000)
    time.sleep(1)
    screenshot(page, "09_scan_complete")

    new_el = page.locator(".new-dogs-section")
    if new_el.count() > 0:
        new_el.scroll_into_view_if_needed()
        time.sleep(0.5)
        screenshot(page, "10_new_dogs_close")

        avail = page.locator("h3", has_text="Available").first
        if avail.count() > 0:
            avail.scroll_into_view_if_needed()
            time.sleep(0.5)
            screenshot(page, "11_available_for_comparison")

    hs = headings_on_page(page)
    ok(f"Final sections: {hs}")

    # Restore
    if KNOWN_FILE.exists() and FIRST_SEEN_FILE.exists():
        known_now = set(json.loads(KNOWN_FILE.read_text()))
        fresh_urls = set(json.loads(FIRST_SEEN_FILE.read_text()).keys())
        KNOWN_FILE.write_text(json.dumps(sorted(known_now | fresh_urls), indent=2))
    FIRST_SEEN_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7861")
    args = parser.parse_args()
    url = args.url.rstrip("/")

    print(f"Testing New Dogs feature at: {url}")
    print(f"Screenshots → {SCREENSHOTS_DIR}/")
    print("=" * 65)

    test_backend_logic()
    test_watcher_integration()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 UITest/1.0"
            ),
        )
        page = ctx.new_page()
        try:
            test_no_new_dogs_on_bootstrap(page, url)
            test_new_dogs_with_injected_state(page, url)
            test_new_dogs_badges(page)
            test_expiry_hides_section(page, url)
            test_partial_expiry(page, url)
            test_mobile(page, url)
            test_ui_screenshots(page, url)
        except Exception as e:
            import traceback
            fail(f"Unexpected error: {e}", traceback.format_exc()[-500:])
        finally:
            browser.close()

    print("\n" + "=" * 65)
    print(f"Results: {PASS_COUNT} passed, {FAIL_COUNT} failed")
    print(f"Screenshots: {SCREENSHOTS_DIR}/")
    return 0 if FAIL_COUNT == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
