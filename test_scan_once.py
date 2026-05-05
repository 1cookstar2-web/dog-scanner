#!/usr/bin/env python3
"""Comprehensive tests for scan_once.py.

Covers every helper plus the main() end-to-end flow with mocked scan().
Mirrors the existing test pattern in this repo (PASS/FAIL prints + final
exit code based on accumulated failures). No network calls, no real
Telegram, no real scan.

Run: cd /home/claude-dev/dog-scanner && python3 test_scan_once.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
failures: list[str] = []


def check(condition: bool, label: str) -> None:
    if condition:
        print(f"  {PASS}  {label}")
    else:
        failures.append(label)
        print(f"  {FAIL}  {label}")


class _FakeDog:
    """Plain object stand-in for scanner.Dog. Plain attributes so json.dumps
    via getattr(d, ..., default) works correctly in scan_once."""
    def __init__(self, url, name="Test", breed="Border Collie",
                 distance=5.0, source="test-rescue",
                 age=None, sex=None, location=None, reserved=False):
        self.url = url
        self.name = name
        self.breed = breed
        self.distance_miles = distance
        self.source = source
        self.age = age
        self.sex = sex
        self.location = location
        self.reserved = reserved


def make_dog(url: str, name: str = "Test", breed: str = "Border Collie",
             distance: float | None = 5.0, source: str = "test-rescue"):
    """Stand-in for scanner.Dog with the fields scan_once reads."""
    return _FakeDog(url=url, name=name, breed=breed,
                    distance=distance, source=source)


def reset_state_dir(scan_once_module, tmpdir: Path):
    scan_once_module.STATE_DIR = tmpdir
    scan_once_module.KNOWN_FILE = tmpdir / "known_dogs.json"
    scan_once_module.PREV_SCAN_FILE = tmpdir / "prev_scan_urls.json"
    scan_once_module.FIRST_SEEN_FILE = tmpdir / "first_seen.json"
    scan_once_module.HEARTBEAT_FILE = tmpdir / "watcher_heartbeat.json"


class _CapturedStdout:
    def __init__(self):
        self.buf = io.StringIO()
        self._real = sys.stdout
    def __enter__(self):
        sys.stdout = self.buf
        return self
    def __exit__(self, *a):
        sys.stdout = self._real
    @property
    def text(self) -> str:
        return self.buf.getvalue()


import scan_once  # noqa: E402


print("\n=== _int_env (defensive int parsing) ===")

with patch.dict(os.environ, {}, clear=True):
    check(scan_once._int_env("MISSING_KEY", 42) == 42, "missing key -> default 42")

with patch.dict(os.environ, {"X": ""}, clear=True):
    check(scan_once._int_env("X", 42) == 42, "empty string -> default 42")

with patch.dict(os.environ, {"X": "   "}, clear=True):
    check(scan_once._int_env("X", 42) == 42, "whitespace-only -> default 42")

with patch.dict(os.environ, {"X": "600"}, clear=True):
    check(scan_once._int_env("X", 42) == 600, "valid '600' -> 600")

with patch.dict(os.environ, {"X": "  900  "}, clear=True):
    check(scan_once._int_env("X", 42) == 900, "padded '  900  ' -> 900")

with patch.dict(os.environ, {"X": "abc"}, clear=True):
    with patch.object(sys, "stderr", io.StringIO()) as err:
        result = scan_once._int_env("X", 42)
    check(result == 42, "'abc' (malformed) -> default 42")
    check("not a valid int" in err.getvalue(), "'abc' -> emits warning to stderr")


print("\n=== _load_json ===")

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp) / "missing.json"
    check(scan_once._load_json(tmp_path, []) == [], "missing file -> default []")
    check(scan_once._load_json(tmp_path, {"k": "v"}) == {"k": "v"},
          "missing file -> default dict")

    p = Path(tmp) / "valid.json"
    p.write_text('["a", "b"]')
    check(scan_once._load_json(p, []) == ["a", "b"], "valid JSON array -> parsed")

    p.write_text('{"x": 1}')
    check(scan_once._load_json(p, {}) == {"x": 1}, "valid JSON object -> parsed")

    p.write_text("{ malformed JSON }")
    check(scan_once._load_json(p, []) == [], "malformed JSON -> default []")


print("\n=== _save_json_atomic ===")

with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "out.json"
    scan_once._save_json_atomic(p, ["a", "b", "c"])
    check(p.exists(), "writes target file")
    check(json.loads(p.read_text()) == ["a", "b", "c"], "content matches")
    check(not (p.with_suffix(".json.tmp")).exists(), "no .tmp leftover after rename")

    nested = Path(tmp) / "nested" / "deeper" / "out.json"
    scan_once._save_json_atomic(nested, {"k": 1})
    check(nested.exists(), "creates missing parent dirs")


print("\n=== _send_telegram (mocked HTTP) ===")

dog = make_dog("https://example.org/dogs/luna", name="Luna",
               breed="Border Collie", distance=12.3, source="example-rescue")

with patch.object(scan_once, "TELEGRAM_TOKEN", ""):
    with patch.object(scan_once, "TELEGRAM_CHAT_ID", "123"):
        with patch("scan_once.urllib.request.urlopen") as urlopen:
            scan_once._send_telegram([dog])
            check(not urlopen.called, "no token -> urlopen NOT called")

with patch.object(scan_once, "TELEGRAM_TOKEN", "tok"):
    with patch.object(scan_once, "TELEGRAM_CHAT_ID", ""):
        with patch("scan_once.urllib.request.urlopen") as urlopen:
            scan_once._send_telegram([dog])
            check(not urlopen.called, "no chat_id -> urlopen NOT called")

with patch.object(scan_once, "TELEGRAM_TOKEN", "TESTTOKEN"):
    with patch.object(scan_once, "TELEGRAM_CHAT_ID", "CHATID"):
        with patch("scan_once.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.status = 200
            scan_once._send_telegram([dog])
            check(urlopen.called, "send: urlopen called once")
            req = urlopen.call_args[0][0]
            check("api.telegram.org/botTESTTOKEN/sendMessage" in req.full_url,
                  "send: URL contains bot token")
            body = req.data.decode()
            check("chat_id=CHATID" in body, "send: payload has chat_id")
            check("Luna" in body, "send: payload contains dog name")
            check("Border+Collie" in body, "send: breed URL-encoded")
            check("12.3+mi" in body, "send: distance present")

with patch.object(scan_once, "TELEGRAM_TOKEN", "T"):
    with patch.object(scan_once, "TELEGRAM_CHAT_ID", "C"):
        with patch("scan_once.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.status = 500
            try:
                scan_once._send_telegram([dog])
                raised = False
            except RuntimeError as e:
                raised = "500" in str(e)
            check(raised, "non-200 -> RuntimeError mentioning status")

dog_no_dist = make_dog("https://e.org/x", name="Mystery", distance=None)
with patch.object(scan_once, "TELEGRAM_TOKEN", "T"):
    with patch.object(scan_once, "TELEGRAM_CHAT_ID", "C"):
        with patch("scan_once.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.status = 200
            scan_once._send_telegram([dog_no_dist])
            body = urlopen.call_args[0][0].data.decode()
            check("dist+unknown" in body, "None distance -> 'dist unknown'")


print("\n=== _update_first_seen ===")

with tempfile.TemporaryDirectory() as tmp:
    reset_state_dir(scan_once, Path(tmp))

    scan_once._update_first_seen(set())
    check(not scan_once.FIRST_SEEN_FILE.exists(), "empty set -> no file written")

    scan_once._update_first_seen({"https://a.org/1", "https://a.org/2"})
    fs = json.loads(scan_once.FIRST_SEEN_FILE.read_text())
    check(len(fs) == 2, "first call: 2 entries written")
    check("https://a.org/1" in fs and "https://a.org/2" in fs, "URLs are keys")
    sample_ts = next(iter(fs.values()))
    check(sample_ts.startswith("20") and "T" in sample_ts and "+00:00" in sample_ts,
          f"timestamp is ISO-8601 UTC")

    original_ts = fs["https://a.org/1"]
    scan_once._update_first_seen({"https://a.org/1", "https://a.org/3"})
    fs2 = json.loads(scan_once.FIRST_SEEN_FILE.read_text())
    check(fs2["https://a.org/1"] == original_ts, "existing entry: timestamp preserved")
    check("https://a.org/3" in fs2, "new entry added on second call")
    check(len(fs2) == 3, "total now 3 entries")


print("\n=== main() end-to-end (mocked scan) ===")

TEST_ENV = {
    "TELEGRAM_TOKEN": "T",
    "TELEGRAM_CHAT_ID": "C",
    "SCAN_INTERVAL_SECS": "600",
}


def run_main(scan_return, dry_run: bool = False, env_overrides: dict | None = None):
    env = {**TEST_ENV, **(env_overrides or {})}
    state_dir = tempfile.mkdtemp()
    reset_state_dir(scan_once, Path(state_dir))
    scan_once.TELEGRAM_TOKEN = env["TELEGRAM_TOKEN"]
    scan_once.TELEGRAM_CHAT_ID = env["TELEGRAM_CHAT_ID"]

    argv = ["scan_once.py"] + (["--dry-run"] if dry_run else [])
    with patch.dict(os.environ, env, clear=False):
        with patch("scan_once.scan", return_value=scan_return):
            with patch("scan_once.urllib.request.urlopen") as urlopen:
                urlopen.return_value.__enter__.return_value.status = 200
                with patch.object(sys, "argv", argv):
                    with _CapturedStdout() as cap:
                        try:
                            code = scan_once.main()
                        except SystemExit as e:
                            code = e.code
                return code, cap.text, urlopen.call_count, Path(state_dir)


# Scenario 1: empty known state -> seeds, NO alerts
dogs_seed = [make_dog(f"https://a.org/{i}") for i in range(3)]
code, out, tg_calls, sd = run_main(dogs_seed)
check(code == 0, "seed: exit 0")
check("seeded known-dog state with 3 URLs" in out, "seed: log says seeded")
check(tg_calls == 0, "seed: no Telegram call")
known = json.loads((sd / "known_dogs.json").read_text())
check(len(known) == 3, "seed: known_dogs.json has 3 URLs")
check((sd / "watcher_heartbeat.json").exists(), "seed: heartbeat written")

# Scenario 2: existing known + 1 new dog -> Telegram sent
dogs_existing = [make_dog(f"https://a.org/{i}") for i in range(3)]
dogs_with_new = dogs_existing + [make_dog("https://a.org/NEW", name="Newbie")]
state_dir = Path(tempfile.mkdtemp())
reset_state_dir(scan_once, state_dir)
(state_dir / "known_dogs.json").write_text(json.dumps([d.url for d in dogs_existing]))
with patch("scan_once.scan", return_value=dogs_with_new):
    with patch("scan_once.urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.status = 200
        with patch.object(sys, "argv", ["scan_once.py"]):
            with patch.dict(os.environ, TEST_ENV, clear=False):
                scan_once.TELEGRAM_TOKEN = "T"
                scan_once.TELEGRAM_CHAT_ID = "C"
                with _CapturedStdout() as cap:
                    code = scan_once.main()
        tg_calls = urlopen.call_count
check(code == 0, "new-dog live: exit 0")
check("1 new dog(s) detected" in cap.text, "new-dog live: log mentions new dog")
check("telegram sent" in cap.text, "new-dog live: log says telegram sent")
check(tg_calls == 1, "new-dog live: Telegram urlopen called once")
known = set(json.loads((state_dir / "known_dogs.json").read_text()))
check("https://a.org/NEW" in known, "new-dog live: NEW url added to known")

# Scenario 3: --dry-run -> state updated, NO Telegram
state_dir = Path(tempfile.mkdtemp())
reset_state_dir(scan_once, state_dir)
(state_dir / "known_dogs.json").write_text(json.dumps([d.url for d in dogs_existing]))
with patch("scan_once.scan", return_value=dogs_with_new):
    with patch("scan_once.urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.status = 200
        with patch.object(sys, "argv", ["scan_once.py", "--dry-run"]):
            with patch.dict(os.environ, TEST_ENV, clear=False):
                scan_once.TELEGRAM_TOKEN = "T"
                scan_once.TELEGRAM_CHAT_ID = "C"
                with _CapturedStdout() as cap:
                    code = scan_once.main()
        tg_calls = urlopen.call_count
check(code == 0, "new-dog dry-run: exit 0")
check("DRY-RUN" in cap.text, "new-dog dry-run: log mentions DRY-RUN")
check("Telegram suppressed" in cap.text, "new-dog dry-run: explicit suppression note")
check(tg_calls == 0, "new-dog dry-run: NO Telegram call")
known = set(json.loads((state_dir / "known_dogs.json").read_text()))
check("https://a.org/NEW" in known, "new-dog dry-run: state STILL updated")

# Scenario 4: scan returns same as known -> no new dogs
state_dir = Path(tempfile.mkdtemp())
reset_state_dir(scan_once, state_dir)
(state_dir / "known_dogs.json").write_text(json.dumps([d.url for d in dogs_existing]))
with patch("scan_once.scan", return_value=dogs_existing):
    with patch("scan_once.urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.status = 200
        with patch.object(sys, "argv", ["scan_once.py"]):
            with patch.dict(os.environ, TEST_ENV, clear=False):
                scan_once.TELEGRAM_TOKEN = "T"
                scan_once.TELEGRAM_CHAT_ID = "C"
                with _CapturedStdout() as cap:
                    code = scan_once.main()
        tg_calls = urlopen.call_count
check(code == 0, "no-new: exit 0")
check("no new dogs this cycle" in cap.text, "no-new: explicit log")
check(tg_calls == 0, "no-new: no Telegram call")
hb = json.loads((state_dir / "watcher_heartbeat.json").read_text())
check(hb["dogs_in_scan"] == 3, "no-new: heartbeat dogs_in_scan=3")
check(hb["new_urls"] == 0, "no-new: heartbeat new_urls=0")
check("source" in hb and hb["source"] in ("cli", "github-actions"),
      "no-new: heartbeat has source field")

# Scenario 5: scan() raises -> exit 1
state_dir = Path(tempfile.mkdtemp())
reset_state_dir(scan_once, state_dir)
def boom():
    raise RuntimeError("network down")
with patch("scan_once.scan", side_effect=boom):
    with patch.object(sys, "argv", ["scan_once.py"]):
        with patch.dict(os.environ, TEST_ENV, clear=False):
            with _CapturedStdout() as cap:
                with patch.object(sys, "stderr", io.StringIO()) as err:
                    code = scan_once.main()
                err_text = err.getvalue()
check(code == 1, "scan failure: exit 1")
check("scan() raised" in err_text, "scan failure: error logged to stderr")

# Scenario 6: Telegram failure -> still exits 0
state_dir = Path(tempfile.mkdtemp())
reset_state_dir(scan_once, state_dir)
(state_dir / "known_dogs.json").write_text(json.dumps([d.url for d in dogs_existing]))
with patch("scan_once.scan", return_value=dogs_with_new):
    with patch("scan_once.urllib.request.urlopen") as urlopen:
        urlopen.side_effect = OSError("telegram unreachable")
        with patch.object(sys, "argv", ["scan_once.py"]):
            with patch.dict(os.environ, TEST_ENV, clear=False):
                scan_once.TELEGRAM_TOKEN = "T"
                scan_once.TELEGRAM_CHAT_ID = "C"
                with _CapturedStdout() as cap:
                    with patch.object(sys, "stderr", io.StringIO()) as err:
                        code = scan_once.main()
                    err_text = err.getvalue()
check(code == 0, "telegram failure: still exit 0")
check("telegram failed" in err_text, "telegram failure: logged to stderr")
known = set(json.loads((state_dir / "known_dogs.json").read_text()))
check("https://a.org/NEW" in known,
      "telegram failure: state STILL updated (no duplicate-alert risk)")


print("\n=== state/dogs.json (rich snapshot for dashboard) ===")

# Set up: pre-populate first_seen with one of the dogs to test the join
state_dir = Path(tempfile.mkdtemp())
reset_state_dir(scan_once, state_dir)
dogs = [
    _FakeDog("https://r.org/luna", name="Luna", breed="Border Collie",
             distance=8.5, source="r-rescue", age="3y", sex="F",
             location="London", reserved=False),
    _FakeDog("https://r.org/rex", name="Rex", breed="Springer Spaniel",
             distance=22.0, source="x-rescue", age="5y", sex="M",
             location="Bristol", reserved=True),
    _FakeDog("https://r.org/mystery", name="Mystery"),  # no first_seen
]
# Pre-seed first_seen.json with two of three URLs at different timestamps
(state_dir / "first_seen.json").write_text(json.dumps({
    "https://r.org/rex": "2026-05-01T10:00:00+00:00",   # older
    "https://r.org/luna": "2026-05-04T20:00:00+00:00",  # newer
}))
# Pre-seed known so we exercise the "no new" branch (clean test)
(state_dir / "known_dogs.json").write_text(json.dumps([d.url for d in dogs]))

with patch("scan_once.scan", return_value=dogs):
    with patch("scan_once.urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.status = 200
        with patch.object(sys, "argv", ["scan_once.py"]):
            with patch.dict(os.environ, TEST_ENV, clear=False):
                scan_once.TELEGRAM_TOKEN = "T"
                scan_once.TELEGRAM_CHAT_ID = "C"
                with _CapturedStdout():
                    code = scan_once.main()
check(code == 0, "snapshot: scan exited 0")
check(scan_once.DOGS_FILE.exists(), "snapshot: dogs.json was written")

snap = json.loads(scan_once.DOGS_FILE.read_text())
check(isinstance(snap, list) and len(snap) == 3, "snapshot: list of 3 records")

# Field shape
record_keys = set(snap[0].keys())
expected_keys = {"url", "name", "breed", "age", "sex", "location",
                 "distance_miles", "source", "reserved", "first_seen"}
check(expected_keys <= record_keys,
      f"snapshot: record has all expected fields (got {record_keys - expected_keys} missing)")

# Sort order: newest first_seen first; missing first_seen sinks to bottom
check(snap[0]["url"] == "https://r.org/luna", "snapshot: newest first_seen comes first")
check(snap[1]["url"] == "https://r.org/rex", "snapshot: older first_seen second")
check(snap[2]["url"] == "https://r.org/mystery", "snapshot: missing first_seen last")

# first_seen join
check(snap[0]["first_seen"] == "2026-05-04T20:00:00+00:00",
      "snapshot: first_seen joined from first_seen.json")
check(snap[2]["first_seen"] is None, "snapshot: missing first_seen is None")

# Field passthrough
luna = snap[0]
check(luna["name"] == "Luna" and luna["breed"] == "Border Collie",
      "snapshot: name/breed correct")
check(luna["distance_miles"] == 8.5, "snapshot: distance_miles passes through")
check(luna["age"] == "3y" and luna["sex"] == "F", "snapshot: age/sex pass through")
check(luna["location"] == "London", "snapshot: location passes through")
check(luna["reserved"] is False, "snapshot: reserved bool passes through")
check(snap[1]["reserved"] is True, "snapshot: reserved=True passes through")


print()
if failures:
    print(f"\033[31m{len(failures)} FAILED\033[0m: {failures}")
    sys.exit(1)
else:
    print(f"\033[32mAll checks passed.\033[0m")
