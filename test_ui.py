"""End-to-end browser test for scanner.cookstar.cc.

Tests the site as a real user would:
  1. Page load — title, heading, no console errors
  2. Scan button — progress bar animates, results appear, count > 0
  3. Results content — dog cards have name/source/distance, links, sections
  4. Subscribe — valid email accepted, duplicate rejected, bad email rejected
  5. Mobile viewport — layout doesn't overflow, tap targets are large enough

Run: python3 test_ui.py [--url URL]
     Defaults to https://scanner.cookstar.cc
"""

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright, expect


PASS_COUNT = 0
FAIL_COUNT = 0


def ok(msg: str) -> None:
    global PASS_COUNT
    PASS_COUNT += 1
    print(f"  PASS  {msg}")


def fail(msg: str, detail: str = "") -> None:
    global FAIL_COUNT
    FAIL_COUNT += 1
    extra = f"\n        {detail}" if detail else ""
    print(f"  FAIL  {msg}{extra}")


def section(title: str) -> None:
    print(f"\n[{title}]")


# ---------------------------------------------------------------------------
# 1. Page load
# ---------------------------------------------------------------------------

def test_page_load(page: Page, url: str) -> None:
    section("1. Page load")
    t0 = time.monotonic()
    page.goto(url, wait_until="load", timeout=30_000)

    # Gradio sets <title> via JS — wait up to 5s for it to appear
    title = ""
    for _ in range(10):
        title = page.title()
        if title:
            break
        time.sleep(0.5)

    elapsed = time.monotonic() - t0

    if "Dog Scanner" in title:
        ok(f"title: {title!r}")
    else:
        fail("title missing 'Dog Scanner'", f"got: {title!r}")

    h1 = page.locator("h1").first
    h1_text = h1.inner_text()
    if "Dog Scanner" in h1_text:
        ok(f"H1 visible: {h1_text[:60]!r}")
    else:
        fail("H1 missing 'Dog Scanner'", h1_text[:80])

    scan_btn = page.locator("button", has_text="Scan").first
    if scan_btn.count() > 0:
        ok("Scan button present")
    else:
        fail("Scan button not found")

    email_input = page.get_by_placeholder("you@example.com")
    if email_input.count() > 0:
        ok("Subscribe email input present")
    else:
        fail("Subscribe email input not found")

    if elapsed < 8.0:
        ok(f"Initial load: {elapsed:.2f}s")
    else:
        fail(f"Initial load slow: {elapsed:.2f}s (expected < 8s)")


# ---------------------------------------------------------------------------
# 2. Scan flow
# ---------------------------------------------------------------------------

def test_scan(page: Page) -> int:
    """Click Scan, wait for full completion. Returns final dog count."""
    section("2. Scan flow")

    console_errors: list[str] = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

    scan_btn = page.locator("button", has_text="Scan").first
    t0 = time.monotonic()
    scan_btn.click()

    # Progress bar should appear quickly
    progress = page.locator(".scan-progress")
    try:
        expect(progress).to_be_visible(timeout=8_000)
        ok("Progress bar appeared after click")
    except Exception as e:
        fail("Progress bar did not appear", str(e)[:120])

    # Wait for .dog-list — only renders on the final yield, after all adapters complete
    dog_list = page.locator(".dog-list").first
    try:
        expect(dog_list).to_be_visible(timeout=90_000)
        elapsed = time.monotonic() - t0
        ok(f"Results rendered in {elapsed:.1f}s")
    except Exception as e:
        fail("Scan did not produce .dog-list within 90s", str(e)[:120])
        return 0

    # Read final count from the Number component
    num_input = page.locator("input[type='number']").first
    try:
        raw = num_input.input_value(timeout=3_000)
        dog_count = int(raw) if raw else 0
    except Exception:
        dog_count = 0

    if dog_count > 0:
        ok(f"Dog count: {dog_count}")
    else:
        fail("Dog count is 0 after scan completed")

    real_errors = [e for e in console_errors
                   if not any(x in e for x in ("WebSocket", "gradio", "favicon", "JSHINT"))]
    if real_errors:
        fail(f"{len(real_errors)} JS error(s) during scan", real_errors[0][:120])
    else:
        ok("No JS console errors during scan")

    return dog_count


# ---------------------------------------------------------------------------
# 3. Results content
# ---------------------------------------------------------------------------

def test_results(page: Page) -> None:
    section("3. Results content")

    # Section headings (Available / Reserved / Possibly out of range)
    headings = page.locator("h3").all()
    h3_texts = []
    for h in headings:
        try:
            h3_texts.append(h.inner_text())
        except Exception:
            pass
    expected_kw = ("Available", "Reserved", "Possibly")
    if any(any(k in t for k in expected_kw) for t in h3_texts):
        ok(f"Section headings: {h3_texts}")
    else:
        fail("No expected section headings (Available/Reserved/Possibly)", str(h3_texts))

    # Row count
    rows = page.locator(".dog-row")
    row_count = rows.count()
    if row_count > 0:
        ok(f"{row_count} dog row(s) rendered")
    else:
        fail("No .dog-row elements found")
        return

    # Inspect first card
    first = rows.first
    dog_name_el = first.locator(".dog-name")
    dog_source_el = first.locator(".dog-source")
    chips = first.locator(".chip")

    name_text = dog_name_el.inner_text() if dog_name_el.count() > 0 else ""
    source_text = dog_source_el.inner_text() if dog_source_el.count() > 0 else ""
    chip_count = chips.count()

    if name_text:
        ok(f"First dog name: {name_text!r}")
    else:
        fail("First dog card has no .dog-name text")

    if source_text:
        ok(f"First dog source badge: {source_text!r}")
    else:
        fail("First dog card has no .dog-source text")

    if chip_count >= 1:
        ok(f"First dog has {chip_count} chip(s) (breed/age/sex/location)")
    else:
        fail("First dog card has no .chip elements")

    href = dog_name_el.get_attribute("href") if dog_name_el.count() > 0 else ""
    if href and href.startswith("http"):
        ok(f"Dog link is a valid URL ({href[:60]})")
    else:
        fail("Dog name link missing or not an http URL", href)

    # Distance badge or 'distance unknown'
    has_dist = (first.locator("[style*='background:#e8f0fe']").count() > 0 or
                first.locator("span[style*='color:#999']").count() > 0)
    if has_dist:
        ok("Distance indicator present on first card")
    else:
        fail("No distance badge or 'unknown' indicator on first card")

    # Check a few random cards for link validity (spot-check rows 5, 10, 20)
    bad_links = 0
    for idx in (5, 10, 20):
        if idx >= row_count:
            break
        r = rows.nth(idx)
        link_el = r.locator(".dog-name")
        if link_el.count() > 0:
            h = link_el.get_attribute("href") or ""
            if not h.startswith("http"):
                bad_links += 1
    if bad_links == 0:
        ok("Spot-check: rows 5/10/20 all have valid http links")
    else:
        fail(f"Spot-check: {bad_links} row(s) with missing/invalid links")


# ---------------------------------------------------------------------------
# 4. Subscribe flow
# ---------------------------------------------------------------------------

def test_subscribe(page: Page) -> None:
    section("4. Subscribe flow")

    email_in = page.get_by_placeholder("you@example.com")
    sub_btn = page.locator("button", has_text="Subscribe").first

    def get_status_text():
        time.sleep(1.2)
        # Gradio renders subscribe output in a markdown block below the row
        all_md = page.locator(".prose p, .prose strong").all_inner_texts()
        return " ".join(all_md).lower()

    # Bad email
    email_in.fill("notanemail")
    sub_btn.click()
    resp = get_status_text()
    if "valid email" in resp or "please" in resp:
        ok("Invalid email rejected")
    else:
        fail("Invalid email not rejected", resp[:120])

    # Valid new address (example.invalid never deliverable, safe to use)
    test_email = "zzz-ui-test-do-not-deliver@example.invalid"
    email_in.fill(test_email)
    sub_btn.click()
    resp = get_status_text()
    if "subscribed" in resp:
        ok("Valid email subscribed successfully")
    else:
        fail("Subscribe with valid email gave unexpected response", resp[:120])

    # Duplicate
    sub_btn.click()
    resp = get_status_text()
    if "already" in resp:
        ok("Duplicate email: 'already subscribed' returned")
    else:
        fail("Duplicate email not detected", resp[:120])

    # Empty
    email_in.fill("")
    sub_btn.click()
    resp = get_status_text()
    if "valid email" in resp or "please" in resp:
        ok("Empty email rejected")
    else:
        fail("Empty email not rejected", resp[:120])

    # Cleanup test address
    subs_file = Path("/home/claude-dev/dog-scanner/state/subscribers.json")
    try:
        subs = json.loads(subs_file.read_text())
        if test_email in subs:
            subs.remove(test_email)
            tmp = subs_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(subs, indent=2))
            tmp.replace(subs_file)
            ok(f"Cleaned up test subscriber")
        else:
            ok("Test subscriber not in file (already absent)")
    except Exception as e:
        fail("Could not clean up test subscriber", str(e))


# ---------------------------------------------------------------------------
# 5. Mobile viewport
# ---------------------------------------------------------------------------

def test_mobile(page: Page, url: str) -> None:
    section("5. Mobile viewport (375×812 — iPhone 14)")

    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(url, wait_until="load", timeout=30_000)
    time.sleep(2)

    h1 = page.locator("h1").first
    try:
        expect(h1).to_be_visible(timeout=5_000)
        box = h1.bounding_box()
        if box and box["x"] >= 0 and box["width"] <= 385:
            ok(f"H1 fits in 375px viewport (x={box['x']:.0f}, w={box['width']:.0f}px)")
        else:
            fail("H1 overflows mobile viewport", str(box))
    except Exception as e:
        fail("H1 not visible on mobile", str(e)[:120])

    scan_btn = page.locator("button", has_text="Scan").first
    try:
        box = scan_btn.bounding_box()
        if box and box["height"] >= 40:
            ok(f"Scan button tap target: {box['height']:.0f}px tall")
        else:
            fail("Scan button too small for touch",
                 f"height={box['height'] if box else 'N/A'}px")
    except Exception as e:
        fail("Could not measure scan button", str(e)[:80])

    email_in = page.get_by_placeholder("you@example.com")
    try:
        box = email_in.bounding_box()
        if box and box["height"] >= 40:
            ok(f"Email input tap target: {box['height']:.0f}px tall")
        else:
            fail("Email input too small for touch",
                 f"height={box['height'] if box else 'N/A'}px")
    except Exception as e:
        fail("Could not measure email input", str(e)[:80])

    scroll_width = page.evaluate("document.body.scrollWidth")
    if scroll_width <= 390:
        ok(f"No horizontal overflow (scrollWidth={scroll_width}px)")
    else:
        fail(f"Horizontal overflow (scrollWidth={scroll_width}px > 375px)")

    page.set_viewport_size({"width": 1280, "height": 800})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://scanner.cookstar.cc")
    args = parser.parse_args()
    url = args.url.rstrip("/")

    print(f"Testing: {url}")
    print("=" * 60)

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
            test_page_load(page, url)
            dog_count = test_scan(page)
            if dog_count > 0:
                test_results(page)
            else:
                print("\n[3. Results content]")
                print("  SKIP  (scan returned 0 dogs — skipping results checks)")
            test_subscribe(page)
            test_mobile(page, url)
        except Exception as e:
            fail(f"Unexpected top-level error: {e}")
        finally:
            browser.close()

    print("\n" + "=" * 60)
    print(f"Results: {PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 0 if FAIL_COUNT == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
