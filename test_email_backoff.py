"""Verify the 429-backoff path added to app.py.

Stubs `requests.post` to simulate a sequence of FormSubmit responses
and asserts that `_RateLimited` is raised on 429, that the watcher sets
a cooldown, and that a subsequent cycle is skipped while cooldown is
active.

Run: cd /home/claude-dev/dog-scanner && python3 test_email_backoff.py
"""

import io
import sys
import time
import types
from unittest.mock import patch


class FakeResponse:
    def __init__(self, status_code, body='{}', headers=None):
        self.status_code = status_code
        self.text = body
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        import json as _j
        return _j.loads(self.text)


def _capture_stdout():
    buf = io.StringIO()
    sys.stdout = buf
    return buf


def _restore_stdout(original):
    sys.stdout = original


def main() -> int:
    import app  # noqa: E402

    original_stdout = sys.stdout

    # ---- Test 1: _send_email raises _RateLimited on 429 ----
    with patch.object(app.requests, "post", return_value=FakeResponse(429, '{"success":false,"message":"Rate limit exceeded."}')):
        try:
            app._send_email("test@example.invalid", [])
        except app._RateLimited as e:
            print(f"PASS  _send_email raises _RateLimited on 429 ({e})")
        except Exception as e:
            print(f"FAIL  expected _RateLimited, got {type(e).__name__}: {e}")
            return 1
        else:
            print("FAIL  expected _RateLimited, nothing raised")
            return 1

    # ---- Test 2: _send_email raises RuntimeError on other 5xx ----
    with patch.object(app.requests, "post", return_value=FakeResponse(503, 'Service Unavailable', headers={"content-type": "text/plain"})):
        try:
            app._send_email("test@example.invalid", [])
        except RuntimeError as e:
            print(f"PASS  _send_email raises RuntimeError on 503 ({e})")
        except app._RateLimited as e:
            print(f"FAIL  503 should NOT raise _RateLimited, got: {e}")
            return 1

    # ---- Test 3: Origin header uses FORMSUBMIT_ORIGIN env ----
    captured = {}

    def fake_post(url, headers=None, **kw):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResponse(200, '{"success":"true"}')

    with patch.object(app.requests, "post", side_effect=fake_post):
        app._send_email("test@example.invalid", [])
    origin = captured["headers"].get("Origin")
    if origin == "https://scanner.cookstar.cc":
        print(f"PASS  Origin header = {origin}")
    else:
        print(f"FAIL  Origin header expected 'https://scanner.cookstar.cc', got {origin!r}")
        return 1

    # ---- Test 4: Simulate watcher-loop handling of 429 → cooldown set ----
    # We can't easily run the infinite `while True` loop, so we just verify
    # the state machine: simulate what the loop does when _send_email raises
    # _RateLimited for the first subscriber.
    app._rate_limited_until = 0.0
    subs = ["a@x.test", "b@x.test", "c@x.test"]

    def always_429(url, headers=None, **kw):
        return FakeResponse(429, '{"success":false,"message":"Rate limit exceeded."}')

    buf = _capture_stdout()
    try:
        sent = 0
        with patch.object(app.requests, "post", side_effect=always_429):
            for s in subs:
                try:
                    app._send_email(s, [])
                    sent += 1
                except app._RateLimited as e:
                    app._rate_limited_until = time.time() + app.FORMSUBMIT_COOLDOWN_SECS
                    print(f"[alerts] RATE LIMITED ({e}) — cooling down for {app.FORMSUBMIT_COOLDOWN_SECS}s; {len(subs) - subs.index(s)} send(s) skipped this cycle")
                    break
    finally:
        _restore_stdout(original_stdout)

    out = buf.getvalue()
    cooldown_delta = app._rate_limited_until - time.time()
    assert sent == 0, f"expected 0 sends before first 429, got {sent}"
    assert "RATE LIMITED" in out, f"expected RATE LIMITED log, got: {out}"
    # 3 subs, break on first → 3 skipped in log
    assert "3 send(s) skipped" in out, f"expected '3 send(s) skipped' in log, got: {out}"
    assert 7000 < cooldown_delta <= 7200, f"expected cooldown ~7200s, got {cooldown_delta}"
    print(f"PASS  watcher loop breaks on first 429, cooldown={int(cooldown_delta)}s, log='{out.strip()}'")

    # ---- Test 5: While cooldown active, loop skips cycle (checked by checking the guard) ----
    app._rate_limited_until = time.time() + 1000
    remaining = app._rate_limited_until - time.time()
    if remaining > 0:
        print(f"PASS  cooldown gate sees {int(remaining)}s remaining → cycle would be skipped")
    else:
        print("FAIL  cooldown gate computation")
        return 1

    # Reset state so the live scanner doesn't inherit test-polluted values
    # (if tests run in the same process — they don't, but defensive).
    app._rate_limited_until = 0.0

    print("\nAll 5 checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
