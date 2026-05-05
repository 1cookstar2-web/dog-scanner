#!/usr/bin/env python3
"""Tests for ai_helpers. Does NOT call the real API — mocks responses.

Run: python3 test_ai_helpers.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import ai_helpers

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
failures: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}")
        failures.append(label)


def make_mock_response(text: str):
    resp = MagicMock()
    block = MagicMock()
    block.text = text
    resp.content = [block]
    return resp


print("=== _extract_json parser ===")
check(ai_helpers._extract_json('{"a":1}') == {"a": 1}, "pure object")
check(ai_helpers._extract_json("[1,2,3]") == [1, 2, 3], "pure array")
check(ai_helpers._extract_json('```json\n{"x":2}\n```') == {"x": 2}, "json code fence")
check(ai_helpers._extract_json('Here you go: {"k":"v"} done') == {"k": "v"}, "prose-wrapped object")
check(ai_helpers._extract_json("Prose then [1,2]") == [1, 2], "prose-wrapped array")
check(ai_helpers._extract_json('[{"a":1},{"b":2}]') == [{"a": 1}, {"b": 2}], "nested")

try:
    ai_helpers._extract_json("no json here")
    failures.append("should raise on no-json")
    print(f"  {FAIL}  raises on no-json input")
except ValueError:
    print(f"  {PASS}  raises ValueError when no JSON found")


print("\n=== ai_breed_check (mocked API) ===")
with tempfile.TemporaryDirectory() as tmp:
    ai_helpers._BREED_CACHE_FILE = Path(tmp) / "breed_cache.json"
    ai_helpers._EXTRACT_CACHE_FILE = Path(tmp) / "extract_cache.json"
    ai_helpers._client = None
    ai_helpers._client_error = None

    mock_client = MagicMock()
    mock_client.messages.create.return_value = make_mock_response(
        '{"match": true, "breed": "Border Collie Cross", "confidence": "high"}'
    )

    with patch.object(ai_helpers, "_get_client", return_value=mock_client):
        text = "Max is a 3-year-old Border Collie mix with tons of energy and herding instinct."
        v = ai_helpers.ai_breed_check(text)
        check(v.match is True, "match=True for BC prose")
        check(v.breed == "Border Collie Cross", "breed label populated")
        check(v.confidence == "high", "confidence parsed")
        check(v.source == "ai", "source=ai on first call")

        # Cache hit
        v2 = ai_helpers.ai_breed_check(text)
        check(v2.source == "cache", "source=cache on repeat call")
        check(mock_client.messages.create.call_count == 1, "API called only once for cached text")

    # Too-short text skipped
    v3 = ai_helpers.ai_breed_check("Short")
    check(v3.source == "skip" and v3.match is False, "short text skipped without API call")

    # No client → skip
    ai_helpers._client = None
    ai_helpers._client_error = "test-disabled"
    v4 = ai_helpers.ai_breed_check("A" * 100 + " Border Collie " + "B" * 100)
    check(v4.source == "skip", "no-client returns skip")
    ai_helpers._client_error = None

print("\n=== ai_extract_dogs (mocked API) ===")
with tempfile.TemporaryDirectory() as tmp:
    ai_helpers._BREED_CACHE_FILE = Path(tmp) / "breed_cache.json"
    ai_helpers._EXTRACT_CACHE_FILE = Path(tmp) / "extract_cache.json"
    ai_helpers._client = None
    ai_helpers._client_error = None

    mock_client = MagicMock()
    mock_client.messages.create.return_value = make_mock_response(
        '[{"name":"Max","breed":"BC","url":"https://x.com/max","location":"UK","age":"3","sex":"M"},'
        '{"name":"Luna","breed":"Springer","url":"https://x.com/luna","location":"","age":"","sex":""}]'
    )

    with patch.object(ai_helpers, "_get_client", return_value=mock_client):
        dogs = ai_helpers.ai_extract_dogs("A" * 300, "TestSource", "https://x.com")
        check(len(dogs) == 2, f"extracted 2 dogs (got {len(dogs)})")
        check(dogs[0].name == "Max" and dogs[1].name == "Luna", "names correct")
        check(dogs[0].url == "https://x.com/max", "first URL correct")

    # Too-short HTML skipped
    dogs_empty = ai_helpers.ai_extract_dogs("short", "TestSource", "https://x.com")
    check(dogs_empty == [], "short HTML returns empty list")

    # Prose-wrapped response
    mock_client2 = MagicMock()
    mock_client2.messages.create.return_value = make_mock_response(
        'Here are the dogs:\n[{"name":"Alice","breed":"Cocker","url":"https://x.com/a","location":"","age":"","sex":""}]\nDone.'
    )
    ai_helpers._EXTRACT_CACHE_FILE = Path(tmp) / "extract_cache2.json"
    with patch.object(ai_helpers, "_get_client", return_value=mock_client2):
        dogs2 = ai_helpers.ai_extract_dogs("B" * 300, "TestSource", "https://x.com")
        check(len(dogs2) == 1 and dogs2[0].name == "Alice", "parses JSON embedded in prose")


print("\n=== matches_breed_ai (regex + fallback) ===")
import scanner
check(scanner.matches_breed_ai("Border Collie") is True, "regex hit: Border Collie")
check(scanner.matches_breed_ai("cocker spaniel cross") is True, "regex hit: cocker spaniel cross")
check(scanner.matches_breed_ai("short") is False, "short non-match: False")

# Mock AI for fallback test
mock_client3 = MagicMock()
mock_client3.messages.create.return_value = make_mock_response(
    '{"match": false, "breed": "Labrador", "confidence": "high"}'
)
with tempfile.TemporaryDirectory() as tmp:
    ai_helpers._BREED_CACHE_FILE = Path(tmp) / "b.json"
    ai_helpers._client = None
    ai_helpers._client_error = None
    with patch.object(ai_helpers, "_get_client", return_value=mock_client3):
        # Long Labrador text — regex won't match, AI says no match
        txt = ("Meet Buddy, a handsome 5-year-old Labrador Retriever "
               "looking for his forever home. Great with children.")
        check(scanner.matches_breed_ai(txt) is False, "AI fallback: Labrador → False")


print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
else:
    print(f"All {14 + 7 + 4 + 3} checks passed.")
