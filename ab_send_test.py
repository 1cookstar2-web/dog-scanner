"""Fire one identical test email via Brevo AND Resend, back-to-back.

Reads creds from the .env file (via os.environ — you source it manually or
`export $(grep -v '^#' .env | xargs)` first). Prints timing + response so
we can compare the two transports head-to-head.

Usage:
  set -a; source /home/claude-dev/dog-scanner/.env; set +a
  python3 ab_send_test.py <recipient-email> [<tag>]

  - recipient-email: where both test messages should land (e.g. 1cookstar2@gmail.com)
  - tag (optional): short label included in subject so you can grep later
"""

import os
import sys
import time
from datetime import datetime, timezone

# Minimal fake-dog payload so we exercise the real _build_message/_subject_for
class _TestDog:
    def __init__(self, tag):
        self.name = f"AB-TEST-{tag}"
        self.source = "EmailABTest"
        self.distance_miles = 0.0
        self.breed = "Test Breed"
        self.age = "-"
        self.sex = "-"
        self.location = "Localhost"
        self.url = "https://scanner.cookstar.cc/"
        self.reserved = False


def _run_one(name, fn, to_addr, subject, body):
    t0 = time.monotonic()
    try:
        fn(to_addr, subject, body)
        dt = time.monotonic() - t0
        return {"transport": name, "ok": True, "elapsed_s": round(dt, 3), "error": None}
    except Exception as e:
        dt = time.monotonic() - t0
        return {"transport": name, "ok": False, "elapsed_s": round(dt, 3), "error": f"{type(e).__name__}: {e}"}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    to_addr = sys.argv[1]
    tag = sys.argv[2] if len(sys.argv) > 2 else datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    import app  # noqa: E402

    dogs = [_TestDog(tag)]
    subject = app._subject_for(dogs) + f" [{tag}]"
    body = app._build_message(dogs) + f"\n\n(AB test tag={tag})"

    results = []
    # Send Brevo only if the creds look present, otherwise report skip.
    if app.BREVO_API_KEY and app.BREVO_FROM_EMAIL:
        results.append(_run_one("brevo", app._send_via_brevo, to_addr, subject, body))
    else:
        results.append({"transport": "brevo", "ok": False, "elapsed_s": 0.0, "error": "BREVO_API_KEY/BREVO_FROM_EMAIL not configured — skipped"})

    if app.RESEND_API_KEY:
        results.append(_run_one("resend", app._send_via_resend, to_addr, subject, body))
    else:
        results.append({"transport": "resend", "ok": False, "elapsed_s": 0.0, "error": "RESEND_API_KEY not configured — skipped"})

    print(f"\nA/B SEND — to={to_addr} subject={subject!r}")
    print("-" * 60)
    for r in results:
        mark = "OK" if r["ok"] else "ER"
        print(f"  {mark}  {r['transport']:<10} {r['elapsed_s']:>6.3f}s   {r['error'] or ''}")
    print()
    print("Next: inspect the recipient inbox for two messages with this subject.")
    print(f"Grep key for Gmail search: {tag}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
