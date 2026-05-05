"""Claude-powered helpers for the dog scanner.

Two capabilities:
  1. ai_breed_check(text)    — decides whether prose describes a BC/Springer/
                               Cocker/Spaniel (cross or pure). Uses Haiku.
  2. ai_extract_dogs(html)   — extracts adoptable dogs from raw HTML when a
                               known adapter returns unexpectedly 0 results.
                               Uses Sonnet.

Both degrade gracefully: if ANTHROPIC_API_KEY is missing or the SDK isn't
installed, the functions return a "skip" result and the caller falls back
to existing regex / adapter logic.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import Lock


def _extract_json(raw: str):
    """Best-effort extraction of a JSON object or array from Claude's output.

    Handles: pure JSON, ```json fences, and prose-wrapped JSON.
    """
    raw = raw.strip()
    # Strip code fences
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    # Direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Find the first [...] or {...} span that parses
    for opener, closer in [("[", "]"), ("{", "}")]:
        i = raw.find(opener)
        while i != -1:
            depth = 0
            for j in range(i, len(raw)):
                c = raw[j]
                if c == opener:
                    depth += 1
                elif c == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(raw[i:j+1])
                        except json.JSONDecodeError:
                            break
            i = raw.find(opener, i + 1)
    raise ValueError("no parseable JSON found in response")

_CACHE_DIR = Path(__file__).resolve().parent / "state"
_CACHE_DIR.mkdir(exist_ok=True)
_BREED_CACHE_FILE = _CACHE_DIR / "ai_breed_cache.json"
_EXTRACT_CACHE_FILE = _CACHE_DIR / "ai_extract_cache.json"

_cache_lock = Lock()

_client = None
_client_error: str | None = None


def _get_client():
    global _client, _client_error
    if _client is not None or _client_error is not None:
        return _client
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        _client_error = f"anthropic SDK not installed: {e}"
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _client_error = "ANTHROPIC_API_KEY not set"
        return None
    _client = anthropic.Anthropic()
    return _client


def _load_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class BreedVerdict:
    match: bool
    breed: str
    confidence: str  # "high" | "medium" | "low"
    source: str  # "cache" | "ai" | "skip"


def ai_breed_check(text: str) -> BreedVerdict:
    """Ask Claude whether the dog description mentions a BC/Springer/Cocker/
    Spaniel or a cross involving one. Returns a BreedVerdict.

    Caches by SHA-256(text) so the same description is never re-queried.
    """
    text = (text or "").strip()
    if not text or len(text) < 20:
        return BreedVerdict(False, "", "low", "skip")

    key = _cache_key(text)
    with _cache_lock:
        cache = _load_cache(_BREED_CACHE_FILE)
        if key in cache:
            v = cache[key]
            return BreedVerdict(v["match"], v["breed"], v["confidence"], "cache")

    client = _get_client()
    if client is None:
        return BreedVerdict(False, "", "low", "skip")

    prompt = (
        "You are helping a dog-rescue scanner decide if a listing describes "
        "a Border Collie, Bearded Collie, Springer Spaniel, Cocker Spaniel, "
        "Sprocker, Sprollie, or a cross involving one of these breeds.\n\n"
        "EXCLUDE pure Cavalier King Charles Spaniels and pure Cockapoos — "
        "those are noise. But INCLUDE crosses like Cavalier x Springer.\n\n"
        "Reply with ONLY a single JSON object, no prose, matching:\n"
        '{"match": true|false, "breed": "short label", "confidence": "high|medium|low"}\n\n'
        f"Listing text:\n---\n{text[:4000]}\n---"
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text
        data = _extract_json(raw)
        verdict = BreedVerdict(
            match=bool(data.get("match")),
            breed=str(data.get("breed", ""))[:80],
            confidence=str(data.get("confidence", "low")).lower(),
            source="ai",
        )
    except Exception as e:
        print(f"[ai-breed] error: {e}", file=sys.stderr)
        return BreedVerdict(False, "", "low", "skip")

    with _cache_lock:
        cache = _load_cache(_BREED_CACHE_FILE)
        cache[key] = {"match": verdict.match, "breed": verdict.breed,
                      "confidence": verdict.confidence}
        _save_cache(_BREED_CACHE_FILE, cache)
    return verdict


@dataclass
class ExtractedDog:
    name: str
    breed: str
    url: str
    location: str
    age: str
    sex: str


def ai_extract_dogs(html: str, source: str, base_url: str) -> list[ExtractedDog]:
    """Feed raw HTML to Claude and ask it to extract adoptable dog listings.

    Used as a fallback when a known adapter returns 0 results unexpectedly.
    Returns an empty list if the API is unavailable or extraction fails.

    Caches by SHA-256(html) so we don't re-query identical pages.
    """
    if not html or len(html) < 200:
        return []

    key = _cache_key(html)
    with _cache_lock:
        cache = _load_cache(_EXTRACT_CACHE_FILE)
        if key in cache:
            return [ExtractedDog(**d) for d in cache[key]]

    client = _get_client()
    if client is None:
        return []

    # Cap HTML size to control token use. Most rescue listing pages fit in ~60KB.
    html_snippet = html[:60000]

    prompt = (
        "Extract every adoptable dog from this UK rescue website's HTML. "
        "Ignore navigation, footer links, and dogs marked as rehomed/adopted.\n\n"
        "Return ONLY a JSON array (no prose, no explanation, no code fence). "
        "Format:\n"
        '[{"name":"...","breed":"...","url":"...","location":"...","age":"...","sex":"..."}]\n\n'
        "Rules:\n"
        "- url: absolute URL; if relative, prepend base_url.\n"
        "- Use empty string \"\" for unknown fields (do NOT omit keys).\n"
        "- Include every dog visible in the listing, even if breed isn't shown — "
        "leave breed as \"\" in that case rather than excluding the dog.\n"
        "- Exclude dogs explicitly marked rehomed/reserved/adopted.\n"
        f"- base_url: {base_url}\n"
        f"- source: {source}\n\n"
        "HTML:\n---\n"
        f"{html_snippet}\n---\n"
        "Respond with the JSON array and nothing else."
    )

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text
        data = _extract_json(raw)
        if not isinstance(data, list):
            return []
        dogs = []
        for d in data:
            if not isinstance(d, dict):
                continue
            url = str(d.get("url", "")).strip()
            if not url:
                continue
            dogs.append(ExtractedDog(
                name=str(d.get("name", ""))[:80],
                breed=str(d.get("breed", ""))[:100],
                url=url[:500],
                location=str(d.get("location", ""))[:80],
                age=str(d.get("age", ""))[:40],
                sex=str(d.get("sex", ""))[:20],
            ))
    except Exception as e:
        print(f"[ai-extract] {source} error: {e}", file=sys.stderr)
        return []

    with _cache_lock:
        cache = _load_cache(_EXTRACT_CACHE_FILE)
        cache[key] = [d.__dict__ for d in dogs]
        _save_cache(_EXTRACT_CACHE_FILE, cache)
    return dogs
