"""Unit tests for the three email transports.

Runs without network access — every requests.post is patched. Verifies:
- correct endpoint, headers, and JSON body for each transport
- 429 raises _RateLimited (for backoff), other 5xx raise RuntimeError
- EMAIL_TRANSPORT dispatcher routes to the right function
- missing-credential cases raise a clear RuntimeError

Run: cd /home/claude-dev/dog-scanner && python3 test_email_transports.py
"""

import sys
from unittest.mock import patch


class FakeResponse:
    def __init__(self, status_code, body='{}', headers=None):
        self.status_code = status_code
        self.text = body
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        import json as _j
        return _j.loads(self.text)


def passed(msg): print(f"PASS  {msg}")
def failed(msg): print(f"FAIL  {msg}"); sys.exit(1)


def main() -> int:
    import app  # noqa: E402

    subject = "Dog Scanner: test"
    body = "test body"

    # ===== FormSubmit =====
    captured = {}

    def grab(url, headers=None, json=None, **kw):
        captured.clear()
        captured.update(url=url, headers=headers, json=json)
        return FakeResponse(200, '{"success":"true"}')

    with patch.object(app.requests, "post", side_effect=grab):
        app._send_via_formsubmit("dest@example.test", subject, body)
    assert captured["url"] == "https://formsubmit.co/ajax/dest@example.test", captured["url"]
    assert captured["json"] == {"_subject": subject, "message": body}
    assert captured["headers"]["Origin"] == "https://scanner.cookstar.cc"
    passed("formsubmit: correct endpoint/payload/origin")

    with patch.object(app.requests, "post", return_value=FakeResponse(429, '{"success":false,"message":"Rate limit exceeded."}')):
        try:
            app._send_via_formsubmit("d@x.test", subject, body)
            failed("formsubmit: expected _RateLimited on 429")
        except app._RateLimited:
            passed("formsubmit: _RateLimited on 429")

    # ===== Brevo =====
    with patch.dict(app.__dict__, {"BREVO_API_KEY": "", "BREVO_FROM_EMAIL": ""}):
        try:
            app._send_via_brevo("d@x.test", subject, body)
            failed("brevo: missing key should raise")
        except RuntimeError as e:
            assert "BREVO_API_KEY" in str(e), str(e)
            passed(f"brevo: missing BREVO_API_KEY → RuntimeError ({e})")

    with patch.dict(app.__dict__, {"BREVO_API_KEY": "xkeysib-abc", "BREVO_FROM_EMAIL": ""}):
        try:
            app._send_via_brevo("d@x.test", subject, body)
            failed("brevo: missing from should raise")
        except RuntimeError as e:
            assert "BREVO_FROM_EMAIL" in str(e), str(e)
            passed(f"brevo: missing BREVO_FROM_EMAIL → RuntimeError ({e})")

    with patch.dict(app.__dict__, {"BREVO_API_KEY": "xkeysib-test-key", "BREVO_FROM_EMAIL": "sender@x.test", "BREVO_FROM_NAME": "Dog Scanner"}):
        with patch.object(app.requests, "post", side_effect=grab):
            app._send_via_brevo("dest@example.test", subject, body)
        assert captured["url"] == "https://api.brevo.com/v3/smtp/email", captured["url"]
        assert captured["headers"]["api-key"] == "xkeysib-test-key"
        assert captured["json"]["sender"] == {"name": "Dog Scanner", "email": "sender@x.test"}
        assert captured["json"]["to"] == [{"email": "dest@example.test"}]
        assert captured["json"]["subject"] == subject
        assert captured["json"]["textContent"] == body
        passed("brevo: correct endpoint, api-key header, sender/to/subject/textContent")

        with patch.object(app.requests, "post", return_value=FakeResponse(429, '{"code":"too_many_requests"}')):
            try:
                app._send_via_brevo("d@x.test", subject, body)
                failed("brevo: expected _RateLimited on 429")
            except app._RateLimited:
                passed("brevo: _RateLimited on 429")

        with patch.object(app.requests, "post", return_value=FakeResponse(401, '{"code":"unauthorized"}')):
            try:
                app._send_via_brevo("d@x.test", subject, body)
                failed("brevo: expected RuntimeError on 401")
            except app._RateLimited:
                failed("brevo: 401 should NOT raise _RateLimited")
            except RuntimeError as e:
                assert "401" in str(e)
                passed(f"brevo: 401 → RuntimeError ({e})")

    # ===== Resend =====
    with patch.dict(app.__dict__, {"RESEND_API_KEY": ""}):
        try:
            app._send_via_resend("d@x.test", subject, body)
            failed("resend: missing key should raise")
        except RuntimeError as e:
            assert "RESEND_API_KEY" in str(e), str(e)
            passed(f"resend: missing RESEND_API_KEY → RuntimeError ({e})")

    with patch.dict(app.__dict__, {"RESEND_API_KEY": "re_test_key", "RESEND_FROM": "Dog Scanner <alerts@cookstar.cc>"}):
        with patch.object(app.requests, "post", side_effect=grab):
            app._send_via_resend("dest@example.test", subject, body)
        assert captured["url"] == "https://api.resend.com/emails", captured["url"]
        assert captured["headers"]["Authorization"] == "Bearer re_test_key"
        assert captured["json"]["from"] == "Dog Scanner <alerts@cookstar.cc>"
        assert captured["json"]["to"] == ["dest@example.test"]
        assert captured["json"]["subject"] == subject
        assert captured["json"]["text"] == body
        passed("resend: correct endpoint, bearer auth, from/to/subject/text")

        with patch.object(app.requests, "post", return_value=FakeResponse(429, '{"name":"rate_limit_exceeded"}')):
            try:
                app._send_via_resend("d@x.test", subject, body)
                failed("resend: expected _RateLimited on 429")
            except app._RateLimited:
                passed("resend: _RateLimited on 429")

        with patch.object(app.requests, "post", return_value=FakeResponse(422, '{"name":"validation_error"}')):
            try:
                app._send_via_resend("d@x.test", subject, body)
                failed("resend: expected RuntimeError on 422")
            except RuntimeError as e:
                assert "422" in str(e)
                passed(f"resend: 422 → RuntimeError ({e})")

    # ===== Dispatcher routing =====
    calls = {"formsubmit": 0, "brevo": 0, "resend": 0}

    def stub_formsubmit(*a, **kw): calls["formsubmit"] += 1
    def stub_brevo(*a, **kw): calls["brevo"] += 1
    def stub_resend(*a, **kw): calls["resend"] += 1

    class _Dog:
        name = "Fido"; source = "Test"; distance_miles = 1.0; breed = "Lab"
        age = "5y"; sex = "M"; location = "Local"; url = "https://x"; reserved = False

    dogs = [_Dog()]

    with patch.dict(app._TRANSPORTS, {"formsubmit": stub_formsubmit, "brevo": stub_brevo, "resend": stub_resend}):
        for t in ("formsubmit", "brevo", "resend"):
            with patch.object(app, "EMAIL_TRANSPORT", t):
                app._send_email("x@y.test", dogs)
    assert calls == {"formsubmit": 1, "brevo": 1, "resend": 1}, calls
    passed("dispatcher: EMAIL_TRANSPORT correctly routes to each transport")

    with patch.object(app, "EMAIL_TRANSPORT", "bogus"):
        try:
            app._send_email("x@y.test", dogs)
            failed("dispatcher: unknown transport should raise")
        except RuntimeError as e:
            assert "bogus" in str(e)
            passed(f"dispatcher: unknown transport → RuntimeError ({e})")

    print("\nAll transport tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
