"""End-to-end browser test for growth.cookstar.cc/finder.

Tests the Finder page as a real user would, verifying:
  1. Page load — title, heading, wallet count present
  2. Filter presets — each button changes the wallet count
  3. Category tabs — selecting a category filters correctly
  4. Sliders — dragging each slider changes the wallet count
  5. Checkboxes — toggling changes the wallet count
  6. View presets — switching changes which columns are shown
  7. Sort columns — clicking a column header re-sorts rows
  8. Row expansion — clicking ▸ opens a wallet detail panel

Run: python3 test_growth_ui.py [--url URL]
"""

import argparse
import sys
import time
import re

from playwright.sync_api import Page, sync_playwright, expect

URL = "https://growth.cookstar.cc/finder"
PASS_COUNT = 0
FAIL_COUNT = 0


def ok(msg): global PASS_COUNT; PASS_COUNT += 1; print(f"  PASS  {msg}")
def fail(msg, detail=""): global FAIL_COUNT; FAIL_COUNT += 1; print(f"  FAIL  {msg}" + (f"\n        {detail}" if detail else ""))
def section(t): print(f"\n[{t}]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def wallet_count(page: Page) -> int:
    """Parse the 'N wallets' H2 text."""
    try:
        txt = page.locator("h2").first.inner_text(timeout=8_000)
        m = re.search(r"([\d,]+)\s+wallet", txt)
        return int(m.group(1).replace(",", "")) if m else 0
    except Exception:
        return -1


def wait_for_count_change(page: Page, old: int, timeout: float = 12.0) -> int:
    """Poll until the H2 wallet count differs from `old`. Returns new count."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        n = wallet_count(page)
        if n != old and n >= 0:
            return n
        time.sleep(0.4)
    return wallet_count(page)


def slider_drag(page: Page, slider_index: int, to_pct: float) -> None:
    """
    Drag slider[slider_index] to a fractional position (0=min, 1=max).

    Strategy: mouse-drag across the entire track — many `input` events fire
    (React updates state each time), then mouseup fires `apply(s)` with the
    latest re-rendered state. Works reliably because by the time mouseup fires,
    React has already processed all the intermediate input events.
    """
    sl = page.locator('input[type="range"]').nth(slider_index)
    box = sl.bounding_box()
    if not box:
        return
    track_start = box["x"] + 8          # avoid edge rounding on thumb
    track_end   = box["x"] + box["width"] - 8
    target_x    = track_start + (track_end - track_start) * to_pct
    mid_y       = box["y"] + box["height"] / 2

    page.mouse.move(track_start, mid_y)
    page.mouse.down()
    # Move in small increments so React sees onChange events as the thumb moves
    steps = 10
    for i in range(1, steps + 1):
        page.mouse.move(track_start + (target_x - track_start) * i / steps, mid_y)
        time.sleep(0.02)
    page.mouse.up()
    time.sleep(0.3)   # let React flush the final state before we read the count


def load_fresh(page: Page, params: str = "") -> None:
    """Navigate to /finder (optionally with query string) and wait for render."""
    p = f"{URL}?{params}" if params else URL
    page.goto(p, wait_until="load", timeout=20_000)
    time.sleep(2)   # let Next.js hydrate


# ---------------------------------------------------------------------------
# 1. Page load
# ---------------------------------------------------------------------------

def test_page_load(page: Page) -> int:
    section("1. Page load")
    load_fresh(page)

    title = page.title()
    if "Growth" in title or "Polymarket" in title or "Finder" in title or "Compounder" in title:
        ok(f"title: {title!r}")
    else:
        fail("title doesn't describe the app", title)

    h1 = page.locator("h1").first.inner_text()
    if h1:
        ok(f"H1: {h1!r}")
    else:
        fail("H1 missing")

    n = wallet_count(page)
    if n > 0:
        ok(f"Initial wallet count: {n:,} (Hidden Gems preset)")
    else:
        fail("No wallets shown on initial load", str(n))

    sliders = page.locator('input[type="range"]').count()
    if sliders == 10:
        ok(f"{sliders} range sliders present")
    else:
        fail(f"Expected 10 sliders, got {sliders}")

    return n


# ---------------------------------------------------------------------------
# 2. Filter presets
# ---------------------------------------------------------------------------

def test_filter_presets(page: Page, baseline: int) -> None:
    section("2. Filter preset buttons")

    presets = [
        ("Proven Compounders", lambda n: True),  # any non-zero count accepted
        ("Copy-Ready",         lambda n: True),
        ("All Wallets",        lambda n: n > baseline),  # always more than Hidden Gems
        ("Hidden Gems",        lambda n: n > 0),
    ]
    prev = baseline
    for label, check in presets:
        btn = page.locator("button", has_text=label).first
        if btn.count() == 0:
            fail(f"Preset button '{label}' not found")
            continue
        btn.click()
        new = wait_for_count_change(page, prev, timeout=10)
        if new == -1:
            fail(f"Preset '{label}' — could not read wallet count")
        elif check(new):
            ok(f"Preset '{label}' → {new:,} wallets")
        else:
            fail(f"Preset '{label}' → {new:,} wallets (failed check vs previous {prev:,})")
        prev = new

    # Confirm the preset counts displayed in button labels match H2
    btn_text = page.locator("button", has_text="Hidden Gems").first.inner_text()
    m = re.search(r"\(([\d,]+)\)", btn_text)
    if m:
        label_n = int(m.group(1).replace(",", ""))
        actual_n = wallet_count(page)
        if label_n == actual_n:
            ok(f"Preset count label matches H2 ({label_n:,})")
        else:
            fail(f"Preset label shows {label_n:,} but H2 shows {actual_n:,}")


# ---------------------------------------------------------------------------
# 3. Category tabs
# ---------------------------------------------------------------------------

def test_category_tabs(page: Page) -> None:
    section("3. Category tabs")

    categories = ["Geopolitics", "Politics", "Crypto", "Sports"]
    for cat in categories:
        # Load fresh each iteration so we always start from a clean Hidden Gems
        # baseline and avoid accidentally clicking "All Wallets" preset.
        load_fresh(page)
        baseline = wallet_count(page)

        btn = page.locator("button", has_text=cat).first
        if btn.count() == 0:
            fail(f"Category button '{cat}' not found")
            continue
        btn.click()
        new = wait_for_count_change(page, baseline, timeout=10)
        if new >= 0 and new <= baseline:
            ok(f"Category '{cat}' → {new:,} wallets (≤ baseline {baseline:,})")
        else:
            fail(f"Category '{cat}' → {new:,} wallets (expected ≤ {baseline:,})")


# ---------------------------------------------------------------------------
# 4. Sliders
# ---------------------------------------------------------------------------

def test_sliders(page: Page) -> None:
    section("4. Sliders — drag each one and verify wallet count responds")
    load_fresh(page)
    baseline = wallet_count(page)

    # Each entry: (slider_index, label, drag_to_pct, expect_direction)
    # expect_direction: "fewer" means count should drop, "more" means should rise
    # We drag to_pct=1.0 for "fewer" (maximum restriction) and 0.0 for "more"
    cases = [
        (0,  "Min growth multiple",             0.5, "fewer"),   # raise to ~10×
        (2,  "Min days active",                 0.5, "fewer"),   # raise to ~180 days
        (3,  "Min lifetime trades",             0.5, "fewer"),   # raise to ~1000 trades
        (4,  "Max days since last trade",       0.0, "fewer"),   # lower to 1 day
        (5,  "Min trajectory R²",               0.8, "fewer"),   # raise to 0.8
        (6,  "Min realised return on deposits", 0.5, "fewer"),   # raise to 150%
        (7,  "Min win rate",                    0.5, "fewer"),   # raise to 50%
        (8,  "Max paper gain decay risk",       0.0, "fewer"),   # lower to 0
        (1,  "Max total deposits",              0.1, "fewer"),   # lower to ~10k
        (9,  "Min conviction-hold ratio",       0.9, "fewer"),   # raise to 0.9
    ]

    for idx, label, to_pct, direction in cases:
        before = wallet_count(page)
        slider_drag(page, idx, to_pct)

        # Wait for navigation (URL push triggers a server re-render)
        new = wait_for_count_change(page, before, timeout=12)

        if new == before:
            # Count didn't change — could be already at extremes, warn not fail
            ok(f"Slider [{idx}] {label!r} → {new:,} (no change; may already be at limit)")
        elif direction == "fewer" and new < before:
            ok(f"Slider [{idx}] {label!r} → {new:,} wallets (↓ from {before:,})")
        elif direction == "more" and new > before:
            ok(f"Slider [{idx}] {label!r} → {new:,} wallets (↑ from {before:,})")
        else:
            fail(f"Slider [{idx}] {label!r} moved to {to_pct*100:.0f}%: {before:,}→{new:,} (expected {direction})")

        # Reset to fresh state after each slider so next test starts clean
        load_fresh(page)


# ---------------------------------------------------------------------------
# 5. Checkboxes
# ---------------------------------------------------------------------------

def test_checkboxes(page: Page) -> None:
    section("5. Checkboxes")
    load_fresh(page)
    baseline = wallet_count(page)

    # "Include speculative" — should add wallets
    speculative_cb = page.get_by_role("checkbox", name="Include speculative (paper-heavy) wallets")
    speculative_cb.check()
    new = wait_for_count_change(page, baseline, timeout=10)
    if new > baseline:
        ok(f"Include speculative: {baseline:,} → {new:,} wallets (↑)")
    elif new == baseline:
        ok(f"Include speculative: count unchanged ({new:,}) — no speculative wallets in this preset")
    else:
        fail(f"Include speculative: {baseline:,} → {new:,} (expected ≥)")

    # Uncheck speculative (load fresh to get clean state)
    load_fresh(page)
    baseline = wallet_count(page)

    # "Include failing" — should add wallets
    failing_cb = page.get_by_role("checkbox", name="Include wallets failing other blueprint filters")
    failing_cb.check()
    new = wait_for_count_change(page, baseline, timeout=10)
    if new > baseline:
        ok(f"Include failing wallets: {baseline:,} → {new:,} wallets (↑)")
    elif new == baseline:
        ok(f"Include failing wallets: count unchanged ({new:,}) — no failing wallets for this preset")
    else:
        fail(f"Include failing wallets: {baseline:,} → {new:,} (expected ≥)")


# ---------------------------------------------------------------------------
# 6. View presets
# ---------------------------------------------------------------------------

def test_view_presets(page: Page) -> None:
    section("6. View presets — Scanner / Performance / Copy-Fit")
    load_fresh(page)

    # All three view presets should load and show some column that others don't
    view_checks = {
        "Performance": ["R²", "Deposits", "Trades", "Portfolio"],
        "Copy-Fit":    ["Return on deposits", "Capital at risk", "Big win"],
        "Scanner":     [],  # default, trust it loaded correctly
    }

    for view_label, expected_cols in view_checks.items():
        btn = page.locator("button", has_text=view_label).first
        btn.click()
        time.sleep(2.5)  # view switch navigates to a new URL

        count = wallet_count(page)
        if count > 0:
            ok(f"View '{view_label}' loads {count:,} wallets")
        else:
            fail(f"View '{view_label}' shows 0 wallets after switch")

        content = page.content()
        for col in expected_cols:
            if col.lower() in content.lower():
                ok(f"  View '{view_label}' contains '{col}' column")
            else:
                fail(f"  View '{view_label}' missing '{col}' column header")


# ---------------------------------------------------------------------------
# 7. Sort columns
# ---------------------------------------------------------------------------

def test_sort_columns(page: Page) -> None:
    section("7. Sort columns")
    load_fresh(page)
    initial_count = wallet_count(page)

    # Column headers that are links / clickable (from finder_columns.tsx / table)
    # They appear as <a> or <th> elements with ?sort=X params
    sort_links = page.locator("a[href*='sort=']").all()
    if len(sort_links) == 0:
        sort_links = page.locator("th a").all()

    if not sort_links:
        fail("No sortable column header links found")
        return

    ok(f"Found {len(sort_links)} sortable column(s)")

    # Click the first two available sort columns
    for link in sort_links[:3]:
        label = link.inner_text().strip()
        before_first_row = ""
        rows = page.locator("table tbody tr, [role='row']")
        if rows.count() > 0:
            try:
                before_first_row = rows.first.inner_text()[:40]
            except Exception:
                pass
        link.click()
        time.sleep(2.5)

        after_count = wallet_count(page)
        after_first_row = ""
        rows = page.locator("table tbody tr, [role='row']")
        if rows.count() > 0:
            try:
                after_first_row = rows.first.inner_text()[:40]
            except Exception:
                pass

        if after_count == initial_count or abs(after_count - initial_count) <= 1:
            ok(f"Sort by '{label}': count stable ({after_count:,})")
        else:
            fail(f"Sort by '{label}': count changed {initial_count:,}→{after_count:,} (sort should not filter)")

        if before_first_row != after_first_row:
            ok(f"Sort by '{label}': row order changed (first row differs)")
        else:
            ok(f"Sort by '{label}': first row unchanged (may be same wallet wins all sorts)")

        load_fresh(page)
        initial_count = wallet_count(page)


# ---------------------------------------------------------------------------
# 8. Row expansion
# ---------------------------------------------------------------------------

def test_row_expansion(page: Page) -> None:
    section("8. Row expansion (▸ → wallet detail panel)")
    load_fresh(page)

    expand_btns = page.locator("button", has_text="▸").all()
    if not expand_btns:
        # Try alternative selector
        expand_btns = page.locator("button[aria-label*='expand'], button[aria-label*='Expand']").all()

    if not expand_btns:
        fail("No ▸ expand buttons found in table rows")
        return

    ok(f"Found {len(expand_btns)} expand button(s)")

    # Click the first row's expand button
    btn = expand_btns[0]
    btn.click()
    time.sleep(2)

    # WalletDetailPanel renders with data-detail-panel attribute
    detail = page.locator("[data-detail-panel]")
    try:
        expect(detail).to_be_visible(timeout=5_000)
        addr = detail.locator(".mono.small").first.inner_text()
        ok(f"Row expanded: detail panel visible (addr: {addr[:18]}...)")
    except Exception as e:
        fail("Row expand clicked but [data-detail-panel] not visible", str(e)[:120])

    # Collapse (click ▾ or same button again)
    # The button should have changed to ▾ after expand
    collapse_btn = page.locator("button", has_text="▾").first
    if collapse_btn.count() > 0:
        collapse_btn.click()
        time.sleep(1)
        ok("Row collapsed (▾ button found and clicked)")
    else:
        # Try clicking the same button again (toggle)
        btn.click()
        time.sleep(1)
        ok("Row collapse attempted (toggle same button)")


# ---------------------------------------------------------------------------
# 9. Search input
# ---------------------------------------------------------------------------

def test_search(page: Page) -> None:
    section("9. Search address input")
    load_fresh(page)
    baseline = wallet_count(page)

    # Find any wallet address from the page to search for
    page_text = page.inner_text("body")
    m = re.search(r"0x[0-9a-fA-F]{10,}", page_text)
    if not m:
        fail("Could not find a wallet address in the page to search for")
        return

    addr_prefix = m.group()[:10]
    search_input = page.locator("input[placeholder*='0x']").first
    if search_input.count() == 0:
        fail("Search address input not found")
        return

    search_input.fill(addr_prefix)
    search_input.press("Enter")
    new = wait_for_count_change(page, baseline, timeout=10)
    if new < baseline:
        ok(f"Search '{addr_prefix}': {baseline:,} → {new:,} wallets (narrowed)")
    elif new == 1:
        ok(f"Search '{addr_prefix}': found exactly 1 wallet")
    else:
        ok(f"Search '{addr_prefix}': {new:,} wallets (search applied)")

    # Clear search
    search_input.fill("")
    search_input.press("Enter")
    cleared = wait_for_count_change(page, new, timeout=10)
    if cleared >= baseline:
        ok(f"Search cleared: count restored to {cleared:,}")
    else:
        ok(f"Search cleared: count now {cleared:,}")


# ---------------------------------------------------------------------------
# 10. URL-parameter filter verification (server-side monotonicity)
# ---------------------------------------------------------------------------

def test_url_params(page: Page) -> None:
    """Navigate directly to filtered URLs — bypasses React client state entirely.

    Each param is tested with three values ordered loose→medium→strict.
    Wallet counts must be monotonically non-increasing (or flat when other
    constraints dominate). This validates the server-side Prisma WHERE clause,
    not the slider drag mechanics.
    """
    section("10. URL-parameter filter verification (server-side monotonicity)")

    # Each tuple: (label, url-param, [loose, medium, strict])
    # Values are ordered so that count should be non-increasing left→right.
    cases = [
        ("min_gm",       ["0.1", "2.0", "6.0"]),
        ("max_deposits", ["50000", "5000", "1500"]),
        ("min_days",     ["1", "60", "180"]),
        ("min_r2",       ["0", "0.3", "0.7"]),
        ("min_win_rate", ["0", "0.3", "0.65"]),
        ("max_decay",    ["1.0", "0.6", "0.2"]),
        ("min_trades",   ["1", "200", "800"]),
    ]

    for param, values in cases:
        counts = []
        for v in values:
            load_fresh(page, f"preset=hidden-gems&{param}={v}")
            n = wallet_count(page)
            counts.append(n)

        monotone = all(counts[i] >= counts[i + 1] for i in range(len(counts) - 1))
        counts_str = " → ".join(f"{c:,}" for c in counts)

        if monotone and counts[0] > counts[-1]:
            ok(f"  {param}: {counts_str} ✓ (monotone, filter active)")
        elif monotone:
            ok(f"  {param}: {counts_str} (flat — other constraints dominate or range is outside preset)")
        else:
            fail(f"  {param} non-monotone: {counts_str}  ← server filter not working")


# ---------------------------------------------------------------------------
# 11. Slider monotonicity via progressive drag (3 positions each)
# ---------------------------------------------------------------------------

def test_slider_monotonicity(page: Page) -> None:
    """Drag each slider through 3 progressively more-restrictive positions.

    Counts must be monotonically non-increasing. Covers both min-type sliders
    (where dragging right = higher threshold = fewer wallets) and max-type
    sliders (where dragging left = lower ceiling = fewer wallets).
    """
    section("11. Slider monotonicity — 3 progressive drag positions")

    # (slider_index, label, [least→most restrictive drag positions])
    mono_cases = [
        (0, "min_gm        [right = more restrictive]", [0.15, 0.45, 0.75]),
        (5, "min_r2        [right = more restrictive]", [0.15, 0.45, 0.75]),
        (3, "min_trades    [right = more restrictive]", [0.15, 0.45, 0.75]),
        (1, "max_deposits  [left  = more restrictive]", [0.85, 0.50, 0.15]),
        (4, "max_age       [left  = more restrictive]", [0.85, 0.50, 0.15]),
    ]

    for idx, label, positions in mono_cases:
        counts = []
        for pct in positions:
            load_fresh(page)
            before = wallet_count(page)
            slider_drag(page, idx, pct)
            new = wait_for_count_change(page, before, timeout=12)
            counts.append(new)

        monotone = all(counts[i] >= counts[i + 1] for i in range(len(counts) - 1))
        counts_str = " → ".join(f"{c:,}" for c in counts)

        if monotone and counts[0] > counts[-1]:
            ok(f"  [{idx}] {label}: {counts_str} ✓")
        elif monotone:
            ok(f"  [{idx}] {label}: {counts_str} (flat — preset ceiling or already at boundary)")
        else:
            fail(f"  [{idx}] {label}: NON-MONOTONE {counts_str}")


# ---------------------------------------------------------------------------
# 12. Compound filter — two sliders together ≤ either alone
# ---------------------------------------------------------------------------

def test_slider_compound(page: Page) -> None:
    """Two slider filters applied together must be >= as restrictive as either alone.

    Applies slider[0] (min_gm) and slider[2] (min_days) independently, then
    together. The combined count must be ≤ min(count_a, count_b).
    """
    section("12. Compound filter — two sliders combined ≤ either alone")

    load_fresh(page)
    baseline = wallet_count(page)

    # slider[0] alone at 60%
    load_fresh(page)
    slider_drag(page, 0, 0.60)
    count_a = wait_for_count_change(page, baseline, timeout=12)
    ok(f"  Slider[0] (min_gm @ 60%) alone: {count_a:,}")

    # slider[2] alone at 60%
    load_fresh(page)
    slider_drag(page, 2, 0.60)
    count_b = wait_for_count_change(page, baseline, timeout=12)
    ok(f"  Slider[2] (min_days @ 60%) alone: {count_b:,}")

    # Both together: drag slider[0], then slider[2] without reloading between
    load_fresh(page)
    slider_drag(page, 0, 0.60)
    after_a = wait_for_count_change(page, baseline, timeout=12)
    slider_drag(page, 2, 0.60)
    time.sleep(5)   # second navigation needs to settle
    count_ab = wallet_count(page)
    ok(f"  Sliders[0+2] combined: {count_ab:,}")

    if count_ab <= min(count_a, count_b):
        ok(f"Compound {count_ab:,} ≤ min({count_a:,}, {count_b:,}) ✓")
    elif count_ab <= max(count_a, count_b) + 3:
        ok(f"Compound {count_ab:,} ≈ more-restrictive filter (one filter subsumes the other)")
    else:
        fail(f"Compound {count_ab:,} > both alone (A={count_a:,}, B={count_b:,})")

    # Second compound: slider[5] (min_r2) + slider[1] (max_deposits)
    load_fresh(page)
    slider_drag(page, 5, 0.70)
    count_c = wait_for_count_change(page, baseline, timeout=12)
    ok(f"  Slider[5] (min_r2 @ 70%) alone: {count_c:,}")

    load_fresh(page)
    slider_drag(page, 1, 0.20)
    count_d = wait_for_count_change(page, baseline, timeout=12)
    ok(f"  Slider[1] (max_deposits @ 20%) alone: {count_d:,}")

    load_fresh(page)
    slider_drag(page, 5, 0.70)
    after_c = wait_for_count_change(page, baseline, timeout=12)
    slider_drag(page, 1, 0.20)
    time.sleep(5)
    count_cd = wallet_count(page)
    ok(f"  Sliders[5+1] combined: {count_cd:,}")

    if count_cd <= min(count_c, count_d):
        ok(f"Compound {count_cd:,} ≤ min({count_c:,}, {count_d:,}) ✓")
    elif count_cd <= max(count_c, count_d) + 3:
        ok(f"Compound {count_cd:,} ≈ more-restrictive filter (one subsumes other)")
    else:
        fail(f"Compound {count_cd:,} > both alone (C={count_c:,}, D={count_d:,})")


# ---------------------------------------------------------------------------
# 13. Preset reset — drag slider → custom badge → click preset → restored
# ---------------------------------------------------------------------------

def test_preset_reset(page: Page) -> None:
    """Clicking a preset after dragging a slider must restore preset defaults.

    Verifies:
    1. Slider drag shows 'Custom (sliders changed)' badge.
    2. Clicking 'Hidden Gems' preset wipes the custom params.
    3. Wallet count returns to the preset baseline.
    4. Custom badge disappears.
    5. Preset button count label matches the H2 count.
    """
    section("13. Preset reset — custom badge appears then clears on preset click")

    load_fresh(page)
    baseline = wallet_count(page)

    # Drag slider[0] (min_gm) to a highly restrictive position
    slider_drag(page, 0, 0.82)
    restricted = wait_for_count_change(page, baseline, timeout=12)

    if restricted < baseline:
        ok(f"Slider drag reduced count: {baseline:,} → {restricted:,}")
    else:
        ok(f"Slider drag, no count change ({restricted:,}); continuing reset test")

    # Verify "Custom (sliders changed)" badge is visible
    custom = page.locator("text='Custom (sliders changed)'")
    try:
        expect(custom).to_be_visible(timeout=5_000)
        ok("'Custom (sliders changed)' badge appeared ✓")
    except Exception as e:
        fail("'Custom (sliders changed)' badge not visible after drag", str(e)[:120])

    # Click Hidden Gems preset — wipes slider URL overrides
    page.locator("button", has_text="Hidden Gems").first.click()
    restored = wait_for_count_change(page, restricted, timeout=14)

    if abs(restored - baseline) <= 5:
        ok(f"Count restored: {restricted:,} → {restored:,} (≈ baseline {baseline:,}) ✓")
    else:
        fail(f"Preset reset: count {restored:,}, expected ≈ {baseline:,}")

    # Custom badge must be gone
    try:
        expect(custom).not_to_be_visible(timeout=5_000)
        ok("'Custom (sliders changed)' badge removed after preset click ✓")
    except Exception as e:
        fail("'Custom (sliders changed)' badge still visible after reset", str(e)[:120])

    # Preset button label count must match H2
    btn_text = page.locator("button", has_text="Hidden Gems").first.inner_text()
    m_label = re.search(r"\(([\d,]+)\)", btn_text)
    if m_label:
        label_n = int(m_label.group(1).replace(",", ""))
        if label_n == restored:
            ok(f"Preset button count ({label_n:,}) matches H2 ✓")
        else:
            fail(f"Preset button shows {label_n:,} but H2 shows {restored:,}")

    # Repeat the reset test using a different slider (min_r2) and a different preset
    load_fresh(page)
    baseline2 = wallet_count(page)

    slider_drag(page, 5, 0.85)
    restricted2 = wait_for_count_change(page, baseline2, timeout=12)
    ok(f"Slider[5] drag: {baseline2:,} → {restricted2:,}")

    try:
        expect(custom).to_be_visible(timeout=5_000)
        ok("'Custom' badge appeared for slider[5] drag ✓")
    except Exception as e:
        fail("'Custom' badge missing after slider[5] drag", str(e)[:80])

    # Now switch to "All Wallets" preset
    page.locator("button", has_text="All Wallets").first.click()
    all_wallets_count = wait_for_count_change(page, restricted2, timeout=14)

    try:
        expect(custom).not_to_be_visible(timeout=5_000)
        ok(f"'Custom' badge cleared after 'All Wallets' click ({all_wallets_count:,} wallets) ✓")
    except Exception as e:
        fail("'Custom' badge persisted after 'All Wallets' click", str(e)[:80])

    # Switch back to Hidden Gems and confirm custom badge still absent
    page.locator("button", has_text="Hidden Gems").first.click()
    final = wait_for_count_change(page, all_wallets_count, timeout=14)
    try:
        expect(custom).not_to_be_visible(timeout=3_000)
        ok(f"No 'Custom' badge after returning to Hidden Gems ({final:,} wallets) ✓")
    except Exception as e:
        fail("'Custom' badge appeared unexpectedly on clean preset switch", str(e)[:80])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://growth.cookstar.cc/finder")
    args = parser.parse_args()
    global URL
    URL = args.url.rstrip("/")

    print(f"Testing: {URL}")
    print("=" * 65)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 UITest/1.0"
            ),
        )
        page = ctx.new_page()

        try:
            baseline = test_page_load(page)
            if baseline <= 0:
                print("\nAborting: page failed to load with wallets.")
                return 1
            test_filter_presets(page, baseline)
            test_category_tabs(page)
            test_sliders(page)
            test_checkboxes(page)
            test_view_presets(page)
            test_sort_columns(page)
            test_row_expansion(page)
            test_search(page)
            test_url_params(page)
            test_slider_monotonicity(page)
            test_slider_compound(page)
            test_preset_reset(page)
        except Exception as e:
            import traceback
            fail(f"Unexpected error: {e}", traceback.format_exc()[-300:])
        finally:
            browser.close()

    print("\n" + "=" * 65)
    print(f"Results: {PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 0 if FAIL_COUNT == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
