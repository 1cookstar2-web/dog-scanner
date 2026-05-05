"""Gradio web UI for the dog scanner.

Scanner now uses DDG fallback when Spaniel Aid origin 403s — see scanner.py."""

import fcntl
import html
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Force line-buffered stdout so watcher print() messages appear in tail'd logs
# immediately, even when redirected to a file (default is block-buffered).
sys.stdout.reconfigure(line_buffering=True)

import gradio as gr
import requests

# Load .env before importing scanner so ANTHROPIC_API_KEY is available.
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

from scanner import scan, scan_iter


# ------------------------------------------------------------
# Email-alert subscription + hourly watcher
# ------------------------------------------------------------

STATE_DIR = Path(__file__).resolve().parent / "state"
STATE_DIR.mkdir(exist_ok=True)
SUBS_FILE = STATE_DIR / "subscribers.json"
KNOWN_FILE = STATE_DIR / "known_dogs.json"
FIRST_SEEN_FILE = STATE_DIR / "first_seen.json"
PREV_SCAN_FILE = STATE_DIR / "prev_scan_urls.json"
HEARTBEAT_FILE = STATE_DIR / "watcher_heartbeat.json"
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL_SECS", "600"))
_NEW_WINDOW = timedelta(hours=24)

# When FormSubmit responds 429 we back off entirely for this many seconds
# rather than re-hammering every hour — continued attempts while throttled
# extend the throttle. Resets on the first successful send after cooldown.
FORMSUBMIT_COOLDOWN_SECS = int(os.environ.get("FORMSUBMIT_COOLDOWN_SECS", "7200"))

# FormSubmit keys its activation state on the Origin header, so this must
# match the domain the subscriber ultimately clicked "Activate" from. Using
# the real production domain (not the placeholder example.com) also makes
# the request look legitimate to FormSubmit's anti-abuse heuristics.
FORMSUBMIT_ORIGIN = os.environ.get("FORMSUBMIT_ORIGIN", "https://scanner.cookstar.cc")

# Transport selection. One of: formsubmit | brevo | resend.
# Defaults to formsubmit for backward-compat with existing subscribers;
# switch to brevo or resend by setting EMAIL_TRANSPORT in the env.
EMAIL_TRANSPORT = os.environ.get("EMAIL_TRANSPORT", "formsubmit").strip().lower()

# --- Brevo (api.brevo.com) ---
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip()
# The "from" address must be a verified sender in the Brevo account. On free
# tier, verify one personal email (e.g. your Gmail) with one click.
BREVO_FROM_EMAIL = os.environ.get("BREVO_FROM_EMAIL", "").strip()
BREVO_FROM_NAME = os.environ.get("BREVO_FROM_NAME", "Dog Scanner").strip()

# --- Resend (api.resend.com) ---
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
# The "from" must be on a verified domain (3 DNS records). Use
# onboarding@resend.dev only for smoke tests — production sends from there
# land in spam.
RESEND_FROM = os.environ.get("RESEND_FROM", "Dog Scanner <onboarding@resend.dev>").strip()

# --- Telegram ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def subscribe(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        return "Please enter a valid email address."
    subs = set(_load_json(SUBS_FILE, []))
    safe = html.escape(email)
    if email in subs:
        return f"**{safe}** is already subscribed."
    subs.add(email)
    _save_json_atomic(SUBS_FILE, sorted(subs))
    return (
        f"Subscribed **{safe}** — you'll get an email whenever a new "
        f"matching dog is listed (checked hourly)."
    )


def _build_message(new_dogs) -> str:
    """Single plain-text message body. FormSubmit silently drops payloads
    using _template:"table" with many dynamic fields, but reliably delivers
    a `message` field containing a newline-bulleted list. URLs on their own
    line are auto-linkified by Gmail/Outlook/Apple Mail."""
    lines = [
        f"{len(new_dogs)} new matching dog(s) have been listed since the last check:",
        "",
    ]
    for d in new_dogs:
        dist = f"{d.distance_miles:g} mi" if d.distance_miles else "~distance unknown"
        name = d.name + (" [RESERVED]" if d.reserved else "")
        bits = [d.breed]
        if d.age:
            bits.append(d.age)
        if d.sex:
            bits.append(d.sex)
        if d.location:
            bits.append(d.location)
        lines.append(f"• {name} ({d.source}, {dist})")
        lines.append(f"  {' · '.join(bits)}")
        lines.append(f"  {d.url}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _subject_for(new_dogs) -> str:
    """Include the dog name(s) so the subject varies per send. FormSubmit
    and Gmail silently drop messages with a repeatedly-identical subject
    from the same sender, so a stable "N new dog(s)" subject stops
    delivering after a few cycles."""
    names = [d.name for d in new_dogs]
    if len(names) == 1:
        return f"Dog Scanner: {names[0]} ({new_dogs[0].source})"
    head = ", ".join(names[:2])
    tail = f" +{len(names) - 2} more" if len(names) > 2 else ""
    return f"Dog Scanner: {len(names)} new — {head}{tail}"


class _RateLimited(Exception):
    """Raised when a transport returns 429. The watcher loop treats this as a
    stop-signal for the current cycle and engages a cooldown so we don't keep
    hammering a throttled endpoint (which can extend the throttle)."""


def _send_via_formsubmit(to_addr: str, subject: str, body: str) -> None:
    r = requests.post(
        f"https://formsubmit.co/ajax/{to_addr}",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # FormSubmit binds activation to Origin — must match the value
            # used on the initial activation POST or it will silently
            # re-request activation.
            "Origin": FORMSUBMIT_ORIGIN,
            "Referer": FORMSUBMIT_ORIGIN.rstrip("/") + "/",
        },
        json={"_subject": subject, "message": body},
        timeout=30,
    )
    if r.status_code == 429:
        raise _RateLimited(f"FormSubmit 429 for {to_addr}: {r.text[:120]}")
    if r.status_code >= 300:
        raise RuntimeError(f"FormSubmit error {r.status_code}: {r.text}")
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if data.get("success") == "false":
        raise RuntimeError(f"FormSubmit rejected: {data.get('message')}")


def _send_via_brevo(to_addr: str, subject: str, body: str) -> None:
    if not BREVO_API_KEY:
        raise RuntimeError("EMAIL_TRANSPORT=brevo but BREVO_API_KEY is unset")
    if not BREVO_FROM_EMAIL:
        raise RuntimeError("EMAIL_TRANSPORT=brevo but BREVO_FROM_EMAIL is unset (must be a verified Brevo sender)")
    r = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "accept": "application/json",
            "api-key": BREVO_API_KEY,
            "content-type": "application/json",
        },
        json={
            "sender": {"name": BREVO_FROM_NAME, "email": BREVO_FROM_EMAIL},
            "to": [{"email": to_addr}],
            "subject": subject,
            "textContent": body,
        },
        timeout=30,
    )
    if r.status_code == 429:
        raise _RateLimited(f"Brevo 429 for {to_addr}: {r.text[:200]}")
    if r.status_code >= 300:
        raise RuntimeError(f"Brevo error {r.status_code}: {r.text}")
    # Brevo returns 201 Created + {"messageId": "..."} on success.


def _send_via_resend(to_addr: str, subject: str, body: str) -> None:
    if not RESEND_API_KEY:
        raise RuntimeError("EMAIL_TRANSPORT=resend but RESEND_API_KEY is unset")
    r = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": RESEND_FROM,
            "to": [to_addr],
            "subject": subject,
            "text": body,
        },
        timeout=30,
    )
    if r.status_code == 429:
        raise _RateLimited(f"Resend 429 for {to_addr}: {r.text[:200]}")
    if r.status_code >= 300:
        raise RuntimeError(f"Resend error {r.status_code}: {r.text}")
    # Resend returns 200 + {"id": "..."} on success.


_TRANSPORTS = {
    "formsubmit": _send_via_formsubmit,
    "brevo": _send_via_brevo,
    "resend": _send_via_resend,
}


def _send_email(to_addr: str, new_dogs) -> None:
    subject = _subject_for(new_dogs)
    body = _build_message(new_dogs)
    transport = _TRANSPORTS.get(EMAIL_TRANSPORT)
    if transport is None:
        raise RuntimeError(f"Unknown EMAIL_TRANSPORT={EMAIL_TRANSPORT!r}; valid: {sorted(_TRANSPORTS)}")
    transport(to_addr, subject, body)


def _send_telegram(new_dogs) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    lines = [f"🐕 {len(new_dogs)} new matching dog(s) found!\n"]
    for d in new_dogs:
        dist = f"{d.distance_miles:g} mi" if d.distance_miles is not None else "dist unknown"
        lines.append(f"• {d.name} ({d.breed}) — {dist} — {d.source}")
        lines.append(f"  {d.url}")
    import urllib.request, urllib.parse
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


WATCHER_THREAD_NAME = "dog-watcher"
WATCHER_LOCK_FILE = STATE_DIR / "watcher.lock"
_state_lock = threading.Lock()


def _acquire_watcher_lock():
    """Exclusive, non-blocking flock on state/watcher.lock. Held for the
    lifetime of the process so two scanners can't run against the same
    state dir and both fire "new dog" emails for the same URL."""
    fd = open(WATCHER_LOCK_FILE, "w")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fd.close()
        return None
    fd.write(f"{os.getpid()}\n")
    fd.flush()
    return fd


def _save_json_atomic(path: Path, data) -> None:
    """Write JSON via tempfile + rename so concurrent readers never see a
    torn file and a crash mid-write can't corrupt the existing state."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _update_first_seen(urls) -> dict:
    """Record first-seen timestamps for any URLs not already tracked.

    Thread-safe: acquires _state_lock so concurrent watcher and UI scans
    don't race on first_seen.json. Returns the full updated dict."""
    with _state_lock:
        data = _load_json(FIRST_SEEN_FILE, {})
        now_iso = datetime.now(timezone.utc).isoformat()
        changed = False
        for url in urls:
            if url not in data:
                data[url] = now_iso
                changed = True
        if changed:
            _save_json_atomic(FIRST_SEEN_FILE, data)
        return data


def _is_new(url: str, first_seen: dict) -> bool:
    ts = first_seen.get(url)
    if not ts:
        return False
    try:
        seen_at = datetime.fromisoformat(ts)
        if seen_at.tzinfo is None:
            seen_at = seen_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - seen_at < _NEW_WINDOW
    except ValueError:
        return False


_rate_limited_until = 0.0


def _watcher_loop() -> None:
    global _rate_limited_until
    time.sleep(10)
    while True:
        try:
            remaining = _rate_limited_until - time.time()
            if remaining > 0:
                # We hit a 429 recently. Skip the scan entirely — re-hammering
                # FormSubmit while throttled just extends the throttle.
                print(f"[alerts] {EMAIL_TRANSPORT} cooldown active — skipping cycle ({int(remaining)}s until retry)")
                time.sleep(SCAN_INTERVAL)
                continue
            dogs = scan()
            current_urls = {d.url for d in dogs}

            # ── UI "New Dogs" tracking ─────────────────────────────────────
            # A dog is "new" only if (a) it appeared this cycle but not in
            # the previous one AND (b) we've never seen it before in any
            # cycle (i.e. not in cumulative known_dogs.json). The second
            # guard eliminates false positives from transient adapter flaps:
            # when an adapter returns [] for one cycle (network blip), its
            # URLs drop out of prev_scan_urls, then recover next cycle and
            # would otherwise be flagged "new" despite being weeks-old
            # listings. Trade-off: relistings (same URL truly returning
            # after rehoming) are not re-flagged. Acceptable — relistings
            # are rare, adapter flaps are common.
            prev_scan_urls = set(_load_json(PREV_SCAN_FILE, None) or [])
            known_pre = set(_load_json(KNOWN_FILE, []))
            if prev_scan_urls:
                ui_new_urls = (current_urls - prev_scan_urls) - known_pre
            else:
                ui_new_urls = set()
            _save_json_atomic(PREV_SCAN_FILE, sorted(current_urls))
            if ui_new_urls:
                _update_first_seen(ui_new_urls)
                print(f"[new] {len(ui_new_urls)} URL(s) first seen this cycle")

            # ── Email-alert tracking (cumulative dedup) ────────────────────
            # known_dogs.json only grows — ensures we never email about the
            # same dog twice even across service restarts.
            with _state_lock:
                known = set(_load_json(KNOWN_FILE, []))
                if not known:
                    _save_json_atomic(KNOWN_FILE, sorted(current_urls))
                    print(f"[alerts] seeded known-dog state with {len(current_urls)} URLs (no emails sent)")
                    new = []
                else:
                    new = [d for d in dogs if d.url not in known]
                    if new:
                        # Mark new dogs as known BEFORE emailing — if the
                        # email fails we'd rather drop a notification than
                        # send it twice on the next cycle.
                        _save_json_atomic(KNOWN_FILE, sorted(known | current_urls))
            if new:
                subs = _load_json(SUBS_FILE, [])
                print(f"[alerts] {len(new)} new dog(s), {len(subs)} subscriber(s)")
                try:
                    _send_telegram(new)
                    print(f"[alerts] telegram sent ({len(new)} dog(s))")
                except Exception as e:
                    print(f"[alerts] telegram failed: {e}")
                for s in subs:
                    try:
                        _send_email(s, new)
                        print(f"[alerts] emailed {s} ({len(new)} dog(s))")
                    except _RateLimited as e:
                        _rate_limited_until = time.time() + FORMSUBMIT_COOLDOWN_SECS
                        print(f"[alerts] RATE LIMITED ({e}) — cooling down for {FORMSUBMIT_COOLDOWN_SECS}s; {len(subs) - subs.index(s)} send(s) skipped this cycle")
                        break
                    except Exception as e:
                        print(f"[alerts] email to {s} failed: {e}")
            elif known:
                print("[alerts] no new dogs this cycle")

            # Heartbeat: external monitors read this file to detect a hung
            # watcher. If the timestamp is older than ~2x SCAN_INTERVAL,
            # something is wrong and the process should be restarted.
            _save_json_atomic(HEARTBEAT_FILE, {
                "last_cycle_utc": datetime.now(timezone.utc).isoformat(),
                "pid": os.getpid(),
                "dogs_in_scan": len(dogs),
                "new_urls": len(ui_new_urls),
                "interval_secs": SCAN_INTERVAL,
            })
        except Exception as e:
            print(f"[alerts] loop error: {e}")
        time.sleep(SCAN_INTERVAL)


def _start_watcher() -> None:
    # Guard against Gradio's auto-reload: module-level flags reset on
    # re-import, but threading.enumerate() sees threads from the previous
    # module load that are still alive. Skip starting if one exists.
    for t in threading.enumerate():
        if t.name == WATCHER_THREAD_NAME and t.is_alive():
            print(f"[alerts] watcher already running (tid={t.ident}); skipping restart")
            return
    lock_fd = _acquire_watcher_lock()
    if lock_fd is None:
        print(f"[alerts] another scanner already holds {WATCHER_LOCK_FILE}; watcher disabled")
        return
    # Retain fd reference so the lock isn't released until process exit.
    globals()["_watcher_lock_fd"] = lock_fd
    t = threading.Thread(target=_watcher_loop, daemon=True, name=WATCHER_THREAD_NAME)
    t.start()
    print(f"[alerts] watcher thread started (interval={SCAN_INTERVAL}s, transport={EMAIL_TRANSPORT}, pid={os.getpid()})")


# ------------------------------------------------------------
# UI
# ------------------------------------------------------------

def _progress_html(pct: int, label: str) -> str:
    pct = max(0, min(100, int(pct)))
    return (
        '<div class="scan-progress">'
        f'  <div class="scan-progress__label">{html.escape(label)}</div>'
        '  <div class="scan-progress__track">'
        f'    <div class="scan-progress__fill" style="width:{pct}%"></div>'
        f'    <div class="scan-progress__pct">{pct}%</div>'
        '  </div>'
        '</div>'
    )


def _prep_scan():
    # First, fast, synchronous hop: paints the 0% bar before the slow
    # scan (~70 adapters) has even spun up its thread pool.
    return _progress_html(0, "Starting scan…"), 0


def run_scan():
    dogs: list = []
    for name, done, total, dogs_so_far in scan_iter():
        dogs = dogs_so_far
        pct = int(done * 100 / total) if total else 0
        label = (
            f"Scanning UK rescue sites… {done}/{total} done"
            if name is not None and done < total
            else "Finalising results…"
        )
        yield _progress_html(pct, label), len(dogs)

    if not dogs:
        yield "<p>No matching dogs found right now. Try again later.</p>", 0
        return

    # The watcher compares consecutive hourly scans and stamps first_seen.json
    # for URLs that appear in the current scan but not the previous one.
    # The UI reads that file but never writes — only the watcher has the
    # prev-scan baseline needed to determine what is genuinely new.
    first_seen = _load_json(FIRST_SEEN_FILE, {})

    _sort_key = lambda d: (d.distance_miles if d.distance_miles is not None else 99999.0, d.source, d.name.lower())
    in_range = sorted(
        [d for d in dogs if d.in_range and not d.reserved],
        key=_sort_key,
    )
    reserved = sorted(
        [d for d in dogs if d.in_range and d.reserved],
        key=_sort_key,
    )
    uncertain = [d for d in dogs if not d.in_range]
    new_dogs = sorted(
        [d for d in in_range if _is_new(d.url, first_seen)],
        key=_sort_key,
    )

    def _dist_html(d):
        if d.distance_miles is None:
            return '<span style="color:#999">~distance unknown</span>'
        return (
            f'<span style="background:#e8f0fe;color:#1a56c9;'
            f'padding:2px 8px;border-radius:10px;font-weight:600;font-size:0.9em">'
            f'{d.distance_miles:g} mi</span>'
        )

    def row(d, is_new=False):
        flags = []
        if is_new:
            flags.append('<span class="flag flag-new">NEW</span>')
        if d.reserved:
            flags.append('<span class="flag flag-reserved">RESERVED</span>')
        if not d.in_range:
            flags.append('<span class="flag flag-range">range uncertain</span>')
        flag_html = "".join(flags)

        chips = [f'<span class="chip chip-breed">{html.escape(d.breed)}</span>']
        if d.age:
            chips.append(f'<span class="chip"><span class="chip-label">Age</span>{html.escape(d.age)}</span>')
        if d.sex:
            chips.append(f'<span class="chip">{html.escape(d.sex)}</span>')
        if d.location:
            chips.append(f'<span class="chip"><span class="chip-label">Location</span>{html.escape(d.location)}</span>')

        return (
            f'<li class="dog-row">'
            f'<div class="dog-header">'
            f'{_dist_html(d)}'
            f'<a href="{html.escape(d.url)}" target="_blank" rel="noopener" class="dog-name">{html.escape(d.name)}</a>'
            f'<span class="dog-source">{html.escape(d.source)}</span>'
            f'{flag_html}'
            f'</div>'
            f'<div class="dog-meta">{"".join(chips)}</div>'
            f'</li>'
        )

    sections = []
    if new_dogs:
        sections.append(
            f'<div class="new-dogs-section">'
            f"<h3>New Dogs ({len(new_dogs)}) — first seen in last 24 hours</h3>"
            f'<ul class="dog-list">'
            + "".join(row(d, is_new=True) for d in new_dogs)
            + "</ul></div>"
        )
    if in_range:
        sections.append(
            f"<h3>Available ({len(in_range)}) — closest first</h3>"
            f'<ul class="dog-list">'
            + "".join(row(d) for d in in_range)
            + "</ul>"
        )
    if reserved:
        sections.append(
            f"<h3>Reserved ({len(reserved)}) — may come back</h3>"
            f'<ul class="dog-list">'
            + "".join(row(d) for d in reserved)
            + "</ul>"
        )
    if uncertain:
        sections.append(
            f"<h3>Possibly out of range ({len(uncertain)}) — verify on listing</h3>"
            f'<ul class="dog-list">'
            + "".join(row(d) for d in uncertain)
            + "</ul>"
        )

    yield "".join(sections), len(dogs)


MOBILE_CSS = """
/* Mobile-first fixes — Gradio's default Row doesn't stack on narrow screens */
@media (max-width: 640px) {
    .gradio-container { padding: 8px !important; max-width: 100% !important; }
    .row-scan, .row-subscribe {
        flex-direction: column !important;
        gap: 10px !important;
    }
    .row-scan > *, .row-subscribe > * {
        min-width: 100% !important;
        width: 100% !important;
    }
    h1 { font-size: 1.5rem !important; line-height: 1.2 !important; }
    h3 { font-size: 1.05rem !important; margin: 14px 0 8px !important; }
    .prose p { font-size: 0.95rem !important; line-height: 1.45 !important; }
}

/* Bigger tap targets on all touchscreens */
button { min-height: 44px !important; }
input[type="text"], input[type="email"] { min-height: 44px !important; font-size: 16px !important; }

/* Custom scan progress bar — replaces Gradio's default indicator so the
   label never overlaps adjacent components. */
.scan-progress {
    margin: 8px 0 4px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.scan-progress__label {
    font-size: 0.92rem;
    color: #333;
    margin-bottom: 6px;
}
.scan-progress__track {
    position: relative;
    height: 22px;
    background: #eef1f6;
    border-radius: 11px;
    overflow: hidden;
    box-shadow: inset 0 1px 2px rgba(0,0,0,0.08);
}
.scan-progress__fill {
    height: 100%;
    background-image:
        linear-gradient(
            45deg,
            rgba(255,255,255,0.22) 25%, transparent 25%,
            transparent 50%, rgba(255,255,255,0.22) 50%,
            rgba(255,255,255,0.22) 75%, transparent 75%, transparent
        ),
        linear-gradient(90deg, #4f6bed 0%, #7c5cff 100%);
    background-size: 24px 24px, 100% 100%;
    border-radius: 11px;
    transition: width 0.35s ease;
    animation: scan-progress-stripes 1.2s linear infinite;
}
@keyframes scan-progress-stripes {
    from { background-position: 0 0, 0 0; }
    to   { background-position: 24px 0, 0 0; }
}
.scan-progress__pct {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #1a2240;
    font-weight: 600;
    font-size: 0.85rem;
    text-shadow: 0 1px 1px rgba(255,255,255,0.45);
}

/* Dog-list rows */
.dog-list { list-style: none; padding-left: 0; margin: 0; }
.dog-row {
    padding: 14px 2px;
    border-bottom: 1px solid #eee;
    word-wrap: break-word;
    overflow-wrap: break-word;
}
.dog-row:last-child { border-bottom: none; }

.dog-header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px 14px;
    margin-bottom: 10px;
}
.dog-name {
    font-size: 1.15rem;
    font-weight: 600;
    color: #1a56c9;
    text-decoration: none;
    word-break: break-word;
    padding: 4px 0;
}
.dog-name:hover { text-decoration: underline; }
.dog-source {
    font-size: 0.85rem;
    color: #555;
    padding: 3px 9px;
    background: #f3f4f6;
    border-radius: 6px;
}

.flag {
    font-size: 0.8rem;
    padding: 2px 9px;
    border-radius: 10px;
    font-weight: 700;
    letter-spacing: 0.02em;
}
.flag-reserved { background: #fff3cd; color: #8a6100; }
.flag-range    { background: #eee;    color: #555; }
.flag-new      { background: #dcfce7; color: #15803d; }

/* New-dogs section — subtle green left rule to separate it visually */
.new-dogs-section {
    border-left: 4px solid #16a34a;
    padding-left: 14px;
    margin-bottom: 8px;
}
.new-dogs-section h3 { color: #15803d; }
.new-dogs-section .dog-row { border-bottom-color: #d1fae5; }

.dog-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 10px;
    margin-left: 0;
}
.chip {
    font-size: 0.95rem;
    color: #1f2937;
    background: #f3f4f6;
    padding: 6px 12px;
    border-radius: 7px;
    line-height: 1.4;
    white-space: normal;
    display: inline-flex;
    align-items: baseline;
    gap: 8px;
}
.chip-label {
    color: #6b7280;
    font-size: 0.78rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}
.chip-breed {
    background: #e8f0fe;
    color: #174291;
    font-weight: 600;
}

@media (max-width: 640px) {
    .dog-name { font-size: 1.05rem; }
    .chip { font-size: 0.92rem; padding: 5px 9px; }
}

/* Light-mode high-contrast pass. Designed for older readers: pure black on
   pure white, heavier weights, larger type. Gated on data-theme="light" which
   a <head> script sets based on (?__theme=) and OS preference — so this
   activates for BOTH forced-light via URL and OS-light. Dark mode untouched. */
html[data-theme="light"] body,
html[data-theme="light"] .gradio-container {
    background: #ffffff !important;
    color: #000000 !important;
    font-size: 17px !important;
}
html[data-theme="light"] html,
html[data-theme="light"] body,
html[data-theme="light"] .gradio-container,
html[data-theme="light"] .gradio-container > *,
html[data-theme="light"] .app,
html[data-theme="light"] .main,
html[data-theme="light"] .wrap,
html[data-theme="light"] .contain,
html[data-theme="light"] .block,
html[data-theme="light"] .form,
html[data-theme="light"] .panel,
html[data-theme="light"] .column,
html[data-theme="light"] .row {
    background-color: #ffffff !important;
}
html[data-theme="light"] body,
html[data-theme="light"] .gradio-container,
html[data-theme="light"] .prose,
html[data-theme="light"] .prose p,
html[data-theme="light"] .prose li,
html[data-theme="light"] p,
html[data-theme="light"] li,
html[data-theme="light"] span,
html[data-theme="light"] div,
html[data-theme="light"] label {
    color: #000000 !important;
}
html[data-theme="light"] .prose p,
html[data-theme="light"] p {
    font-weight: 500 !important;
    line-height: 1.6 !important;
    font-size: 1.05rem !important;
}
html[data-theme="light"] h1 { color: #000 !important; font-weight: 800 !important; font-size: 2rem !important; }
html[data-theme="light"] h2 { color: #000 !important; font-weight: 700 !important; }
html[data-theme="light"] h3 { color: #000 !important; font-weight: 700 !important; font-size: 1.2rem !important; }

html[data-theme="light"] .dog-row {
    border-bottom: 1px solid #9ca3af !important;
    padding: 18px 2px !important;
}
html[data-theme="light"] .dog-name {
    color: #0b3fa3 !important;
    font-weight: 700 !important;
    font-size: 1.3rem !important;
    text-decoration: underline !important;
    text-underline-offset: 3px !important;
    text-decoration-thickness: 1.5px !important;
}
html[data-theme="light"] .dog-name:hover { color: #062a6f !important; }
html[data-theme="light"] .dog-source {
    background: #d1d5db !important;
    color: #000 !important;
    font-weight: 600 !important;
    font-size: 0.95rem !important;
    padding: 4px 10px !important;
    border: 1px solid #6b7280 !important;
}
html[data-theme="light"] .chip {
    background: #ffffff !important;
    color: #000000 !important;
    font-size: 1.05rem !important;
    font-weight: 600 !important;
    padding: 7px 13px !important;
    border: 1.5px solid #4b5563 !important;
}
html[data-theme="light"] .chip-label {
    color: #000 !important;
    font-weight: 800 !important;
    font-size: 0.82rem !important;
}
html[data-theme="light"] .chip-breed {
    background: #c7d7f7 !important;
    color: #0b2e6d !important;
    border: 1.5px solid #0b3fa3 !important;
}
html[data-theme="light"] .flag-reserved {
    background: #fde68a !important; color: #713f12 !important;
    border: 1.5px solid #a16207 !important; font-weight: 700 !important;
}
html[data-theme="light"] .flag-range {
    background: #ffffff !important; color: #000 !important;
    border: 1.5px solid #4b5563 !important; font-weight: 700 !important;
}
html[data-theme="light"] .flag-new {
    background: #bbf7d0 !important; color: #14532d !important;
    border: 1.5px solid #16a34a !important; font-weight: 700 !important;
}
html[data-theme="light"] .new-dogs-section {
    border-left-color: #16a34a !important;
}
html[data-theme="light"] .new-dogs-section h3 { color: #14532d !important; }
html[data-theme="light"] .new-dogs-section .dog-row { border-bottom-color: #6ee7b7 !important; }
html[data-theme="light"] button.primary,
html[data-theme="light"] button[class*="primary"] {
    background: linear-gradient(90deg, #3b4fd1 0%, #6a47e8 100%) !important;
    color: #fff !important;
    border: none !important;
    font-weight: 700 !important;
    font-size: 1.1rem !important;
}
html[data-theme="light"] input,
html[data-theme="light"] textarea,
html[data-theme="light"] select {
    color: #000 !important;
    background: #ffffff !important;
    border: 1.5px solid #4b5563 !important;
    font-size: 16px !important;
    font-weight: 500 !important;
}
html[data-theme="light"] input::placeholder { color: #4b5563 !important; }
html[data-theme="light"] label,
html[data-theme="light"] .label-wrap span,
html[data-theme="light"] [data-testid="block-label"] {
    color: #000 !important;
    font-weight: 700 !important;
}
html[data-theme="light"] .scan-progress__label { color: #000 !important; font-weight: 600 !important; }
html[data-theme="light"] .scan-progress__pct   { color: #fff !important; text-shadow: 0 1px 2px rgba(0,0,0,0.45) !important; }
html[data-theme="light"] footer,
html[data-theme="light"] footer a,
html[data-theme="light"] footer span,
html[data-theme="light"] footer button {
    color: #1f2937 !important;
    font-weight: 500 !important;
}
"""


THEME_DETECT_SCRIPT = """
<script>
(function(){
    function apply() {
        var p = new URLSearchParams(location.search);
        var forced = p.get('__theme');
        var isDark = forced === 'dark' ||
                     (forced !== 'light' &&
                      window.matchMedia &&
                      window.matchMedia('(prefers-color-scheme: dark)').matches);
        document.documentElement.dataset.theme = isDark ? 'dark' : 'light';
    }
    apply();
    // Re-apply if user flips OS preference live and we're not forced.
    if (window.matchMedia) {
        try {
            window.matchMedia('(prefers-color-scheme: dark)')
                .addEventListener('change', apply);
        } catch(e){}
    }
})();
(function(){
    // Hard-reload on server-side file changes. Gradio's built-in dev_mode
    // reconnect fetches /config but doesn't re-render already-mounted
    // components reliably; a full page reload is reliable and ~2s end-to-end.
    if (!window.EventSource) return;
    try {
        var es = new EventSource('/gradio_api/dev/reload');
        es.addEventListener('reload', function(){
            setTimeout(function(){ window.location.reload(); }, 250);
        });
    } catch(e){}
})();
</script>
"""

with gr.Blocks(
    title="Dog Scanner — Border Collie / Springer near Hayling Island",
) as demo:
    gr.Markdown(
        """
        # Dog Scanner (beta)

        Border Collies, Springer Spaniels, and their crosses within
        ~100 miles of Hayling Island (PO11). Searches 67 UK rescue sites
        and breed specialists — national charities (Dogs Trust, Battersea,
        Blue Cross, NAWT, RSPCA branches), regional rescues across Hampshire,
        Sussex, Surrey, Kent, Berkshire and beyond, and breed specialists
        (Spaniel Aid, ESSW, SYESSR, NESSR, FOSTBC, Border Collie Spot and
        more) — all in one click. Distances shown are great-circle miles from
        PO11; use the listing link to verify exact location.
        """
    )
    with gr.Row(elem_classes=["row-scan"]):
        scan_btn = gr.Button("🔍 Scan", variant="primary", size="lg")
        count = gr.Number(label="Total found", interactive=False, value=0)
    results = gr.HTML(label="Results")
    scan_btn.click(
        fn=_prep_scan,
        inputs=None,
        outputs=[results, count],
        show_progress="hidden",
    ).then(
        fn=run_scan,
        inputs=None,
        outputs=[results, count],
        show_progress="hidden",
    )

    gr.Markdown("### 📬 Get email alerts for new dogs")
    gr.Markdown(
        "Enter your email and the scanner will automatically check every hour "
        "and email you the moment a new matching dog is listed."
    )
    with gr.Row(elem_classes=["row-subscribe"]):
        email_in = gr.Textbox(
            label="Your email",
            placeholder="you@example.com",
            scale=3,
        )
        sub_btn = gr.Button("Subscribe", scale=1, variant="secondary")
    sub_status = gr.Markdown("")
    sub_btn.click(fn=subscribe, inputs=email_in, outputs=sub_status)


demo.queue(max_size=10)

if __name__ == "__main__":
    _start_watcher()
    demo.launch(
        server_name="127.0.0.1",
        server_port=7861,
        theme=gr.themes.Soft(),
        css=MOBILE_CSS,
        head=THEME_DETECT_SCRIPT,
    )


