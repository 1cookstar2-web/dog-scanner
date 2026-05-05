#!/usr/bin/env python3
"""Dog scanner. Finds Border Collies, Springer Spaniels, or crosses
within ~100 miles of Hayling Island (PO11) across UK rescue sites."""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import datetime as dt
import json
import math
import re
import sys
import time
from dataclasses import asdict, dataclass
from typing import Callable

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# ------------------------------------------------------------
# Distance-to-PO11 infrastructure
# ------------------------------------------------------------

PO11_LAT, PO11_LON = 50.783, -0.984


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# Known rescue-centre coordinates. Key is a substring matcher against the
# Dog.location string. First hit wins, so list more-specific names first.
CENTRE_COORDS: list[tuple[str, tuple[float, float]]] = [
    ("Dogs Trust Shoreham",       (50.832, -0.272)),
    ("Dogs Trust Salisbury",      (51.069, -1.795)),
    ("Dogs Trust Newbury",        (51.402, -1.327)),
    ("Dogs Trust Harefield",      (51.602, -0.481)),
    ("Dogs Trust Basildon",       (51.572,  0.455)),
    ("Dogs Trust Canterbury",     (51.279,  1.080)),
    ("Dogs Trust Evesham",        (52.092, -1.948)),
    ("Battersea Brands Hatch",    (51.358,  0.269)),
    ("Battersea Old Windsor",     (51.476, -0.595)),
    ("Battersea London",          (51.482, -0.148)),
    ("Blue Cross Southampton",    (50.932, -1.445)),
    ("Blue Cross Burford",        (51.809, -1.635)),
    ("Blue Cross Suffolk",        (52.245,  0.817)),  # Thurston / Bury St Edmunds
    ("Blue Cross Tiverton",       (50.902, -3.490)),  # Devon
    ("Blue Cross Kimpton",        (51.866, -0.309)),  # Herts
    ("Blue Cross Lewknor",        (51.678, -0.997)),  # Oxon
    ("Blue Cross Newport",        (51.588, -2.998)),  # Monmouthshire
    ("Blue Cross Rolleston",      (52.824, -1.632)),  # Staffordshire
    ("Blue Cross Hertfordshire",  (51.866, -0.309)),
    ("Blue Cross Oxfordshire",    (51.678, -0.997)),
    ("NAWT Trindledown",          (51.420, -1.488)),
    ("NAWT Heathlands",           (51.754, -0.478)),
    ("NAWT Watford",              (51.691, -0.388)),
    ("NAWT Berkshire",            (51.420, -1.488)),
    ("NAWT Hertfordshire",        (51.754, -0.478)),
    ("Wiccaweys",                 (51.040, -2.283)),
    ("Holbrook",                  (51.062, -0.329)),
    ("Helping Hounds",            (51.121, -0.758)),
    ("Last Chance Edenbridge",    (51.193,  0.060)),
    ("Last Chance New Romney",    (50.994,  0.944)),
    ("Last Chance",               (51.193,  0.060)),
    ("Border Collie Spot",        (51.430, -0.750)),  # Binfield, Berks
    ("FOSTBC",                    (54.090, -1.400)),  # Boroughbridge, N. Yorks (UK-wide fosters)
    ("PPBC",                      (51.790, -3.990)),  # Ammanford, Wales (UK-wide fosters)
    ("Sprocker Assist",           (52.300, -1.500)),  # UK-wide fosters; central default
    ("Ferne",                     (50.870, -2.960)),  # Chard, Somerset
    ("Margaret Green",            (50.690, -2.110)),  # Wareham, Dorset
    ("RRAUK",                     (51.113, -0.860)),  # Bordon, Hants fosters
    ("Pawprints to Freedom",      (52.300, -1.500)),  # UK-wide fosters; central default
    ("Epsom Canine",              (51.330, -0.270)),  # Epsom, Surrey
    # --- Round 2 additions (Tier 1, <=30mi) ---
    ("Phoenix Rehoming",          (50.867, -0.977)),  # Havant, Hants
    ("RSPCA Solent",              (50.830, -1.230)),  # Stubbington Ark, Fareham
    ("RSPCA Sussex West",         (50.870, -0.770)),  # Mount Noddy, Chichester
    ("RSPCA Isle of Wight",       (50.628, -1.244)),  # Godshill, IoW
    ("St Francis",                (50.966, -1.305)),  # Fair Oak, Eastleigh
    ("Second Chance Animal Rescue", (50.946, -1.374)),# Mansbridge, Southampton
    ("Clymping",                  (50.824, -0.606)),  # Ford, near Arundel
    ("Wadars",                    (50.806, -0.440)),  # Ferring, Worthing
    ("Arundawn",                  (51.063, -0.323)),  # Horsham, West Sussex
    ("Fareham",                   (50.850, -1.180)),  # Fareham BC (Ocella)
    ("Gosport",                   (50.795, -1.130)),  # Gosport BC (Ocella)
    # --- Round 2 additions (Tier 2, 30–70mi) ---
    ("Pro Dogs Direct",           (51.246, -1.088)),  # SE foster network default
    ("FurBuddies",                (50.752, -1.624)),  # Hordle, New Forest
    ("Dogs N Homes",              (51.282, -0.842)),  # Fleet, Hants
    ("Waggy Tails",               (50.720, -1.983)),  # Poole, Dorset
    ("Chilterns Dog Rescue",      (51.608, -0.569)),  # Chalfont St Peter, Bucks
    # --- Round 2 additions (Tier 3, 70–100mi edge) ---
    ("Heathlands Animal Sanctuary", (52.053, -0.030)),  # Royston, Herts
    ("Mutts in Distress",         (51.819,  0.160)),   # Harlow/Essex-Herts border
    ("Help 4 Hounds",             (51.222, -2.975)),   # Highbridge, Somerset
    ("Oxfordshire Animal Sanctuary", (51.659, -1.080)),# Stadhampton, Oxon
    ("Woodgreen Pets Charity",    (52.300, -0.173)),   # Godmanchester, Cambs
    ("Teckels Animal Sanctuary",  (51.779, -2.325)),   # Whitminster, Glos
    # --- Round 3: UK-wide breed specialists + reclaimed rescues ---
    ("UK Spaniel Rescue",         (52.300, -1.500)),  # UK-wide fosters; central default
    ("Spaniel Rescue Foundation", (53.398, -0.327)),  # Middle Rasen, Lincs; UK-wide fosters
    ("Collie Rescue R&S",         (53.383, -1.467)),  # Sheffield HQ; UK-wide home-check
    ("Save Our Spaniels",         (52.300, -1.500)),  # UK-wide fosters
    ("Spaniel Assist",            (52.300, -1.500)),  # UK-wide fosters
    ("Helping Dogs and Cats UK",  (51.270, -1.080)),  # Basingstoke ~40mi
    ("Lurcher SOS",               (52.300, -1.500)),  # UK-wide fosters
    ("Forever Hounds Trust",      (52.300, -1.500)),  # UK-wide fosters
    ("NWESSR",                    (53.259, -2.515)),  # Northwich, NW England
    ("Animal Rescue Cymru",       (52.100, -4.700)),  # West Wales
    ("Many Tears",                (51.680, -4.150)),  # Llanelli; UK-wide fosters
    ("Lurcher Link",              (52.300, -1.500)),  # aggregator; UK-wide fosters
    # --- Round 4: new adapters ---
    ("Foal Farm",                 (51.326,  0.021)),  # Biggin Hill, Kent
    ("DBARC",                     (51.449, -0.860)),  # Hurst, Wokingham
    ("Cinque Ports",              (51.080,  0.950)),  # Kent SE
    ("AllDogsMatter",             (51.595, -0.175)),  # London N2
    ("Mayhew",                    (51.553, -0.244)),  # London NW10
    ("Greyhound and Lurcher Rescue", (52.300, -1.500)),  # aggregator; UK-wide
    ("EGLR",                      (52.092, -1.948)),  # Ashton-under-Hill, Worcs
    ("Mikey's Dog Rescue",        (50.720, -2.430)),  # Dorchester, Dorset
    # --- Distance-fix coords for existing adapter locations ---
    ("Stoke-on-Trent",            (53.003, -2.186)),  # CAESSR fosters
    ("Tetbury",                   (51.636, -2.160)),  # Spaniel Aid fosters
    ("Totton",                    (50.921, -1.490)),  # Spaniel Aid fosters near Southampton
    ("Southampton",               (50.909, -1.404)),  # plain key (in addition to Blue Cross Southampton)
    ("NESSR",                     (54.000, -2.600)),  # North of England SSR
]

# County centroids for foster-based rescues (Spaniel Aid, Oldies Club, Dogsblog)
COUNTY_COORDS: dict[str, tuple[float, float]] = {
    "hampshire": (51.07, -1.34),
    "isle of wight": (50.70, -1.30),
    "west sussex": (50.93, -0.60),
    "east sussex": (50.91,  0.29),
    "sussex": (50.92, -0.15),
    "surrey": (51.24, -0.40),
    "kent": (51.23,  0.59),
    "dorset": (50.75, -2.36),
    "berkshire": (51.45, -1.00),
    "wiltshire": (51.32, -1.89),
    "oxfordshire": (51.76, -1.27),
    "buckinghamshire": (51.81, -0.81),
    "greater london": (51.51, -0.12),
    "london": (51.51, -0.12),
    "hertfordshire": (51.81, -0.23),
    "middlesex": (51.57, -0.42),
    "essex": (51.76,  0.47),
    "somerset": (51.10, -2.92),
    "gloucestershire": (51.83, -2.23),
    "worcestershire": (52.19, -2.22),
    "warwickshire": (52.28, -1.58),
    "staffordshire": (52.88, -2.05),
    "cornwall": (50.40, -4.75),
    "devon": (50.71, -3.74),
    "dartmoor": (50.57, -3.92),
    "cambridgeshire": (52.30,  0.12),
    "norfolk": (52.76,  1.16),
    "suffolk": (52.19,  1.00),
    "bedfordshire": (52.02, -0.46),
    "northamptonshire": (52.24, -0.89),
    "leicestershire": (52.63, -1.13),
    "lincolnshire": (53.05, -0.35),
    "nottinghamshire": (53.11, -0.97),
    "derbyshire": (53.08, -1.63),
    "cheshire": (53.17, -2.55),
    "lancashire": (53.77, -2.71),
    "yorkshire": (53.96, -1.09),
    "northumberland": (55.21, -2.08),
    "cumbria": (54.58, -2.79),
    "greater manchester": (53.48, -2.24),
    "merseyside": (53.41, -2.99),
    "tyne and wear": (54.97, -1.61),
    "conwy": (53.29, -3.83),
    "denbighshire": (53.18, -3.42),
    "flintshire": (53.25, -3.13),
    "wrexham": (53.05, -2.99),
    "monmouthshire": (51.81, -2.72),
    "swansea": (51.62, -3.94),
    "cardiff": (51.48, -3.18),
    "newport": (51.58, -2.99),
    "wales": (52.13, -3.78),
    "carmarthenshire": (51.87, -4.31),
    "pembrokeshire": (51.81, -4.97),
    "ceredigion": (52.22, -4.05),
    "powys": (52.38, -3.39),
    "gwynedd": (52.92, -3.96),
    "north yorkshire": (54.22, -1.49),
    "south yorkshire": (53.54, -1.37),
    "west yorkshire": (53.72, -1.63),
    "east yorkshire": (53.93, -0.42),
    "vale of glamorgan": (51.410, -3.420),
    "scotland": (56.49, -4.20),
    "aberdeen": (57.15, -2.10),
    "aberdeenshire": (57.15, -2.50),
    "edinburgh": (55.95, -3.19),
    "glasgow": (55.86, -4.26),
    "northern ireland": (54.74, -6.49),
}


# UK postcode-area prefix → approximate coord (for when the location string
# contains only a postcode district like "LE17" or "WN6" with no resolvable
# town/county). Keys sorted longest-first at lookup time so two-letter areas
# win over their one-letter prefixes (e.g. "WC" before "W").
UK_POSTCODE_AREA_COORDS: dict[str, tuple[float, float]] = {
    # Scotland
    "AB": (57.15, -2.10), "DD": (56.46, -2.97), "DG": (55.07, -3.60),
    "EH": (55.95, -3.19), "FK": (56.00, -3.78), "G":  (55.86, -4.26),
    "HS": (57.94, -6.87), "IV": (57.48, -4.22), "KA": (55.61, -4.50),
    "KW": (58.44, -3.09), "KY": (56.18, -3.16), "ML": (55.66, -3.78),
    "PA": (56.12, -5.05), "PH": (56.70, -3.78), "TD": (55.58, -2.79),
    "ZE": (60.15, -1.15),
    # Wales
    "CF": (51.48, -3.18), "LD": (52.14, -3.38), "LL": (53.08, -3.93),
    "NP": (51.58, -2.99), "SA": (51.62, -3.94), "SY": (52.71, -2.75),
    # N. Ireland
    "BT": (54.60, -6.00),
    # N England
    "BB": (53.75, -2.48), "BD": (53.80, -1.75), "BL": (53.59, -2.43),
    "CA": (54.66, -2.93), "CH": (53.19, -2.89), "CW": (53.19, -2.38),
    "DH": (54.78, -1.58), "DL": (54.53, -1.55), "DN": (53.52, -1.13),
    "FY": (53.82, -3.03), "HD": (53.65, -1.78), "HG": (54.00, -1.54),
    "HU": (53.75, -0.34), "HX": (53.72, -1.86), "LA": (54.05, -2.80),
    "LS": (53.80, -1.55), "M":  (53.48, -2.24), "NE": (54.98, -1.61),
    "OL": (53.54, -2.12), "PR": (53.76, -2.70), "S":  (53.38, -1.47),
    "SK": (53.41, -2.15), "SR": (54.91, -1.38), "TS": (54.57, -1.23),
    "WA": (53.39, -2.59), "WF": (53.68, -1.50), "WN": (53.55, -2.63),
    "YO": (53.96, -1.09),
    # Midlands
    "B":  (52.48, -1.90), "CV": (52.41, -1.52), "DE": (52.92, -1.48),
    "DY": (52.41, -2.14), "HR": (52.06, -2.72), "LE": (52.63, -1.13),
    "LN": (53.23, -0.54), "NG": (52.95, -1.15), "NN": (52.24, -0.89),
    "ST": (52.81, -2.11), "WR": (52.19, -2.22), "WS": (52.61, -1.98),
    "WV": (52.59, -2.13),
    # East of England
    "AL": (51.75, -0.33), "CB": (52.21,  0.13), "CM": (51.73,  0.47),
    "CO": (51.89,  0.90), "EN": (51.65,  0.04), "HP": (51.74, -0.75),
    "IG": (51.56,  0.08), "IP": (52.06,  1.16), "LU": (51.88, -0.42),
    "MK": (52.04, -0.76), "NR": (52.63,  1.30), "PE": (52.57, -0.24),
    "RM": (51.56,  0.18), "SG": (51.90, -0.20), "SS": (51.58,  0.72),
    # SE + London
    "BN": (50.83, -0.14), "BR": (51.41,  0.05), "CR": (51.37, -0.10),
    "CT": (51.28,  1.08), "DA": (51.45,  0.22), "E":  (51.53,  0.04),
    "EC": (51.52, -0.10), "GU": (51.24, -0.58), "HA": (51.58, -0.34),
    "KT": (51.40, -0.30), "ME": (51.39,  0.52), "N":  (51.57, -0.11),
    "NW": (51.55, -0.20), "OX": (51.75, -1.26), "PO": (50.80, -1.08),
    "RG": (51.46, -1.00), "RH": (51.10, -0.17), "SE": (51.48, -0.06),
    "SL": (51.51, -0.59), "SM": (51.36, -0.19), "SO": (50.91, -1.40),
    "SW": (51.46, -0.17), "TN": (51.13,  0.27), "TW": (51.47, -0.33),
    "UB": (51.56, -0.42), "W":  (51.51, -0.22), "WC": (51.52, -0.12),
    "WD": (51.66, -0.39),
    # SW England
    "BA": (51.33, -2.39), "BH": (50.72, -1.88), "BS": (51.46, -2.58),
    "DT": (50.72, -2.44), "EX": (50.72, -3.53), "GL": (51.86, -2.24),
    "PL": (50.37, -4.14), "SN": (51.55, -1.78), "SP": (51.07, -1.79),
    "TA": (51.02, -3.10), "TQ": (50.47, -3.54), "TR": (50.27, -5.06),
}


def resolve_distance(location: str | None) -> float | None:
    """Return miles from PO11 for a given location string, or None if unknown."""
    if not location:
        return None
    loc_l = location.lower()
    for key, (lat, lon) in CENTRE_COORDS:
        if key.lower() in loc_l:
            return round(_haversine_miles(lat, lon, PO11_LAT, PO11_LON), 1)
    for county, (lat, lon) in COUNTY_COORDS.items():
        if re.search(rf"\b{re.escape(county)}\b", loc_l):
            return round(_haversine_miles(lat, lon, PO11_LAT, PO11_LON), 1)
    # Postcode-area fallback — match the area letters at the start of a UK
    # postcode-district token (e.g. "WN6", "LE17", "SO16 1AB"). Case-insensitive.
    pc_match = re.search(r"\b([A-Z]{1,2})\d[A-Z0-9]?\b", location.upper())
    if pc_match:
        area = pc_match.group(1)
        # Prefer two-letter area match over one-letter (e.g. "WC" before "W")
        for try_area in (area, area[:1]) if len(area) > 1 else (area,):
            if try_area in UK_POSTCODE_AREA_COORDS:
                lat, lon = UK_POSTCODE_AREA_COORDS[try_area]
                return round(_haversine_miles(lat, lon, PO11_LAT, PO11_LON), 1)
    return None

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-GB,en;q=0.9",
}
TIMEOUT = 25


def _ddg_site_search(site_path: str, extra: str = "") -> list[tuple[str, str, str]]:
    """Scrape DuckDuckGo HTML results for a site-restricted query.

    Fallback for origin-blocked rescues (IP/ASN blocks at the origin that no
    browser fingerprint defeats). Returns (url, title, snippet) triples.
    DDG is more tolerant of scrapers than Brave (which 429s aggressively).
    """
    ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    headers = {"User-Agent": ua, "Accept-Language": "en-GB,en;q=0.9"}
    q = f"site:{site_path} {extra}".strip()
    try:
        r = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": q},
            headers=headers,
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        print(f"[DDG] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[DDG] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for res in soup.select("div.result"):
        a = res.select_one("a.result__a")
        if not a:
            continue
        href = a.get("href", "")
        if site_path not in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        title = a.get_text(" ", strip=True)
        snip_el = res.select_one("a.result__snippet, div.result__snippet")
        snippet = snip_el.get_text(" ", strip=True) if snip_el else ""
        out.append((href, title, snippet))
    return out


def _brave_site_search(site_path: str, extra: str = "", max_offset: int = 20) -> list[tuple[str, str]]:
    """Scrape Brave Search results for site-restricted queries.

    Secondary fallback (DDG is primary). Brave aggressively 429s on bursts;
    prefer DDG unless Brave is specifically needed for better breed-snippet recall.
    """
    out: list[tuple[str, str]] = []
    ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    headers = {"User-Agent": ua, "Accept-Language": "en-GB,en;q=0.9"}
    offset = 0
    while offset <= max_offset:
        q = f"site:{site_path} {extra}".strip()
        url = (f"https://search.brave.com/search?"
               f"q={requests.utils.quote(q)}&source=web&offset={offset}")
        try:
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"[Brave] {e}", file=sys.stderr)
            break
        if r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, "lxml")
        cards = soup.select('div[data-type="web"], div.snippet')
        if not cards:
            break
        page_hits = 0
        for card in cards:
            a = card.find("a", href=True)
            if not a:
                continue
            href = a["href"]
            if site_path not in href:
                continue
            text = card.get_text(" ", strip=True)
            out.append((href, text))
            page_hits += 1
        if page_hits == 0:
            break
        offset += 10
        time.sleep(1.5)
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for href, text in out:
        if href in seen:
            continue
        seen.add(href)
        uniq.append((href, text))
    return uniq


@dataclass
class Dog:
    source: str
    name: str
    breed: str
    age: str | None
    sex: str | None
    location: str | None
    url: str
    in_range: bool
    reserved: bool = False
    distance_miles: float | None = None

    def __post_init__(self) -> None:
        if self.distance_miles is None:
            self.distance_miles = resolve_distance(self.location)


_BREED_PATTERNS = [
    re.compile(r"border\s*collie", re.I),
    re.compile(r"\bbearded\s*collie\b", re.I),
    re.compile(r"\bbeardie\b", re.I),
    re.compile(r"\bcollie\b", re.I),
    re.compile(r"springer\s*spaniel", re.I),
    re.compile(r"\bspringer\b", re.I),
    re.compile(r"\bsprollie\b", re.I),
    re.compile(r"cocker\s*spaniel", re.I),
    re.compile(r"\bcocker\b", re.I),
    re.compile(r"\bsprocker\b", re.I),
    re.compile(r"\bspaniel\b", re.I),
]


def matches_breed(text: str) -> bool:
    return any(p.search(text) for p in _BREED_PATTERNS)


def matches_breed_ai(text: str) -> bool:
    """Regex-first, AI fallback. Adapters can opt in per-call. Only consults
    Claude when the regex returns False AND the text is long enough to be a
    real description. No-ops when ANTHROPIC_API_KEY is unset."""
    if matches_breed(text):
        return True
    if len(text or "") < 40:
        return False
    try:
        from ai_helpers import ai_breed_check
    except ImportError:
        return False
    return ai_breed_check(text).match


# Breed noise — dogs that slip through the `\bspaniel\b` / `\bcocker\b`
# broadening but aren't what the user wants (pure Cavalier King Charles,
# Cockapoos). A strong-wanted keyword on the same breed string overrides
# the exclusion so crosses like "Cavalier x Springer" are still kept.
# Note: "cocker" is NOT a strong-wanted keyword here, because "Cockapoo
# (Cocker spaniel x Poodle)" contains it and would escape the filter.
_BREED_NOISE_STRONG_KEEP = re.compile(
    r"border\s*collie|\bcollie\b|\bspringer\b|\bsprocker\b|\bsprollie\b",
    re.I,
)
_BREED_NOISE_EXCLUDE = re.compile(r"cockapoo|\bcavalier\b", re.I)


def is_breed_noise(breed: str) -> bool:
    """True for Cavalier/Cockapoo listings that aren't genuine BC/Springer
    crosses. See adapter_url_gotchas.md / breed_audit.md for context."""
    if not breed:
        return False
    if _BREED_NOISE_STRONG_KEEP.search(breed):
        return False
    return bool(_BREED_NOISE_EXCLUDE.search(breed))


_BREED_EXTRACT_RE = re.compile(
    r"\b("
    r"(?:Pure(?:bred)?\s+)?"
    r"(?:Border\s+Collie|Rough\s+Collie|Smooth\s+Collie|Bearded\s+Collie|"
    r"Welsh\s+Springer\s+Spaniel|English\s+Springer\s+Spaniel|Springer\s+Spaniel|"
    r"Cocker\s+Spaniel|Sprocker|Sprollie|Collie|Springer|Spaniel)"
    r"(?:\s+(?:cross|mix))?"
    r"(?:\s+x\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)?"
    r")",
    re.I,
)


def extract_breed(text: str, fallback: str = "Collie/Springer match") -> str:
    m = _BREED_EXTRACT_RE.search(text)
    return m.group(1).strip() if m else fallback


def iter_h2_bodies(main, skip_re):
    """Yield (heading_text, body_text) pairs by walking document-order text
    between consecutive <h2> elements. Works when h2s live under unrelated
    parents (siblings-based walk fails)."""
    headings = main.find_all("h2")
    for i, h in enumerate(headings):
        name = h.get_text(" ", strip=True)
        if not name or (skip_re is not None and skip_re.search(name)):
            continue
        next_h = headings[i + 1] if i + 1 < len(headings) else None
        body_parts: list[str] = []
        for node in h.find_all_next(string=True):
            if next_h is not None:
                anc = node.parent
                stop = False
                while anc is not None:
                    if anc is next_h:
                        stop = True
                        break
                    anc = anc.parent
                if stop:
                    break
            txt = str(node).strip()
            if txt:
                body_parts.append(txt)
        yield name, " ".join(body_parts)


def age_from_dob(iso_date: str | None) -> str | None:
    if not iso_date:
        return None
    try:
        d = dt.date.fromisoformat(iso_date)
    except ValueError:
        return None
    days = (dt.date.today() - d).days
    if days < 0:
        return None
    years = days // 365
    months = (days % 365) // 30
    if years == 0:
        return f"{months} months"
    if months == 0:
        return f"{years} years"
    return f"{years}y {months}m"


# =====================================================================
# Dogsblog (WordPress aggregator)
# =====================================================================

IN_RANGE_REGIONS = {
    "south-east-england", "south-west-england", "london", "east-of-england",
}
OUT_OF_RANGE_REGIONS = {
    "north-east-england", "north-west-england", "yorkshire-and-the-humber",
    "east-midlands", "west-midlands", "east-anglia-england",
    "wales", "scotland", "northern-ireland", "outside-of-uk",
}

DOGSBLOG_CATEGORIES = [
    ("Border Collie", "border-collie"),
    ("Border Collie Cross", "border-collie-cross"),
    ("Collie Cross", "collie-cross"),
    ("Springer Spaniel", "springer-spaniel"),
    ("Springer Spaniel Cross", "springer-spaniel-cross"),
    ("Cocker Spaniel Cross", "cocker-spaniel-cross"),
    ("Spaniel Cross", "spaniel-cross"),
]


def _dogsblog_region(classes: list[str]) -> str | None:
    for c in classes:
        if c.startswith("tag-located-"):
            return c[len("tag-located-"):]
    return None


def _dogsblog_meta(classes: list[str]) -> tuple[str | None, str | None]:
    sex = age = None
    for c in classes:
        if c == "category-male":
            sex = "Male"
        elif c == "category-female":
            sex = "Female"
        else:
            m = re.match(r"^category-(\d[\d-]*)-years$", c)
            if m:
                raw = m.group(1)
                age = f"{raw}+ years" if raw == "6" else f"{raw} years"
    return age, sex


def fetch_dogsblog() -> list[Dog]:
    dogs: list[Dog] = []
    seen: set[str] = set()
    for breed_label, slug in DOGSBLOG_CATEGORIES:
        url = f"https://www.dogsblog.com/category/breed/{slug}/"
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"[Dogsblog] {slug}: {e}", file=sys.stderr)
            continue
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for art in soup.find_all("article"):
            link = art.find("a", href=True)
            if not link:
                continue
            href = link["href"]
            if href in seen:
                continue
            seen.add(href)

            h = art.find(["h1", "h2", "h3"])
            title = h.get_text(strip=True) if h else "Unknown"
            if title.lower().startswith(("(rehomed)", "rehomed")):
                continue
            # Headlines look like "Floki – 8 year old male Border Collie"; keep just the name.
            name = re.split(r"\s+[–—-]\s+", title, maxsplit=1)[0].strip() or title

            classes = art.get("class") or []
            region = _dogsblog_region(classes)
            if region in OUT_OF_RANGE_REGIONS:
                continue
            in_range = region in IN_RANGE_REGIONS
            age, sex = _dogsblog_meta(classes)
            loc_text = region.replace("-", " ").title() if region else "Location not tagged"

            dogs.append(Dog(
                source="Dogsblog",
                name=name,
                breed=breed_label,
                age=age,
                sex=sex,
                location=loc_text,
                url=href,
                in_range=in_range,
            ))

    # Second pass: for any dog at a vague region (or untagged), fetch the
    # detail page and look for a finer location in the meta description or
    # og:title only. Listing-level region tags are often too coarse
    # ("South East England") or missing entirely for Romanian/Hungarian imports.
    # Deliberately avoid scanning the full body — Dogsblog sidebars contain
    # "Dogs in <County>" widgets that false-positive heavily.
    def _probe_fine_location(d: Dog) -> None:
        if d.location and d.location != "Location not tagged":
            if not re.search(r"\b(South East|South West|North East|North West|East Midlands|West Midlands|East Of England)\b", d.location, re.I):
                return
        try:
            rr = requests.get(d.url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return
        if rr.status_code != 200:
            return
        ss = BeautifulSoup(rr.text, "lxml")
        signals: list[str] = []
        for tag, attr in [
            (ss.find("meta", attrs={"name": "description"}), "content"),
            (ss.find("meta", property="og:description"), "content"),
            (ss.find("meta", property="og:title"), "content"),
            (ss.find("title"), None),
        ]:
            if not tag:
                continue
            val = tag.get(attr) if attr else tag.get_text(" ", strip=True)
            if val:
                signals.append(val)
        if not signals:
            return
        blob = " | ".join(signals)
        # (a) "foster in <Location>" / "based in <Location>"
        m = re.search(
            r"(?:currently\s+)?(?:in\s+)?foster(?:ed)?\s+in\s+([A-Z][A-Za-z\- ]+?)(?:[,.]|\s+UK|\s+England|\s+Wales|\s+Scotland)",
            blob,
        ) or re.search(
            r"\bbased\s+in\s+([A-Z][A-Za-z\- ]+?)(?:[,.]|\s+UK|\s+England|\s+Wales|\s+Scotland)",
            blob,
        )
        if m:
            refined = m.group(1).strip()
            dist = resolve_distance(refined)
            if dist is not None:
                d.location = refined
                d.distance_miles = dist
                return
        # (b) originating-rescue name in metadata — check if any known county
        # keyword appears alongside "rescue/sanctuary/rehoming/adoption" in the
        # meta-only blob. Constrained to meta tags, so sidebar noise is excluded.
        for county in list(COUNTY_COORDS.keys()):
            if re.search(
                rf"\b(?:rescue|sanctuary|rehoming|adoption)\b[^.|]{{0,60}}\b{re.escape(county)}\b",
                blob, re.I,
            ) or re.search(
                rf"\b{re.escape(county)}\b[^.|]{{0,60}}\b(?:rescue|sanctuary|rehoming|adoption)\b",
                blob, re.I,
            ):
                lat, lon = COUNTY_COORDS[county]
                d.location = county.title()
                d.distance_miles = round(_haversine_miles(lat, lon, PO11_LAT, PO11_LON), 1)
                return

    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(_probe_fine_location, dogs))
    return dogs


# =====================================================================
# Battersea (Drupal, server-rendered)
# =====================================================================

def _battersea_centre(dog_url: str) -> str | None:
    try:
        r = requests.get(dog_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    text = BeautifulSoup(r.text, "lxml").get_text(" ", strip=True)
    m = re.search(r"\b(Old Windsor|Brands Hatch|London)\b", text)
    return m.group(1) if m else None


def fetch_battersea() -> list[Dog]:
    candidates: list[tuple[str, str, str | None, str, bool]] = []
    base = "https://www.battersea.org.uk/dogs/dog-rehoming-gallery"
    for page in range(30):
        url = base if page == 0 else f"{base}?page={page}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"[Battersea] page {page}: {e}", file=sys.stderr)
            break
        if r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, "lxml")
        cards = soup.select(".card-animal")
        if not cards:
            break
        for c in cards:
            name_el = c.select_one(".card-title")
            breed_el = c.select_one(".breed-name")
            age_el = c.select_one(".animal-age")
            link = c.find("a", href=True)
            if not link or not name_el:
                continue
            name = name_el.get_text(strip=True)
            breed_raw = breed_el.get_text(strip=True) if breed_el else ""
            age_raw = age_el.get_text(" ", strip=True) if age_el else ""
            age = re.sub(r"^Age\s*", "", age_raw).strip() or None
            if not (matches_breed(breed_raw) or matches_breed(name)):
                continue
            reserved = "reserved" in c.get_text(" ").lower()
            candidates.append((name, breed_raw, age, link["href"], reserved))

    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=4) as ex:
        centres = list(ex.map(lambda c: _battersea_centre(c[3]), candidates))
    for (name, breed_raw, age, href, reserved), centre in zip(candidates, centres):
        location = f"Battersea {centre}" if centre else "Battersea (London / Old Windsor / Brands Hatch)"
        dogs.append(Dog(
            source="Battersea",
            name=name,
            breed=breed_raw,
            age=age,
            sex=None,
            location=location,
            url=href,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


# =====================================================================
# Dogs Trust (Apollo GraphQL)
# =====================================================================

DT_GRAPHQL = "https://www.dogstrust.org.uk/api/df-search/graphql?cacheKey=0"
DT_QUERY = """query SearchFilterDogs($page: Int, $sort: String, $breed: [String], $age: [String], $size: [String], $gender: [String], $centres: [String], $daysSinceAdded: Int, $liveWithCats: Boolean, $liveWithDogs: Boolean, $liveWithPreschool: Boolean, $liveWithPrimary: Boolean, $liveWithSecondary: Boolean, $isUnderdog: Boolean, $noReserved: Boolean, $searchFrom: PlaceInput) {
  results: searchFilterDogs(
    where: {page: $page, sort: $sort, breed: $breed, age: $age, size: $size, gender: $gender, centres: $centres, daysSinceAdded: $daysSinceAdded, liveWithCats: $liveWithCats, liveWithDogs: $liveWithDogs, liveWithPreschool: $liveWithPreschool, liveWithPrimary: $liveWithPrimary, liveWithSecondary: $liveWithSecondary, isUnderdog: $isUnderdog, noReserved: $noReserved, searchFrom: $searchFrom}
  ) {
    totalResults
    numberOfPages
    results { apiKey name url gender dob breed isCrossBreed centreCode status frontEndBreedName isReserved }
  }
}"""

# Centres within ~100mi of PO11 (computed from their published lat/lon):
#   Shoreham 31, Salisbury 41, Newbury 46, Harefield 60, Basildon 85, Canterbury 97, Evesham 97
DT_IN_RANGE_CENTRES = ["SHO", "SAL", "NEW", "HAR", "BAS", "CAN", "EVE"]
DT_CENTRE_NAMES = {
    "SHO": "Dogs Trust Shoreham (Sussex)",
    "SAL": "Dogs Trust Salisbury (Wiltshire)",
    "NEW": "Dogs Trust Newbury (Berkshire)",
    "HAR": "Dogs Trust Harefield (West London)",
    "BAS": "Dogs Trust Basildon (Essex)",
    "CAN": "Dogs Trust Canterbury (Kent)",
    "EVE": "Dogs Trust Evesham (Worcestershire)",
}
DT_TARGET_BREEDS = ["Collie (Border)", "Spaniel (English Springer)"]


def _dt_enrich_cross(dog_url: str) -> str | None:
    """Fetch page-data.json for a Dogs Trust dog to get the cross-breed parent.
    Returns the parent breed name, or None on failure."""
    try:
        r = requests.get(
            f"https://www.dogstrust.org.uk/page-data{dog_url}/page-data.json",
            headers=HEADERS, timeout=10,
        )
        if r.status_code != 200:
            return None
        payload = r.json()
        dog_data = payload.get("result", {}).get("data", {}).get("dogData", {})
        xb = dog_data.get("relationships", {}).get("field_crossbreed") or {}
        return xb.get("name")
    except Exception:
        return None


def fetch_dogs_trust() -> list[Dog]:
    dogs: list[Dog] = []
    for page in range(20):
        body = {
            "operationName": "SearchFilterDogs",
            "query": DT_QUERY,
            "variables": {
                "page": page, "sort": "NEW",
                "breed": DT_TARGET_BREEDS,
                "centres": DT_IN_RANGE_CENTRES,
                "age": [], "size": [], "gender": [],
                "liveWithCats": False, "liveWithDogs": False,
                "liveWithPreschool": False, "liveWithPrimary": False,
                "liveWithSecondary": False,
            },
        }
        try:
            r = requests.post(DT_GRAPHQL, json=body, headers={
                "User-Agent": UA,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Origin": "https://www.dogstrust.org.uk",
                "Referer": "https://www.dogstrust.org.uk/rehoming/dogs",
            }, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"[Dogs Trust] page {page}: {e}", file=sys.stderr)
            break
        if r.status_code != 200:
            break
        payload = r.json()
        if isinstance(payload, list):
            payload = payload[0]
        results = payload.get("data", {}).get("results", {}).get("results", []) or []
        if not results:
            break
        for d in results:
            primary = d.get("frontEndBreedName") or d.get("breed", "")
            # If it's a cross and primary isn't Collie/Spaniel, spell out the
            # cross parent (which our filter guarantees is Collie/Springer)
            if d.get("isCrossBreed") and not re.search(r"Collie|Spaniel", primary):
                cross_parent = _dt_enrich_cross(d.get("url", ""))
                if cross_parent:
                    display_breed = f"{primary} × {cross_parent}"
                else:
                    display_breed = f"{primary} (× Collie/Springer)"
            else:
                display_breed = primary
            dogs.append(Dog(
                source="Dogs Trust",
                name=d.get("name", "Unknown"),
                breed=display_breed,
                age=age_from_dob(d.get("dob")),
                sex="Male" if d.get("gender") == "M" else "Female" if d.get("gender") == "F" else None,
                location=DT_CENTRE_NAMES.get(d.get("centreCode"), d.get("centreCode")),
                url="https://www.dogstrust.org.uk" + d.get("url", ""),
                in_range=True,
                reserved=bool(d.get("isReserved")),
            ))
        if len(results) < 15:
            break
    return dogs


# =====================================================================
# Blue Cross (JS-rendered, needs Playwright)
# =====================================================================

def fetch_blue_cross() -> list[Dog]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[Blue Cross] playwright not installed, skipping", file=sys.stderr)
        return []

    # Blue Cross has Southampton + Burford in range; their site shows all UK
    # dogs mixed. Without per-dog centre info in the card, we can't filter
    # server-side — include everything matching breed, user can check centre
    # on the listing page.
    dogs: list[Dog] = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            ctx = b.new_context(user_agent=UA, locale="en-GB",
                                viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            try:
                page.goto("https://www.bluecross.org.uk/rehome/dog",
                          timeout=45000, wait_until="domcontentloaded")
                page.wait_for_selector("article a[href*='/pet/']", timeout=15000)
                page.wait_for_timeout(1500)
                articles = page.locator("article").element_handles()
                for art in articles:
                    try:
                        txt = art.inner_text()
                    except Exception:
                        continue
                    # Expected layout: "Name\nBreed\n{breed}\n[Cross\n]Pet Sex\n{sex}\nAge\n{age}"
                    if not matches_breed(txt):
                        continue
                    a = art.query_selector("a[href*='/pet/']")
                    href = a.get_attribute("href") if a else None
                    if not href:
                        continue
                    if href.startswith("/"):
                        href = "https://www.bluecross.org.uk" + href
                    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
                    # Drop leading badge tokens (Reserved/On Hold/New/Star etc.)
                    # so they don't get mistaken for the dog's name.
                    BADGE_RX = re.compile(
                        r"^\s*(reserved|on\s*hold|new|star\s*dog|pending|adopted|rehomed|homed)\b",
                        re.I,
                    )
                    reserved = False
                    while lines and BADGE_RX.match(lines[0]):
                        if re.match(r"^\s*(reserved|on\s*hold|pending|adopted|rehomed|homed)\b",
                                    lines[0], re.I):
                            reserved = True
                        lines.pop(0)
                    name = lines[0] if lines else "Unknown"
                    breed = _pick_after(lines, "Breed") or ""
                    if _pick_after(lines, "Breed", offset=2) == "Cross":
                        breed += " Cross"
                    sex = _pick_after(lines, "Pet Sex")
                    age = _pick_after(lines, "Age")
                    dogs.append(Dog(
                        source="Blue Cross",
                        name=name,
                        breed=breed or "—",
                        age=age,
                        sex=sex,
                        location="Blue Cross (check listing for centre)",
                        url=href,
                        in_range=True,
                        reserved=reserved,
                    ))
            finally:
                page.close()
        finally:
            b.close()
    # Enrich each dog with its specific rehoming centre by probing the detail
    # page — the listing cards don't expose centre info, but the detail page
    # always has an <a href="/<slug>-rehoming-centre"> link. Parallelised.
    def _probe_centre(d: Dog) -> None:
        try:
            rd = requests.get(d.url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return
        if rd.status_code != 200:
            return
        m = re.search(
            r'href="/([A-Za-z0-9\-]+)-rehoming-centre"',
            rd.text,
        )
        if not m:
            return
        slug = m.group(1)
        # Slugs look like "suffolk", "oxfordshire-burford", "southampton",
        # "kimpton", "lewknor", "newport-mon", "rolleston", "tiverton".
        # Resolve to a human-readable centre name (title-case words, strip
        # county prefixes we don't need in the user-visible label).
        words = slug.split("-")
        pretty = " ".join(w.title() for w in words)
        d.location = f"Blue Cross {pretty}"
        d.distance_miles = resolve_distance(d.location)

    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(_probe_centre, dogs))
    return dogs


def _pick_after(lines: list[str], label: str, offset: int = 1) -> str | None:
    for i, ln in enumerate(lines):
        if ln == label and i + offset < len(lines):
            return lines[i + offset]
    return None


# =====================================================================
# NAWT (National Animal Welfare Trust)
# =====================================================================

# In-range centres: Trindledown (RG17, ~50mi), Heathlands Hemel (HP2, ~80mi),
# Watford (WD25, ~75mi). Out of range: Clacton, Somerset (borderline).
NAWT_IN_RANGE_COUNTIES = {
    "berkshire", "hertfordshire", "buckinghamshire", "oxfordshire",
    "hampshire", "surrey", "west sussex", "east sussex", "kent", "london",
    "dorset", "wiltshire", "somerset",  # borderline but accept
}
NAWT_OUT_OF_RANGE_COUNTIES = {
    "essex", "norfolk", "suffolk", "cornwall", "devon", "cambridgeshire",
}


def fetch_nawt() -> list[Dog]:
    dogs: list[Dog] = []
    for page in range(1, 20):
        url = "https://www.nawt.org.uk/rehoming/dogs/" + (f"?page={page}" if page > 1 else "")
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"[NAWT] page {page}: {e}", file=sys.stderr)
            break
        if r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, "lxml")
        cards = soup.select(".page-cards__card")
        if not cards:
            break
        found_any = False
        for card in cards:
            link = card.find("a", href=lambda h: h and "/rehoming/animal/" in h)
            href = link["href"] if link else None
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.nawt.org.uk" + href
            txt = card.get_text(" ", strip=True)
            # Typical text: "Leon available Cornwall 4 years 0 months, Male Border Collie Cross"
            if not matches_breed(txt):
                continue
            # Parse: first token is name (ends before "available"), then county, age, sex, breed
            m = re.match(
                r"^(?P<name>[^,]+?)\s+(?:available|reserved)\s+"
                r"(?P<county>[A-Za-z ]+?)\s+"
                r"(?P<age>\d+\s*years?\s*\d*\s*months?)\s*,?\s*"
                r"(?P<sex>Male|Female)\s+"
                r"(?P<breed>.+?)\s*(?:Find out more|$)",
                txt,
            )
            if m:
                name = m.group("name").strip()
                county = m.group("county").strip()
                age = m.group("age").strip()
                sex = m.group("sex")
                breed = m.group("breed").strip()
            else:
                # Fallback: take first word as name
                toks = txt.split()
                name = toks[0] if toks else "Unknown"
                county = None
                age = sex = None
                breed = "—"
            county_l = (county or "").lower()
            if county_l in NAWT_OUT_OF_RANGE_COUNTIES:
                continue
            in_range = county_l in NAWT_IN_RANGE_COUNTIES
            dogs.append(Dog(
                source="NAWT",
                name=name,
                breed=breed,
                age=age,
                sex=sex,
                location=f"NAWT {county}" if county else "NAWT",
                url=href,
                in_range=in_range,
                reserved="reserved" in txt.lower(),
            ))
            found_any = True
        if not found_any and page > 1:
            break
    return dogs


# =====================================================================
# Wiccaweys (Border Collie specialist, Gillingham Dorset ~60mi)
# =====================================================================

def fetch_wiccaweys() -> list[Dog]:
    url = "https://www.wiccaweys.co.uk/rehome/dogs/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Wiccaweys] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Wiccaweys"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/dog/" not in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        # name from anchor text, or the slug
        name = a.get_text(strip=True)
        if not name or name.lower() in ("read more", "more"):
            # derive from slug
            slug = href.rstrip("/").rsplit("/", 1)[-1]
            name = slug.replace("-", " ").title()
        dogs.append(Dog(
            source="Wiccaweys",
            name=name,
            breed="Border Collie (rescue specialist)",
            age=None,
            sex=None,
            location="Wiccaweys (Gillingham, Dorset)",
            url=href,
            in_range=True,
        ))
    return dogs


# =====================================================================
# ESSW (English Springer Spaniel Welfare) — single-page, all sections
# =====================================================================

def fetch_essw() -> list[Dog]:
    url = "https://essw.co.uk/"
    r = None
    for attempt in range(2):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            break
        except requests.RequestException as e:
            if attempt == 1:
                print(f"[ESSW] {e}", file=sys.stderr)
                return []
    if r is None or r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    # Pre-scan all h4 Location tags and associate each with its nearest
    # preceding h2 (ESSW renders a detailed profile modal for featured dogs
    # which carries a "Location: Region" h4 — use it when present).
    loc_by_name: dict[str, str] = {}
    for h4 in soup.find_all("h4"):
        t4 = h4.get_text(" ", strip=True)
        m4 = re.match(r"Location\s*:\s*(.{2,120})", t4)
        if not m4:
            continue
        prev_h2 = h4.find_previous("h2")
        if prev_h2 is not None:
            key = prev_h2.get_text(strip=True)
            if key and len(key) <= 30:
                loc_by_name[key] = m4.group(1).strip()
    # Each dog is an h2 or h3 heading with a following paragraph describing them.
    for h in soup.find_all(["h2", "h3"]):
        name = h.get_text(strip=True)
        if not name or len(name) > 30:
            continue
        if name.lower() in {"donate", "sponsor", "newsletter", "contact",
                            "privacy", "english springer spaniel welfare",
                            "home", "about", "our dogs", "about us"}:
            continue
        # The sibling paragraph usually describes them
        ctx_el = h.find_next(["p", "div"])
        ctx = ctx_el.get_text(" ", strip=True)[:300] if ctx_el else ""
        if len(ctx) < 40:
            continue
        # All ESSW dogs live on the landing page — give each a unique fragment
        # so the scan-level URL dedup doesn't collapse them into one entry.
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower() or "dog"
        region = loc_by_name.get(name)
        if not region:
            # Fallback: scan the dog's narrative paragraph for any UK
            # county/region keyword. ESSW sponsor-dog descriptions sometimes
            # mention locations directly (e.g. "came to me in Devon",
            # "Dartmoor walks"). Be conservative — only accept explicit
            # county/region words we know the coordinates of.
            longer_ctx = ctx_el.get_text(" ", strip=True) if ctx_el else ""
            for county_kw in list(COUNTY_COORDS.keys()):
                if re.search(rf"\b{re.escape(county_kw)}\b", longer_ctx, re.I):
                    region = county_kw.title()
                    break
        location = f"ESSW foster ({region})" if region else "ESSW (UK-wide fosters; enquire for closest)"
        dogs.append(Dog(
            source="ESSW",
            name=name,
            breed="English Springer Spaniel (rescue specialist)",
            age=None,
            sex=None,
            location=location,
            url=f"{url}#{slug}",
            in_range=True,
        ))
    return dogs


# =====================================================================
# Spaniel Aid UK (WordPress, LiteSpeed, UK-wide fosters)
# =====================================================================

# County → in-range flag for Spaniel Aid / Oldies Club / CAESSR location parsing.
# "in range" = rough <100mi drive of PO11 (Hayling Island).
IN_RANGE_COUNTIES = {
    "hampshire", "west sussex", "east sussex", "sussex", "surrey", "kent",
    "berkshire", "wiltshire", "dorset", "oxfordshire", "buckinghamshire",
    "greater london", "london", "hertfordshire", "middlesex", "essex",
    "somerset", "gloucestershire", "west berkshire",
}
OUT_OF_RANGE_COUNTIES = {
    "yorkshire", "north yorkshire", "south yorkshire", "west yorkshire", "east yorkshire",
    "lancashire", "cumbria", "northumberland", "durham", "tyne and wear",
    "cheshire", "merseyside", "greater manchester", "staffordshire",
    "shropshire", "herefordshire", "worcestershire", "warwickshire",
    "west midlands", "derbyshire", "nottinghamshire", "leicestershire",
    "lincolnshire", "rutland", "norfolk", "suffolk", "cambridgeshire",
    "bedfordshire", "northamptonshire", "cornwall", "devon",
    "carmarthenshire", "pembrokeshire", "ceredigion", "powys",
    "gwynedd", "conwy", "denbighshire", "flintshire", "wrexham",
    "monmouthshire", "glamorgan", "swansea", "cardiff",
    "scotland", "highland", "aberdeenshire", "fife", "perthshire",
    "lothian", "edinburgh", "glasgow", "dumfries", "galloway", "ayrshire",
    "northern ireland", "antrim", "down", "armagh", "fermanagh", "tyrone",
}


def _classify_county(location_text: str) -> bool | None:
    """Return True if county in range, False if out of range, None if unknown."""
    loc_l = location_text.lower()
    for c in OUT_OF_RANGE_COUNTIES:
        if c in loc_l:
            return False
    for c in IN_RANGE_COUNTIES:
        if c in loc_l:
            return True
    return None


def _parse_spaniel_aid_card(href: str, txt: str) -> Dog | None:
    """Parse a Spaniel Aid dog card (either origin HTML container text or
    Brave Search title+snippet) into a Dog. Returns None if breed doesn't match."""
    if not matches_breed(txt):
        return None
    reserved = bool(re.search(r"\bRESERVED\b|\bRESERVATION\b", txt))
    clean = re.sub(r"\b(?:RESERVED|RESERVATION|FOSTER VIEW TO ADOPT|VIEW TO ADOPT|ADOPTED|HOMED)\b", "", txt, flags=re.I)
    clean = re.sub(r"\s{2,}", " ", clean).strip()
    name_m = re.match(r"^([^\d]+?)\s+[A-Z]{2}\d+", clean)
    name = name_m.group(1).strip().title() if name_m else "Unknown"
    age_m = re.search(r"(\d+\s*(?:years?|months?|weeks?)(?:\s+\d+\s*months?)?)", clean)
    age = age_m.group(1) if age_m else None
    location = None
    for rx in (
        r"([A-Z][A-Za-z\-' ]+?,\s*[A-Z][A-Za-z\-' ]+?,\s*(?:England|Wales|Scotland|Northern Ireland))",
        r"([A-Z][a-zA-Z]?\d[A-Z0-9]?,\s*[A-Z][A-Za-z\-' ]+?,\s*(?:England|Wales|Scotland|Northern Ireland))",
        r"([A-Z][A-Za-z\-' ]+?,\s*[A-Z][A-Za-z\-' ]+?\s+[A-Z]{1,2}\d[A-Z0-9]?)\b",
        r"([A-Z][A-Za-z\-' ]+?,\s*(?:South|North|West|East|Mid)\s+(?:Wales|England|Scotland))",
        r"\b([A-Z][A-Za-z\-' ]+?,\s*(?:England|Wales|Scotland|Northern Ireland))\b",
    ):
        loc_m = re.search(rx, clean)
        if loc_m:
            location = loc_m.group(1)
            location = re.sub(
                r"\b([A-Z])([a-z])(\d)",
                lambda m: m.group(1) + m.group(2).upper() + m.group(3),
                location,
            )
            break
    breed_m = re.search(r"[A-Z]{2}\d+\s*(?:[^\w\s]*\s*)?([A-Za-z][A-Za-z /()\-]+?)\s+\d+\s*(?:years?|months?|weeks?)", clean)
    breed = breed_m.group(1).strip() if breed_m else "Spaniel (mix)"
    breed = re.sub(r"\b(Nearly|Almost|Approx\.?|Approximately)\s*$", "", breed).strip()
    sex_m = re.search(r"\b(England|Wales|Scotland|Northern Ireland|UK)\s+(Male|Female)\b", clean)
    sex = sex_m.group(2) if sex_m else None
    in_range = True
    if location:
        verdict = _classify_county(location)
        in_range = verdict if verdict is not None else True
    return Dog(
        source="Spaniel Aid",
        name=name,
        breed=breed,
        age=age,
        sex=sex,
        location=location or "Spaniel Aid foster (UK)",
        url=href,
        in_range=in_range,
        reserved=reserved,
    )


def _spaniel_aid_origin_cards() -> list[tuple[str, str]]:
    """Fetch Spaniel Aid origin listing and return (url, card_text) pairs.
    Empty list on HTTP error or block — caller should fall back to Brave."""
    url = "https://spanielaid.co.uk/available-dogs/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Spaniel Aid] origin: {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[Spaniel Aid] origin HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=lambda h: h and "/spaniel/" in h):
        href = a["href"]
        if href in seen:
            continue
        seen.add(href)
        container = a
        for _ in range(6):
            if container.parent:
                container = container.parent
            txt = container.get_text(" ", strip=True)
            if len(txt) > 40 and "Read more" in txt:
                break
        pairs.append((href, container.get_text(" ", strip=True)))
    return pairs


def _parse_spaniel_aid_ddg(href: str, title: str, snippet: str) -> Dog | None:
    """Parse a Spaniel Aid DDG search result into a Dog.

    DDG title format: "NAME SA#### [- RESERVED] Breed, Town, County, England | Spaniel..."
    Age lives in the snippet (e.g. "3 year old cocker spaniel")."""
    combined = f"{title} {snippet}"
    if not matches_breed(combined):
        return None
    # Strip DDG's trailing " | Spaniel..." site suffix, plus DDG's "..." truncation
    core = re.sub(r"\s*\|\s*Spaniel.*$", "", title).strip()
    core = re.sub(r"\s*\.{2,}\s*$", "", core).strip()
    reserved = "RESERVED" in core.upper() or "RESERVED" in snippet.upper()
    # Parse "NAME SA#### - RESERVED Breed, Loc..., Country"
    m = re.match(r"^([A-Z][A-Za-z'-]*)\s+SA\d+\s*[\-\s]*(?:RESERVED\s+)?(.+?)\.?\s*$",
                 core, re.I)
    if not m:
        return None
    name = m.group(1).title()
    rest = m.group(2).strip()
    # Split "Breed, LocA, LocB, Country" — country is last
    breed = "Spaniel (mix)"
    location = None
    cm = re.match(
        r"^(.+?)\s*,\s*(.+?,\s*(?:England|Wales|Scotland|Northern Ireland))\s*$",
        rest, re.I,
    )
    if cm:
        breed = cm.group(1).strip()
        location = cm.group(2).strip()
    else:
        parts = [p.strip() for p in rest.split(",")]
        if len(parts) >= 2:
            breed = parts[0]
            location = ", ".join(parts[1:])
        else:
            breed = rest
    # Age from snippet (title has none)
    age_m = re.search(r"(\d+)\s*(?:year|yr)s?\s*old", snippet, re.I)
    age = f"{age_m.group(1)} years" if age_m else None
    # Sex from snippet — pronouns
    sex = None
    if re.search(r"\bshe\b|\bher\b", snippet, re.I):
        sex = "Female"
    elif re.search(r"\bhe\b|\bhis\b|\bhim\b", snippet, re.I):
        sex = "Male"
    in_range = True
    if location:
        verdict = _classify_county(location)
        in_range = verdict if verdict is not None else True
    return Dog(
        source="Spaniel Aid",
        name=name,
        breed=breed,
        age=age,
        sex=sex,
        location=location or "Spaniel Aid foster (UK)",
        url=href,
        in_range=in_range,
        reserved=reserved,
    )


def _spaniel_aid_ddg_dogs() -> list[Dog]:
    """Fallback: enumerate Spaniel Aid dogs via DuckDuckGo when origin is blocked.
    Multiple breed-keyword passes maximise recall. Pace queries to avoid 202s."""
    triples: list[tuple[str, str, str]] = []
    # Keep query count low — DDG HTML endpoint 202s under bursts. Catch-all
    # + one narrow keyword is enough to get past the 10-result page cap.
    for i, keyword in enumerate(("", "springer")):
        if i > 0:
            time.sleep(6)
        triples.extend(_ddg_site_search("spanielaid.co.uk/spaniel/", keyword))
    seen: set[str] = set()
    dogs: list[Dog] = []
    for href, title, snippet in triples:
        if href in seen:
            continue
        seen.add(href)
        dog = _parse_spaniel_aid_ddg(href, title, snippet)
        if dog is not None:
            dogs.append(dog)
    return dogs


def fetch_spaniel_aid() -> list[Dog]:
    pairs = _spaniel_aid_origin_cards()
    if pairs:
        dogs = [_parse_spaniel_aid_card(href, txt) for href, txt in pairs]
        dogs = [d for d in dogs if d is not None]
        print(f"[Spaniel Aid] {len(dogs)} dogs via origin", file=sys.stderr)
        return dogs
    # Origin blocked (403) or empty — fall back to DDG
    dogs = _spaniel_aid_ddg_dogs()
    print(f"[Spaniel Aid] {len(dogs)} dogs via DDG fallback", file=sys.stderr)
    return dogs


# =====================================================================
# Oldies Club (WP aggregator — UK-wide older-dog fosters from many rescues)
# =====================================================================

def fetch_oldies_club() -> list[Dog]:
    dogs: list[Dog] = []
    seen: set[str] = set()
    for page in range(1, 8):  # cap at 7 pages ≈ 140 posts
        suffix = "" if page == 1 else f"page/{page}/"
        url = f"https://www.oldies.org.uk/category/adopt-an-oldie/{suffix}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"[Oldies Club] page {page}: {e}", file=sys.stderr)
            break
        if r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, "lxml")
        posts = soup.select("h2.wp-block-post-title a, h3.wp-block-post-title a")
        if not posts:
            break
        found_on_page = False
        for a in posts:
            href = a["href"]
            if href in seen:
                continue
            seen.add(href)
            title = a.get_text(" ", strip=True)
            if not matches_breed(title):
                continue
            found_on_page = True
            # Title usually: "Name (Rescue Name, fostered County)"
            paren = re.search(r"\(([^)]+)\)", title)
            location = paren.group(1).strip() if paren else None
            name = re.sub(r"\s*\([^)]+\)\s*$", "", title).strip()
            in_range = True
            if location:
                verdict = _classify_county(location)
                in_range = verdict if verdict is not None else True
            dogs.append(Dog(
                source="Oldies Club",
                name=name,
                breed="Senior dog (Collie/Springer match)",
                age=None,
                sex=None,
                location=location or "Oldies Club foster (UK)",
                url=href,
                in_range=in_range,
            ))
        if not found_on_page and page > 1:
            # Still iterate in case breed dogs appear deeper, but break if empty page
            pass
    return dogs


# =====================================================================
# CAESSR (Cocker & English Springer Spaniel Rescue, Staffordshire HQ
# but UK-wide fosters)
# =====================================================================

def fetch_caessr() -> list[Dog]:
    url = "https://www.caessr.org.uk/rehoming/dogs/needing-homes.html"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[CAESSR] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"CAESSR"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    # Strip nav/sidebar/footer
    for t in soup(["nav", "footer", "aside", "script", "style"]):
        t.decompose()
    main = soup.find(class_="t3-content") or soup.find("main") or soup.body
    if not main:
        return []
    dogs: list[Dog] = []
    # Each dog is an h3 heading in the main content (short uppercase name)
    for h in main.find_all("h3"):
        name = h.get_text(strip=True)
        if not name or len(name) > 25:
            continue
        # Skip sidebar/widget headings
        if name.lower() in {"sidebar", "login form", "featured dogs", "donate now",
                            "donate with justgiving", "welcome visitor", "popular articles",
                            "contact us"}:
            continue
        # Site wraps dogs in Bootstrap panels:
        # <div class="panel"><h3 class="panel-title"/><div class="panel-body"><p/></div></div>
        # Pull description from the enclosing .panel-body; fall back to sibling walk
        # for older templates.
        parts: list[str] = []
        panel = h.find_parent(class_="panel")
        if panel is not None:
            body_el = panel.find(class_="panel-body")
            if body_el is not None:
                parts.append(body_el.get_text(" ", strip=True))
        if not parts:
            for sib in h.find_next_siblings():
                if sib.name in ("h2", "h3", "h4"):
                    break
                if sib.name == "p":
                    parts.append(sib.get_text(" ", strip=True))
                if len(" ".join(parts)) > 1500:
                    break
        body = " ".join(parts)
        if not body or len(body) < 40:
            continue
        # Classify by county mentioned in the description
        verdict = _classify_county(body)
        in_range = verdict if verdict is not None else True
        # Pull a location hint
        loc_m = re.search(
            r"(?:foster home in|currently in|located in|based in|fostered in)\s+"
            r"([A-Z][\w\-' ]+?)"
            r"(?=[.,;!?)]|\s+(?:is|was|has|have|who|with|and|currently|where|but|for)\b|\s*$)",
            body,
        )
        location = loc_m.group(1).strip() if loc_m else "CAESSR (UK-wide fosters)"
        dogs.append(Dog(
            source="CAESSR",
            name=name.title(),
            breed="Cocker / English Springer Spaniel",
            age=None,
            sex=None,
            location=location,
            url=url + "#" + name.lower(),
            in_range=in_range,
        ))
    return dogs


# =====================================================================
# Last Chance Animal Rescue (Edenbridge Kent ~53mi, New Romney ~80mi)
# =====================================================================

def fetch_last_chance() -> list[Dog]:
    url = "https://www.lastchanceanimalrescue.co.uk/kennel/dog.php"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Last Chance] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Last Chance"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    for h in soup.find_all("h2"):
        name = h.get_text(strip=True)
        if not name or len(name) > 40:
            continue
        # Collect sibling content until next h2
        parts: list[str] = []
        sib = h
        for _ in range(8):
            sib = sib.find_next_sibling()
            if not sib or (sib.name == "h2"):
                break
            parts.append(sib.get_text(" ", strip=True))
        body = " ".join(parts)
        if not matches_breed(body):
            continue
        breed_m = re.search(r"Breed:\s*([A-Za-z \-/x]+?)(?:Colour|Location|$)", body)
        breed = breed_m.group(1).strip() if breed_m else "—"
        # Body runs "Location: New Romney Bruce was returned..." with no structural
        # separator before the description — anchor on the dog's name to extract cleanly.
        loc_m = re.search(
            rf"Location:\s*(.+?)\s+{re.escape(name)}\b",
            body,
        )
        if not loc_m:
            loc_m = re.search(
                r"Location:\s*([A-Za-z][A-Za-z \-']{2,30}?)\s+(?:[A-Z][a-z]+\s+(?:was|is|came|has|loves)|$)",
                body,
            )
        location = loc_m.group(1).strip().rstrip(",") if loc_m else "Edenbridge"
        ga_m = re.search(r"Gender/Age:\s*(Male|Female)\s*([0-9][a-z0-9 ]+?)\s+Breed", body)
        sex = ga_m.group(1) if ga_m else None
        age = ga_m.group(2).strip() if ga_m else None
        # Per-dog URL: the site uses ?id= links inside each block
        anchor = None
        for a in h.find_all_next("a", href=True):
            if "dog.php" in a["href"] and "?" in a["href"]:
                anchor = a["href"]
                break
        dog_url = anchor if anchor else url + "#" + name.replace(" ", "-")
        if dog_url.startswith("/"):
            dog_url = "https://www.lastchanceanimalrescue.co.uk" + dog_url
        dogs.append(Dog(
            source="Last Chance",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location=f"Last Chance {location}",
            url=dog_url,
            in_range=True,  # Both Edenbridge & New Romney are in-range
        ))
    return dogs


# =====================================================================
# Holbrook Animal Rescue (Horsham, West Sussex ~35mi)
# =====================================================================

def fetch_holbrook() -> list[Dog]:
    list_url = "https://www.holbrookanimalrescue.com/dogs-available-for-adoption"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Holbrook] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Holbrook"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dog_urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/dogs-for-adoption-1/" in h and not h.rstrip("/").endswith("dogs-for-adoption-1"):
            if h not in seen:
                seen.add(h)
                dog_urls.append(h)
    # Fetch each dog's page in parallel and filter by breed
    def probe(url: str) -> Dog | None:
        try:
            rr = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        if not matches_breed(text):
            return None
        # Format seen: "{Name} {Name} - {Neutered} {Sex} {Breed} Age - N years..."
        name_m = re.search(r"^([A-Za-z][A-Za-z \-']+?)\s+[A-Z]", text)
        name = name_m.group(1).strip() if name_m else url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        breed_m = re.search(r"(?:Neutered|Spayed)?\s*(?:Female|Male)\s+([A-Za-z][A-Za-z /x\-]+?)\s+Age", text)
        breed = breed_m.group(1).strip() if breed_m else "Collie/Springer match"
        age_m = re.search(r"Age\s*-\s*([0-9][a-zA-Z0-9 ]+?)(?=\s+Height|\s+Dogs|\s+Personality|$)", text)
        age = age_m.group(1).strip() if age_m else None
        sex = "Male" if re.search(r"\bMale\b", text) else ("Female" if re.search(r"\bFemale\b", text) else None)
        return Dog(
            source="Holbrook",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Holbrook (Horsham, West Sussex)",
            url=url,
            in_range=True,
        )
    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# Helping Hounds (Grayshott, Surrey/Hants border ~25mi)
# =====================================================================

def fetch_helping_hounds() -> list[Dog]:
    list_url = "https://www.helpinghoundshampshire.com/dog-information"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Helping Hounds] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Helping Hounds"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dog_urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/dog-information/" in h and not h.rstrip("/").endswith("dog-information"):
            if h not in seen:
                seen.add(h)
                dog_urls.append(h)

    def probe(url: str) -> Dog | None:
        try:
            rr = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        if not matches_breed(text):
            return None
        # Format: "See all dogs AVAILABLE {Name} {Breed} {Age} {Sex} ..."
        m = re.search(
            r"AVAILABLE\s+([A-Za-z][A-Za-z &\-']+?)\s+"
            r"([A-Za-z][A-Za-z X/&\-]+?)\s+"
            r"(\d+\s*(?:years?|months?|weeks?))\s+"
            r"(Male|Female)",
            text,
        )
        if m:
            name, breed, age, sex = m.group(1).strip(), m.group(2).strip(), m.group(3).strip(), m.group(4)
        else:
            name = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").replace("%26", "&").title()
            breed = "Collie/Springer match"
            age = None
            sex = None
        return Dog(
            source="Helping Hounds",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Helping Hounds (Grayshott, Surrey/Hants)",
            url=url,
            in_range=True,
        )
    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# The Border Collie Spot (BC specialist, Binfield Berkshire ~50mi)
# =====================================================================

def fetch_bc_spot() -> list[Dog]:
    url = "https://thebordercolliespot.com/dogs-needing-homes/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[BC Spot] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"BC Spot"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    for t in soup(["nav", "footer", "aside", "script", "style", "header"]):
        t.decompose()
    main = soup.find("main") or soup.find(class_="entry-content") or soup.body
    if not main:
        return []
    # Non-dog section headings that appear as h2 on the guide portion of the page
    NON_DOG = {
        "dogs needing homes", "before you adopt", "read our guide",
        "adopting a rescue border collie",
        "what to expect when adopting a rescue border collie",
        "training & behaviour", "training and behaviour", "ideal home",
        "in summary", "training & communication", "training and communication",
        "behaviour notes", "behavior notes", "personality", "about", "other info",
        "medical", "about the spot", "home check", "contact us",
    }
    dogs: list[Dog] = []
    for h in main.find_all("h2"):
        name = h.get_text(strip=True)
        if not name or len(name) > 30:
            continue
        if name.lower() in NON_DOG:
            continue
        # Dog name heuristic: single word, capitalized, no "&"/"and"
        if " & " in name or " and " in name.lower() or "/" in name:
            continue
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower() or "dog"
        dogs.append(Dog(
            source="Border Collie Spot",
            name=name,
            breed="Border Collie (rescue specialist)",
            age=None,
            sex=None,
            location="Border Collie Spot (Binfield, Berkshire)",
            url=f"{url}#{slug}",
            in_range=True,
        ))
    return dogs


# =====================================================================
# FOSTBC — Freedom of Spirit Trust for Border Collies (Yorks HQ, UK-wide)
# =====================================================================

def fetch_fostbc() -> list[Dog]:
    url = "https://fostbc.org.uk/dogs-for-adoption/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[FOSTBC] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"FOSTBC"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    # Dogs live in sections delimited by h3 headings. Only "Ready for adoption"
    # entries are available; Under Assessment / Home offered are excluded.
    ready_h3 = None
    for h3 in soup.find_all("h3"):
        if "ready for adoption" in h3.get_text(" ", strip=True).lower():
            ready_h3 = h3
            break
    if ready_h3 is None:
        ready_h3 = soup
    # Collect entry-title anchors that sit after Ready h3 but before next h3
    dogs: list[Dog] = []
    seen: set[str] = set()
    node = ready_h3
    # Walk forward through the document order
    all_nodes = list(soup.find_all(True))
    start_idx = 0
    if ready_h3 is not soup:
        try:
            start_idx = all_nodes.index(ready_h3)
        except ValueError:
            start_idx = 0
    # Collect h2 headings and /portfolio-item/ hrefs in document order, then
    # zip them. The image link for a dog appears BEFORE the h2 heading in each
    # section, so positional zip is more reliable than tracking a "last h2".
    # FOSTBC also reuses slugs (e.g. /portfolio-item/moss-2/ now titles "Chase"),
    # so the h2 is authoritative for the name — not the URL slug.
    headings: list[str] = []
    hrefs: list[str] = []
    for el in all_nodes[start_idx + 1:]:
        if el.name == "h3" and "ready for adoption" not in el.get_text(" ", strip=True).lower():
            break
        if el.name == "h2":
            t = el.get_text(" ", strip=True)
            if t:
                headings.append(t)
        elif el.name == "a" and el.get("href") and "/portfolio-item/" in el.get("href", ""):
            href = el["href"]
            if href not in seen:
                seen.add(href)
                hrefs.append(href)
    for name, href in zip(headings, hrefs):
        dogs.append(Dog(
            source="FOSTBC",
            name=name,
            breed="Border Collie (rescue specialist)",
            age=None,
            sex=None,
            location="FOSTBC (Boroughbridge, N. Yorkshire; UK-wide fosters)",
            url=href,
            in_range=True,
        ))
    return dogs


# =====================================================================
# PPBC — Protecting Preloved Border Collies (Wales HQ, UK-wide)
# =====================================================================

def fetch_ppbc() -> list[Dog]:
    url = "https://www.protectingprelovedbordercollies.org.uk/dogs-for-rehoming"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[PPBC] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"PPBC"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    art = soup.find("article")
    if not art:
        return []
    body = art.get_text(" ", strip=True)
    # Each dog block starts with "NAME - N months/years". Use these as split points.
    # Only match names that start with a capital and are short (not sentences).
    pattern = re.compile(
        r"\b([A-Z][a-zA-Z]{1,20}(?:\s+[A-Z][a-zA-Z]+)?)\s*[-–]\s*(\d+\s*(?:months?|years?|weeks?))\b"
    )
    matches = list(pattern.finditer(body))
    dogs: list[Dog] = []
    seen: set[str] = set()
    for m in matches:
        name = m.group(1).strip()
        age = m.group(2).strip()
        if name.lower() in {"april", "may", "june", "march", "february", "january", "july",
                            "august", "september", "october", "november", "december",
                            "monday", "tuesday", "wednesday", "thursday", "friday",
                            "saturday", "sunday", "ppbc"}:
            continue
        if name in seen:
            continue
        seen.add(name)
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower() or "dog"
        dogs.append(Dog(
            source="PPBC",
            name=name,
            breed="Border Collie (rescue specialist)",
            age=age,
            sex=None,
            location="PPBC (Ammanford, Wales; UK-wide fosters)",
            url=f"{url}#{slug}",
            in_range=True,
        ))
    return dogs


# =====================================================================
# Sprocker Assist (Sprocker = Springer × Cocker specialist, UK-wide)
# =====================================================================

def fetch_sprocker_assist() -> list[Dog]:
    url = "https://www.sprockerassist.org/dogs-available/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Sprocker Assist] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Sprocker Assist"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    for t in soup(["nav", "footer", "aside", "script", "style", "header"]):
        t.decompose()
    main = soup.find("main") or soup.find(class_="entry-content") or soup.body
    if not main:
        return []
    dogs: list[Dog] = []
    # Page structure: each dog has h2/h3 with name, plus description. When no dogs
    # are listed, main text is the standing info copy.
    for h in main.find_all(["h2", "h3"]):
        name = h.get_text(strip=True)
        if not name or len(name) > 30:
            continue
        lower = name.lower()
        if any(skip in lower for skip in [
            "sprockers looking", "looking for", "can you offer", "foster", "home",
            "sprocker assist", "our dogs", "dogs available", "adopt"
        ]):
            continue
        parts: list[str] = []
        for sib in h.find_next_siblings():
            if sib.name in ("h2", "h3"):
                break
            parts.append(sib.get_text(" ", strip=True))
            if len(" ".join(parts)) > 500:
                break
        body = " ".join(parts)
        if len(body) < 30:
            continue
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower() or "dog"
        dogs.append(Dog(
            source="Sprocker Assist",
            name=name,
            breed="Sprocker (Springer × Cocker)",
            age=None,
            sex=None,
            location="Sprocker Assist (UK-wide fosters)",
            url=f"{url}#{slug}",
            in_range=True,
        ))
    return dogs


# =====================================================================
# Ferne Animal Sanctuary (Chard, Somerset ~95mi)
# =====================================================================

def fetch_ferne() -> list[Dog]:
    list_url = "https://www.ferneanimalsanctuary.org/animals/dogs/"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Ferne] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Ferne"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dog_urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/animal/" in h and h.rstrip("/").split("/")[-1] not in ("animal",):
            if h not in seen:
                seen.add(h)
                dog_urls.append(h)

    def probe(url: str) -> Dog | None:
        try:
            rr = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        if not matches_breed(text):
            return None
        # Name from H1 or page title
        h1 = ss.find("h1")
        name = h1.get_text(strip=True) if h1 else url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        breed_m = re.search(r"Breed[:\s]+([A-Za-z][A-Za-z /x\-]+?)(?:\s{2,}|$|Age|Sex|Colour|Weight)", text, re.I)
        breed = breed_m.group(1).strip() if breed_m else "Collie/Springer match"
        age_m = re.search(r"(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?)", text)
        age = age_m.group(1) if age_m else None
        sex = "Male" if re.search(r"\bMale\b", text) else ("Female" if re.search(r"\bFemale\b", text) else None)
        return Dog(
            source="Ferne",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Ferne Animal Sanctuary (Chard, Somerset)",
            url=url,
            in_range=True,
        )
    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# Margaret Green Animal Rescue (Wareham, Dorset ~65mi)
# =====================================================================

def fetch_margaret_green() -> list[Dog]:
    url = "https://margaretgreenanimalrescue.org.uk/dog-rehoming/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Margaret Green] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Margaret Green"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    # Map each "Learn More" anchor back to its preceding text (name/breed/age/sex)
    entries: list[tuple[str, str]] = []  # (href, listing_container_text)
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/dog/" not in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        # Walk up to a small container holding this entry
        container = a
        for _ in range(5):
            if container.parent:
                container = container.parent
            txt = container.get_text(" ", strip=True)
            if 20 < len(txt) < 400:
                break
        entries.append((href, container.get_text(" ", strip=True)))

    def _probe(item: tuple[str, str]) -> tuple[str, str, object] | None:
        href, listing_txt = item
        if matches_breed(listing_txt):
            return (href, listing_txt, None)
        try:
            rd = requests.get(href, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rd.status_code != 200:
            return None
        detail = BeautifulSoup(rd.text, "lxml")
        for t in detail(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        detail_text = (detail.find("main") or detail.body or detail).get_text(" ", strip=True)
        if not matches_breed(detail_text):
            return None
        return (href, detail_text, detail.find("h1"))

    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for res in pool.map(_probe, entries):
            if not res:
                continue
            href, txt, detail_h1 = res
            if detail_h1:
                name = detail_h1.get_text(" ", strip=True) or href.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
                breed = extract_breed(txt)
                age_m = re.search(r"(\d+\s*(?:years?|months?))", txt, re.I)
                age = age_m.group(1) if age_m else None
                sex_src = txt[:400]
                sex = "Female" if re.search(r"\bshe\b|\bher\b", sex_src, re.I) else ("Male" if re.search(r"\bhe\b|\bhim\b|\bhis\b", sex_src, re.I) else None)
            else:
                txt_clean = re.sub(r"\s*\(Reserved\)\s*", " ", txt, flags=re.I)
                m = re.search(
                    r"([A-Z][A-Za-z \-&']+?)\s+"
                    r"([A-Za-z][A-Za-z X/\-]+?)\s+"
                    r"(\d+)\s*Years?\s*Old\s+"
                    r"(Male|Female)",
                    txt_clean,
                )
                if m:
                    name = m.group(1).strip()
                    breed = m.group(2).strip()
                    age = f"{m.group(3)} years"
                    sex = m.group(4)
                else:
                    name = href.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
                    breed = extract_breed(txt)
                    age_m = re.search(r"(\d+\s*(?:years?|months?))", txt, re.I)
                    age = age_m.group(1) if age_m else None
                    sex_src = txt[:400]
                    sex = "Female" if re.search(r"\bshe\b|\bher\b", sex_src, re.I) else ("Male" if re.search(r"\bhe\b|\bhim\b|\bhis\b", sex_src, re.I) else None)
            reserved = bool(re.search(r"reserved", txt, re.I))
            dogs.append(Dog(
                source="Margaret Green",
                name=re.sub(r"\s*\(Reserved\)\s*", "", name, flags=re.I).strip(),
                breed=breed,
                age=age,
                sex=sex,
                location="Margaret Green (Wareham, Dorset)",
                url=href,
                in_range=True,
                reserved=reserved,
            ))
    return dogs


# =====================================================================
# RRAUK — Romanian Rescue Appeal UK (Hampshire & Bordon fosters)
# =====================================================================

def fetch_rrauk() -> list[Dog]:
    url = "https://rrauk.com/our-rescue-dogs-looking-for-homes/"
    SKIP_URL_TOKENS = [
        "/category/", "/spotlight-on-", "/transporting-", "/volunteer",
        "/sponsor", "/donate", "/foster", "/about", "/contact",
        "/adopting-a-", "/rescue-back-up", "/adoption-donation",
        "/preparing-our-dogs", "/our-rescue-dogs-looking-for-homes",
        "/search-our-dogs", "rradogs.com", "/podcast", "/events",
        "/leave-a-gift", "/thought-about-", "/your-will", "/fostering-a-",
        "/gift-in-your", "/wp-content/", "/wp-admin/", "/feed",
    ]
    ARTICLE_PREFIXES = (
        "leave-", "thought-", "donating-", "sponsor-", "rra-",
        "spotlight-", "search-", "our-", "adopting-", "adopt-",
        "foster-", "fostering-", "gift-", "privacy-", "accessibility-",
        "volunteers-", "preparing-", "transporting-",
    )

    def _collect_candidates() -> list[str]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"[RRAUK] {e}", file=sys.stderr)
            return []
        if r.status_code != 200:
            print(f"[{"RRAUK"}] HTTP {r.status_code}", file=sys.stderr)
            return []
        soup = BeautifulSoup(r.text, "lxml")
        out: list[str] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("https://rrauk.com/"):
                continue
            if href.rstrip("/") in ("https://rrauk.com", url.rstrip("/")):
                continue
            if any(skip in href for skip in SKIP_URL_TOKENS):
                continue
            slug = href.rstrip("/").rsplit("/", 1)[-1]
            if slug.startswith(ARTICLE_PREFIXES):
                continue
            if href in seen:
                continue
            seen.add(href)
            out.append(href)
        return out

    candidate_urls = _collect_candidates()
    if not candidate_urls:
        # Listing occasionally renders empty — retry once before giving up.
        time.sleep(1)
        candidate_urls = _collect_candidates()
    if not candidate_urls:
        return []

    def probe(dog_url: str) -> Dog | None:
        try:
            rr = requests.get(dog_url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        h1 = ss.find("h1")
        slug = dog_url.rstrip("/").rsplit("/", 1)[-1]
        title = h1.get_text(" ", strip=True) if h1 else slug.replace("-", " ").title()
        # Only trust breed matches in the slug or the article title — the page
        # body contains sidebar/widget content that changes between requests
        # and produces false positives.
        if not matches_breed(slug.replace("-", " ")) and not matches_breed(title):
            return None
        name = re.split(r"[–-]", title, maxsplit=1)[0].strip()
        if len(name) > 30:
            name = name[:30].rstrip()
        return Dog(
            source="RRAUK",
            name=name,
            breed="Collie/Springer match (Romanian rescue)",
            age=None,
            sex=None,
            location="RRAUK (Hampshire / Bordon fosters)",
            url=dog_url,
            in_range=True,
        )

    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, candidate_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# Pawprints to Freedom (UK-wide fosters)
# =====================================================================

def fetch_pawprints() -> list[Dog]:
    url = "https://pawprints2freedom.co.uk/dogs-in-the-uk"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Pawprints] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Pawprints"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    # Listing h2s have no breed info — collect per-dog URLs and probe each.
    candidates: list[tuple[str, str, str, str]] = []  # (title, name, age, sex, href)
    seen: set[str] = set()
    for h2 in soup.find_all("h2"):
        title = h2.get_text(" ", strip=True)
        m = re.match(
            r"^([A-Z][A-Za-z \-']+?)\s*-\s*(\d+)\s*(year|month|week)s?\s*old\s+(male|female)",
            title,
            re.I,
        )
        if not m:
            continue
        name = m.group(1).strip()
        age = f"{m.group(2)} {m.group(3)}s"
        sex = m.group(4).capitalize()
        # The h2 is wrapped in an ancestor <a> — find it.
        anc = h2
        href = None
        for _ in range(5):
            if anc.parent:
                anc = anc.parent
            if anc.name == "a" and anc.get("href"):
                href = anc["href"]
                break
        if not href:
            continue
        if not href.startswith("http"):
            href = "https://pawprints2freedom.co.uk" + href
        if href in seen:
            continue
        seen.add(href)
        candidates.append((name, age, sex, href))

    def probe(entry: tuple[str, str, str, str]) -> Dog | None:
        name, age, sex, dog_url = entry
        try:
            rr = requests.get(dog_url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        text = (ss.find("main") or ss.body or ss).get_text(" ", strip=True)
        if not matches_breed(text):
            return None
        breed_m = re.search(
            r"(border\s*collie[^.,\n]{0,30}|collie(?:\s*cross)?[^.,\n]{0,30}|"
            r"springer\s*spaniel[^.,\n]{0,30}|springer[^.,\n]{0,30}|sprollie[^.,\n]{0,30})",
            text, re.I,
        )
        breed = breed_m.group(1).strip() if breed_m else "Collie/Springer match"
        in_range = True
        verdict = _classify_county(text)
        if verdict is False:
            in_range = False
        return Dog(
            source="Pawprints to Freedom",
            name=name,
            breed=breed[:60],
            age=age,
            sex=sex,
            location="Pawprints to Freedom (UK-wide fosters)",
            url=dog_url,
            in_range=in_range,
        )

    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, candidates):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# Epsom Canine Rescue (Epsom, Surrey ~50mi)
# =====================================================================

def fetch_epsom_canine() -> list[Dog]:
    url = "https://www.epsomcaninerescue.org.uk/dogs-for-homing/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Epsom Canine] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Epsom Canine"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    for t in soup(["nav", "footer", "aside", "script", "style", "header"]):
        t.decompose()
    main = soup.find("main") or soup.body
    if not main:
        return []
    dogs: list[Dog] = []
    # Each dog is h1 heading with name; structural labels ("Dogs for Homing",
    # "Meet the dogs in our care") are h1 too but we filter by downstream content.
    h1s = main.find_all("h1")
    seen: set[str] = set()
    for h1 in h1s:
        name = h1.get_text(strip=True)
        if not name or len(name) > 30:
            continue
        if name.lower() in {"dogs for homing", "meet the dogs in our care",
                            "dogs in our care"}:
            continue
        if name in seen:
            continue
        seen.add(name)
        # Sibling walk doesn't work on this layout (each H1 is in its own div).
        # Walk document order forward until the next H1.
        parts: list[str] = []
        for el in h1.find_all_next():
            if el.name == "h1":
                break
            if el.name in ("p", "span", "li", "td"):
                t = el.get_text(" ", strip=True)
                if t:
                    parts.append(t)
            if len(" ".join(parts)) > 1600:
                break
        body = " ".join(parts)
        combined = f"{name} {body}"
        if not matches_breed(combined):
            continue
        breed_m = re.search(r"Breed[:\s]+([A-Za-z][A-Za-z /x\-]+?)(?:\s+Date|\s+Sex|\s+Colour|\s+Weight|$)", body)
        breed = breed_m.group(1).strip() if breed_m else "Collie/Springer match"
        dob_m = re.search(r"Date of Birth[:\s]+([0-9A-Za-z/ ]+?)(?:\s+Sex|\s+Colour|$)", body)
        age = None
        if dob_m:
            raw = dob_m.group(1).strip()
            # Try "01/07/2014" or "Dec 2025"
            try:
                for fmt in ("%d/%m/%Y", "%b %Y", "%B %Y"):
                    try:
                        d = dt.datetime.strptime(raw, fmt).date()
                        days = (dt.date.today() - d).days
                        if days >= 0:
                            yrs = days // 365
                            mos = (days % 365) // 30
                            age = f"{yrs}y {mos}m" if yrs else f"{mos} months"
                        break
                    except ValueError:
                        continue
            except Exception:
                pass
        sex = "Male" if re.search(r"Sex[:\s]+Male", body) else ("Female" if re.search(r"Sex[:\s]+Female", body) else None)
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower() or "dog"
        dogs.append(Dog(
            source="Epsom Canine",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Epsom Canine (Epsom, Surrey)",
            url=f"{url}#{slug}",
            in_range=True,
        ))
    return dogs


# =====================================================================
# Phoenix Rehoming (Havant, Hants ~3mi)
# =====================================================================

def fetch_phoenix_rehoming() -> list[Dog]:
    list_url = "https://phoenixrehoming.co.uk/adopt/"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Phoenix Rehoming] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Phoenix Rehoming"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dog_urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/animals/" in h and not h.rstrip("/").endswith("animals"):
            if h not in seen:
                seen.add(h)
                dog_urls.append(h)

    def probe(url: str) -> Dog | None:
        try:
            rr = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        if not matches_breed(text):
            return None
        h1 = ss.find("h1")
        name = h1.get_text(strip=True) if h1 else url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        breed_m = re.search(r"Breed[:\s]+([A-Za-z][A-Za-z /x\-]+?)(?:\s{2,}|Age|Sex|Size|Colour|$)", text, re.I)
        breed = breed_m.group(1).strip() if breed_m else "Collie/Springer match"
        age_m = re.search(r"(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?)", text)
        age = age_m.group(1) if age_m else None
        sex = "Male" if re.search(r"\bMale\b", text) else ("Female" if re.search(r"\bFemale\b", text) else None)
        reserved = bool(re.search(r"reserved", text, re.I))
        return Dog(
            source="Phoenix Rehoming",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Phoenix Rehoming (Havant, Hampshire)",
            url=url,
            in_range=True,
            reserved=reserved,
        )
    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# Fareham & Gosport Ocella Stray Dogs Register (~3-5mi)
# =====================================================================

def fetch_fareham_gosport_ocella() -> list[Dog]:
    url = ("https://www.fareham.gov.uk/internetlookups/search.aspx"
           "?list=OcellaStrayDogsRegister&txtSearchAcase_closed=No")
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Fareham/Gosport Ocella] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Fareham/Gosport Ocella"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    # Find result table — contains 11 columns including Breed
    for table in soup.find_all("table"):
        headers_row = table.find("tr")
        if not headers_row:
            continue
        headers_txt = [th.get_text(" ", strip=True).lower() for th in headers_row.find_all(["th", "td"])]
        if "breed" not in " ".join(headers_txt):
            continue
        # Map header index -> field
        col = {name: i for i, name in enumerate(headers_txt)}
        for row in table.find_all("tr")[1:]:
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < len(headers_txt):
                continue
            def g(key: str) -> str:
                for k, i in col.items():
                    if key in k and i < len(cells):
                        return cells[i]
                return ""
            breed = g("breed")
            if not breed or not matches_breed(breed):
                continue
            authority = g("authority") or "Fareham"
            reference = g("reference") or g("ref")
            sex = g("sex") or None
            colour = g("colour") or g("color")
            size = g("size")
            seizure_loc = g("seizure location") or g("location")
            outcome = (g("outcome") or "").lower()
            if "rehomed" in outcome or "claimed" in outcome or "returned" in outcome:
                continue
            name = reference or f"Stray {authority}"
            bits = [b for b in (colour, size) if b]
            age = " · ".join(bits) if bits else None
            location = f"{authority} Council{(' — ' + seizure_loc) if seizure_loc else ''}"
            dogs.append(Dog(
                source=f"{authority} Ocella",
                name=name,
                breed=breed,
                age=age,
                sex=sex,
                location=location,
                url=url,
                in_range=True,
            ))
        break
    return dogs


# =====================================================================
# RSPCA findapet template helper
# =====================================================================

def _rspca_findapet_probe(listing_url: str, branch_slug: str,
                          branch_display: str, location_prefix: str) -> list[Dog]:
    try:
        r = requests.get(listing_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[{branch_display}] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{branch_display}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dog_urls: list[str] = []
    seen: set[str] = set()
    # Only collect per-dog detail URLs: /local/<branch>/findapet/details/<name>/<id>/rehome
    detail_re = re.compile(
        rf"/local/{re.escape(branch_slug)}/findapet/details/[^/]+/[A-Z0-9_]+/rehome",
        re.I,
    )
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if detail_re.search(h):
            full = h if h.startswith("http") else f"https://www.rspca.org.uk{h}"
            if full not in seen:
                seen.add(full)
                dog_urls.append(full)

    def probe(url: str) -> Dog | None:
        try:
            rr = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        # Extract the formal breed field — prose mentions don't count.
        breed_m = re.search(r"Breed[:\s]+([^A-Z][^\n]{0,80}?)(?:\s+Colour|\s+Age|\s+Sex|\s+Ref|$)", text)
        if not breed_m:
            breed_m = re.search(r"Breed[:\s]+([A-Z][A-Za-z][A-Za-z /x\-\(\)]{2,60})", text)
        if not breed_m:
            return None
        breed = breed_m.group(1).strip()
        if not matches_breed(breed):
            return None
        # Pet name from URL slug: /details/NAME/ID/rehome
        m = re.search(r"/details/([^/]+)/[A-Z0-9_]+/rehome", url, re.I)
        name = m.group(1).replace("_", " ").strip().title() if m else "Dog"
        age_m = re.search(r"Age[:\s]+([^A-Z][^\n]{0,40}?)(?:\s+Sex|\s+Breed|\s+Colour|\s+Ref|$)", text)
        age = age_m.group(1).strip() if age_m else None
        sex = "Male" if re.search(r"Sex[:\s]+Male", text) else ("Female" if re.search(r"Sex[:\s]+Female", text) else None)
        reserved = bool(re.search(r"reserved", text, re.I))
        return Dog(
            source=branch_display,
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location=location_prefix,
            url=url,
            in_range=True,
            reserved=reserved,
        )
    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


def fetch_rspca_solent() -> list[Dog]:
    return _rspca_findapet_probe(
        "https://www.rspca.org.uk/local/findapet/-/rspca/solent-branch",
        "solent-branch",
        "RSPCA Solent",
        "RSPCA Solent (Stubbington Ark, Fareham)",
    )


def fetch_rspca_sussex_west() -> list[Dog]:
    return _rspca_findapet_probe(
        "https://rspcasussexwest.org.uk/rehoming-a-dog/",
        "sussex-west-branch",
        "RSPCA Sussex West",
        "RSPCA Sussex West (Mount Noddy, Chichester)",
    )


def fetch_rspca_isle_of_wight() -> list[Dog]:
    # Independent IoW branch runs its own site, not the national findapet widget.
    list_url = "https://www.rspca-isleofwight.org.uk/animals-for-adoption/"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[RSPCA Isle of Wight] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"RSPCA Isle of Wight"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dog_urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/pet/" in h and not h.rstrip("/").endswith("pet"):
            if h not in seen:
                seen.add(h)
                dog_urls.append(h)

    def probe(url: str) -> Dog | None:
        try:
            rr = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        # Skip cats/rabbits
        if re.search(r"\b(cat|kitten|rabbit|guinea pig)\b", text, re.I) and \
           not re.search(r"\b(dog|puppy)\b", text, re.I):
            return None
        if not matches_breed(text):
            return None
        h1 = ss.find("h1")
        name = h1.get_text(strip=True) if h1 else url.rstrip("/").split("/")[-1].replace("-", " ").title()
        breed_m = re.search(r"Breed[:\s]+([A-Za-z][A-Za-z /x\-]+?)(?:\s{2,}|Age|Sex|Colour|$)", text, re.I)
        breed = breed_m.group(1).strip() if breed_m else "Collie/Springer match"
        age_m = re.search(r"(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?)", text)
        age = age_m.group(1) if age_m else None
        sex = "Male" if re.search(r"\bMale\b", text) else ("Female" if re.search(r"\bFemale\b", text) else None)
        return Dog(
            source="RSPCA Isle of Wight",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="RSPCA Isle of Wight (Godshill, IoW)",
            url=url,
            in_range=True,
        )
    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# St Francis Animal Welfare (Fair Oak, Hants ~20mi)
# =====================================================================

def fetch_st_francis() -> list[Dog]:
    url = "https://www.stfrancisanimalwelfare.co.uk/dogs/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[St Francis] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"St Francis"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    for t in soup(["script", "style", "nav", "footer", "header"]):
        t.decompose()
    main = soup.find("main") or soup.body
    if not main:
        return []
    dogs: list[Dog] = []
    # Each dog section is a heading (h2/h3) followed by labelled fields.
    headings = main.find_all(["h2", "h3"])
    for i, h in enumerate(headings):
        name = h.get_text(strip=True)
        if not name or len(name) > 60:
            continue
        if re.search(r"(dogs in our care|donate|contact|volunteer|sponsor|adopt|policy|about)", name, re.I):
            continue
        # Gather body between this heading and the next
        parts: list[str] = []
        nxt = h.next_sibling
        while nxt and (i + 1 >= len(headings) or nxt is not headings[i + 1]):
            if hasattr(nxt, "get_text"):
                parts.append(nxt.get_text(" ", strip=True))
            elif isinstance(nxt, str):
                parts.append(nxt.strip())
            nxt = getattr(nxt, "next_sibling", None)
            if len(parts) > 60:
                break
        body = " ".join(p for p in parts if p)
        if not matches_breed(body):
            continue
        breed_m = re.search(r"Breed[:\s]+([A-Za-z][A-Za-z /x\-]+?)(?:\s+Age|\s+Sex|\s+Size|\s+Energy|$)", body, re.I)
        breed = breed_m.group(1).strip() if breed_m else "Collie/Springer match"
        age_m = re.search(r"Age[:\s]+([^A-Z][^\n]{0,40}?)(?:\s+Sex|\s+Size|\s+Energy|$)", body)
        age = age_m.group(1).strip() if age_m else None
        sex_m = re.search(r"Sex[:\s]+(Male|Female)", body, re.I)
        sex = sex_m.group(1).capitalize() if sex_m else None
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
        dogs.append(Dog(
            source="St Francis",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="St Francis Animal Welfare (Fair Oak, Hampshire)",
            url=f"{url}#{slug}",
            in_range=True,
        ))
    return dogs


# =====================================================================
# Second Chance Animal Rescue (Southampton ~20mi)
# =====================================================================

def fetch_second_chance() -> list[Dog]:
    list_url = "https://www.secondchanceanimalrescue.co.uk/dogs/"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Second Chance] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Second Chance"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dog_urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        # WordPress date-permalink: /YYYY/MM/DD/name/
        if re.search(r"/\d{4}/\d{2}/\d{2}/[^/]+/?$", h) and "secondchanceanimalrescue" in h:
            if h not in seen:
                seen.add(h)
                dog_urls.append(h)

    def probe(url: str) -> Dog | None:
        try:
            rr = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        if not matches_breed(text):
            return None
        h1 = ss.find("h1")
        name = h1.get_text(strip=True) if h1 else url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        breed_m = re.search(r"Breed[:\s]+([A-Za-z][A-Za-z /x\-]+?)(?:\s{2,}|Age|Sex|$)", text, re.I)
        breed = breed_m.group(1).strip() if breed_m else "Collie/Springer match"
        age_m = re.search(r"(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?)", text)
        age = age_m.group(1) if age_m else None
        sex = "Male" if re.search(r"\bMale\b", text) else ("Female" if re.search(r"\bFemale\b", text) else None)
        reserved = bool(re.search(r"reserved", text, re.I))
        return Dog(
            source="Second Chance Animal Rescue",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Second Chance Animal Rescue (Southampton)",
            url=url,
            in_range=True,
            reserved=reserved,
        )
    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# Clymping Dog Sanctuary (Ford, near Arundel ~20mi)
# =====================================================================

def fetch_clymping() -> list[Dog]:
    url = "https://clympingdogsanctuary.co.uk/dogs-for-adoption/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Clymping] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Clymping"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    for t in soup(["script", "style", "nav", "footer", "header"]):
        t.decompose()
    main = soup.find("main") or soup.body
    if not main:
        return []
    dogs: list[Dog] = []
    # Each dog appears as a heading (h2/h3/h4) + description text below.
    headings = main.find_all(["h2", "h3", "h4"])
    for i, h in enumerate(headings):
        name = h.get_text(strip=True)
        if not name or len(name) > 60:
            continue
        if re.search(r"(adoption|contact|donate|about|dogs for adoption|our dogs)", name, re.I):
            continue
        parts: list[str] = []
        nxt = h.find_next_sibling()
        while nxt and nxt not in headings[i + 1:i + 2]:
            parts.append(nxt.get_text(" ", strip=True) if hasattr(nxt, "get_text") else "")
            nxt = nxt.find_next_sibling() if hasattr(nxt, "find_next_sibling") else None
            if len(parts) > 20:
                break
        body = " ".join(p for p in parts if p)
        if not matches_breed(body):
            continue
        breed_m = re.search(r"([A-Z][A-Za-z][A-Za-z /x\-]{2,40}?(?:Collie|Spaniel|Sprollie)[A-Za-z /x\-]*)", body)
        breed = breed_m.group(1).strip() if breed_m else "Collie/Springer match"
        age_m = re.search(r"(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?)\s*(?:old)?", body, re.I)
        age = age_m.group(1) if age_m else None
        sex = "Male" if re.search(r"\bMale\b|\bboy\b|\bhim\b|\bhe is\b", body, re.I) else \
              ("Female" if re.search(r"\bFemale\b|\bgirl\b|\bher\b|\bshe is\b", body, re.I) else None)
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
        dogs.append(Dog(
            source="Clymping",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Clymping Dog Sanctuary (Ford, West Sussex)",
            url=f"{url}#{slug}",
            in_range=True,
        ))
    return dogs


# =====================================================================
# Wadars (Worthing, W. Sussex ~26mi)
# =====================================================================

def fetch_wadars() -> list[Dog]:
    list_url = "https://wadars.co.uk/dogs/"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Wadars] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Wadars"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/wadars_dogs/" not in h:
            continue
        if h.rstrip("/").endswith("wadars_dogs"):
            continue
        if h in seen:
            continue
        seen.add(h)
        # Walk up for a container with card text
        container = a
        for _ in range(6):
            if container.parent:
                container = container.parent
            txt = container.get_text(" ", strip=True)
            if 20 < len(txt) < 800:
                break
        txt = container.get_text(" ", strip=True)
        if not matches_breed(txt):
            continue
        name = a.get_text(strip=True) or h.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        breed_m = re.search(r"([A-Z][A-Za-z][A-Za-z /x\-]{2,40}?(?:Collie|Spaniel|Sprollie)[A-Za-z /x\-]*)", txt)
        breed = breed_m.group(1).strip() if breed_m else "Collie/Springer match"
        age_m = re.search(r"(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?)", txt)
        age = age_m.group(1) if age_m else None
        sex = "Male" if re.search(r"\bMale\b", txt) else ("Female" if re.search(r"\bFemale\b", txt) else None)
        reserved = bool(re.search(r"reserved", txt, re.I))
        dogs.append(Dog(
            source="Wadars",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Wadars (Worthing, West Sussex)",
            url=h,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


# =====================================================================
# Arundawn Dog Rescue (Horsham, W. Sussex ~30mi)
# =====================================================================

def fetch_arundawn() -> list[Dog]:
    list_url = "https://www.arundawndogrescue.co.uk/available-dogs"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Arundawn] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Arundawn"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    # Collect candidate per-dog URLs — Wix slugs like /tasha.
    SKIP = {
        "", "available-dogs", "about", "about-us", "contact", "contact-us",
        "donate", "adopt", "adoption", "fostering", "foster", "blog",
        "home", "shop", "news", "rainbow-bridge", "success-stories",
        "volunteer", "faq", "faqs", "privacy-policy", "terms",
    }
    dog_urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if not h.startswith("/") and "arundawndogrescue.co.uk/" not in h:
            continue
        path = h.split("arundawndogrescue.co.uk")[-1] if "arundawndogrescue" in h else h
        path = path.strip("/").split("?")[0].split("#")[0]
        if "/" in path:
            continue
        if path.lower() in SKIP:
            continue
        if len(path) < 2 or len(path) > 40:
            continue
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9\-]+$", path):
            continue
        full = f"https://www.arundawndogrescue.co.uk/{path}"
        if full in seen:
            continue
        seen.add(full)
        dog_urls.append(full)

    def probe(url: str) -> Dog | None:
        try:
            rr = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        if not matches_breed(text):
            return None
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        # Prefer the URL slug for the dog name (Wix sites put prose in headings).
        name = slug.replace("-", " ").title()
        h1 = ss.find(["h1", "h2"])
        if h1:
            raw = re.split(r"\s*[-–—|]\s*", h1.get_text(strip=True))[0].strip()
            # Only trust H1 if it's short and looks like a name.
            if 2 <= len(raw) <= 25 and len(raw.split()) <= 3 and not re.search(
                r"(home|story|volunteer|adopt|success|blog|donate|foster)", raw, re.I
            ):
                name = raw
        breed_m = re.search(r"([A-Z][A-Za-z][A-Za-z /x\-]{2,40}?(?:Collie|Spaniel|Sprollie)[A-Za-z /x\-]*)", text)
        breed = breed_m.group(1).strip() if breed_m else "Collie/Springer match"
        age_m = re.search(r"(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?)", text)
        age = age_m.group(1) if age_m else None
        sex = "Male" if re.search(r"\bMale\b", text) else ("Female" if re.search(r"\bFemale\b", text) else None)
        return Dog(
            source="Arundawn",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Arundawn Dog Rescue (Horsham, West Sussex)",
            url=url,
            in_range=True,
        )
    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# Pro Dogs Direct (London/SE fosters ~50mi)
# =====================================================================

def fetch_pro_dogs_direct() -> list[Dog]:
    list_url = "https://prodogsdirect.org.uk/dogs-for-adoption/"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Pro Dogs Direct] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Pro Dogs Direct"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    SKIP = {
        "dogs-for-adoption", "reserved", "adopted", "sponsor-a-dog", "about", "donate",
        "before-you-start", "e-newsletter", "gift-aid", "foster", "foster2adopt",
        "rehome", "volunteer", "privacy-policy", "contact",
    }
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "prodogsdirect.org.uk" not in h:
            continue
        slug = h.rstrip("/").split("/")[-1]
        if slug in SKIP or not slug or slug.startswith("page"):
            continue
        # Per-dog: /{name}-{breed-tokens}/
        if not re.match(r"^[a-z][a-z0-9\-]{2,}$", slug):
            continue
        if h in seen:
            continue
        seen.add(h)
        # Inspect the card containing this link
        container = a
        for _ in range(5):
            if container.parent:
                container = container.parent
            txt = container.get_text(" ", strip=True)
            if 30 < len(txt) < 500:
                break
        txt = container.get_text(" ", strip=True)
        # Skip already-rehomed/adopted dogs and pages where text is too short or generic
        if re.search(r"\b(rehomed|adopted)\b", txt, re.I):
            continue
        if not matches_breed(txt):
            continue
        name = slug.split("-")[0].title()
        breed = extract_breed(txt)
        age_m = re.search(r"(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?)", txt)
        age = age_m.group(1) if age_m else None
        loc_m = re.search(r"(Basingstoke|Bracknell|Guildford|Aldershot|Farnham|Reading|Surrey|Berkshire|Hampshire|Kent|London)", txt)
        place = loc_m.group(1) if loc_m else "London/SE foster"
        location = f"Pro Dogs Direct ({place})"
        dogs.append(Dog(
            source="Pro Dogs Direct",
            name=name,
            breed=breed,
            age=age,
            sex=None,
            location=location,
            url=h,
            in_range=True,
        ))
    return dogs


# =====================================================================
# FurBuddies (Hordle, New Forest ~40mi)
# =====================================================================

def fetch_furbuddies() -> list[Dog]:
    list_url = "https://furbuddies.org/"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[FurBuddies] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"FurBuddies"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    SKIP = {
        "", "about", "about-us", "contact", "contact-us", "donate", "adopt", "sponsor",
        "gallery", "volunteer", "home", "news", "shop", "fundraising", "fostering",
        "success-stories", "privacy-policy", "terms", "faq", "faqs", "store",
    }
    for a in soup.find_all("a", href=True):
        h = a["href"]
        # Either relative /name or absolute furbuddies.org/name
        if h.startswith("http") and "furbuddies.org" not in h:
            continue
        path = h.split("furbuddies.org")[-1] if "furbuddies.org" in h else h
        path = path.strip("/").split("?")[0].split("#")[0]
        if not path or "/" in path:
            continue
        if path.lower() in SKIP or not re.match(r"^[a-zA-Z][a-zA-Z0-9\-]+$", path):
            continue
        full = f"https://furbuddies.org/{path}"
        if full in seen:
            continue
        seen.add(full)
        # Card context
        container = a
        for _ in range(5):
            if container.parent:
                container = container.parent
            txt = container.get_text(" ", strip=True)
            if 20 < len(txt) < 500:
                break
        txt = container.get_text(" ", strip=True)
        if not matches_breed(txt):
            continue
        name = path.replace("-", " ").title()
        breed = extract_breed(txt)
        age_m = re.search(r"(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?)", txt)
        age = age_m.group(1) if age_m else None
        sex = "Male" if re.search(r"\bMale\b", txt) else ("Female" if re.search(r"\bFemale\b", txt) else None)
        dogs.append(Dog(
            source="FurBuddies",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="FurBuddies (Hordle, New Forest)",
            url=full,
            in_range=True,
        ))
    return dogs


# =====================================================================
# Dogs N Homes Rescue (Fleet, Hants ~42mi)
# =====================================================================

def fetch_dogs_n_homes() -> list[Dog]:
    list_url = "https://dogsnhomes.org.uk/adopt-a-dog/"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Dogs N Homes] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Dogs N Homes"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dog_urls: list[str] = []
    seen: set[str] = set()
    # Skip "COMING SOON" cards — those dogs aren't yet available, and this
    # rescue's per-dog pages are slow (~7s each), so it's not worth probing them.
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/adopt-a-dog/" not in h:
            continue
        if h.rstrip("/").endswith("adopt-a-dog"):
            continue
        if h in seen:
            continue
        card = a.find_parent(["article", "div", "li", "section"])
        card_text = card.get_text(" ", strip=True) if card else ""
        if "COMING SOON" in card_text.upper():
            continue
        seen.add(h)
        dog_urls.append(h)

    def probe(url: str) -> Dog | None:
        try:
            rr = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        if not matches_breed(text):
            return None
        h1 = ss.find("h1")
        name = h1.get_text(strip=True) if h1 else url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        name = re.split(r"\s*[-–—|]\s*", name)[0].strip()
        breed = extract_breed(text)
        age_m = re.search(r"(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?)", text)
        age = age_m.group(1) if age_m else None
        sex = "Male" if re.search(r"\bMale\b", text) else ("Female" if re.search(r"\bFemale\b", text) else None)
        return Dog(
            source="Dogs N Homes",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Dogs N Homes (Fleet, Hampshire)",
            url=url,
            in_range=True,
        )
    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# Waggy Tails Rescue (Poole, Dorset ~55mi)
# =====================================================================

def fetch_waggy_tails() -> list[Dog]:
    list_url = "https://waggytails.org.uk/dogs/"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Waggy Tails] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Waggy Tails"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/dog/" not in h or h.rstrip("/").endswith("/dog"):
            continue
        if h in seen:
            continue
        seen.add(h)
        # Walk up until we have a container that holds this dog's card but no OTHER dog anchors.
        # Anchors to the same /dog/<slug>/ (image + title) are allowed; only a different slug breaks.
        container = a
        best = None
        for _ in range(7):
            if container.parent is None:
                break
            container = container.parent
            others = [x for x in container.find_all("a", href=True)
                      if x["href"] != a["href"] and "/dog/" in x["href"]
                      and not x["href"].rstrip("/").endswith("/dog")]
            if others:
                break
            best = container
        if best is None:
            continue
        txt = best.get_text(" ", strip=True)
        if not matches_breed(txt):
            continue
        name_raw = h.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        name = re.sub(r"\s*\d+\s*$", "", name_raw).strip()
        breed = extract_breed(txt)
        age_m = re.search(r"(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?)", txt)
        age = age_m.group(1) if age_m else None
        sex = "Male" if re.search(r"\bMale\b", txt) else ("Female" if re.search(r"\bFemale\b", txt) else None)
        reserved = bool(re.search(r"reserved", txt, re.I))
        dogs.append(Dog(
            source="Waggy Tails",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Waggy Tails (Poole, Dorset)",
            url=h,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


# =====================================================================
# Chilterns Dog Rescue Society (Chalfont St Peter, Bucks ~65mi)
# =====================================================================

def fetch_chilterns() -> list[Dog]:
    list_url = "https://chilternsdogrescue.org.uk/adopt-a-dog/"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Chilterns] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Chilterns"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dog_urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/dog/" not in h or h.rstrip("/").endswith("/dog"):
            continue
        if h in seen:
            continue
        seen.add(h)
        dog_urls.append(h)

    def probe(url: str) -> Dog | None:
        try:
            rr = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        if not matches_breed(text):
            return None
        h1 = ss.find("h1")
        name = h1.get_text(strip=True) if h1 else url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        name = re.split(r"\s*[-–—|]\s*", name)[0].strip()
        breed = extract_breed(text)
        age_m = re.search(r"(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?(?:\s*Old)?)", text, re.I)
        age = age_m.group(1) if age_m else None
        sex = "Male" if re.search(r"\bMale\b", text) else ("Female" if re.search(r"\bFemale\b", text) else None)
        reserved = bool(re.search(r"\breserved\b", text, re.I))
        return Dog(
            source="Chilterns Dog Rescue",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Chilterns Dog Rescue (Chalfont St Peter, Bucks)",
            url=url,
            in_range=True,
            reserved=reserved,
        )
    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# Heathlands Animal Sanctuary (Royston, Herts ~95mi)
# =====================================================================

def fetch_heathlands() -> list[Dog]:
    list_url = "https://heathlands.org.uk/dogs.php"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Heathlands] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Heathlands"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if not h.startswith("dog.php?id="):
            continue
        if h in seen:
            continue
        seen.add(h)
        label = a.get_text(" ", strip=True)
        # label format: "Shadow- 3 years- Border Collie" or "Shadow - 3 years - Border Collie"
        parts = [p.strip() for p in re.split(r"\s*-\s*", label, maxsplit=2)]
        if len(parts) < 3:
            continue
        name, age, breed_raw = parts[0], parts[1], parts[2]
        if not matches_breed(breed_raw):
            continue
        full_url = f"https://heathlands.org.uk/{h}"
        dogs.append(Dog(
            source="Heathlands Animal Sanctuary",
            name=name,
            breed=breed_raw,
            age=age,
            sex=None,
            location="Heathlands Animal Sanctuary (Royston, Herts)",
            url=full_url,
            in_range=True,
        ))
    return dogs


# =====================================================================
# Mutts in Distress (Herts/Essex ~85mi)
# =====================================================================

def fetch_mutts_in_distress() -> list[Dog]:
    url = "https://mutts-in-distress.org.uk/mutts-dogs/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Mutts in Distress] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Mutts in Distress"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    for t in soup(["script", "style", "nav", "footer", "header"]):
        t.decompose()
    main = soup.find("main") or soup.body
    if not main:
        return []
    dogs: list[Dog] = []
    SKIP_HEAD = re.compile(r"(primary|sidebar|footer|latest|search|recent|archive|category|tag)", re.I)
    for raw, body in iter_h2_bodies(main, SKIP_HEAD):
        name = re.split(r"[!?]| Help |\(|\-", raw)[0].strip()
        if not name or len(name) > 40:
            continue
        if not matches_breed(body):
            continue
        breed = extract_breed(body)
        age_m = re.search(r"(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?)", body)
        age = age_m.group(1) if age_m else None
        first = body[:300]
        sex = "Female" if re.search(r"\bshe\b|\bher\b", first, re.I) else ("Male" if re.search(r"\bhe\b|\bhim\b|\bhis\b", first, re.I) else None)
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
        dogs.append(Dog(
            source="Mutts in Distress",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Mutts in Distress (Herts/Essex)",
            url=f"{url}#{slug}",
            in_range=True,
        ))
    return dogs


# =====================================================================
# Help 4 Hounds (Highbridge, Somerset ~100mi edge)
# =====================================================================

def fetch_help4hounds() -> list[Dog]:
    url = "https://help4hounds.org/services/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Help 4 Hounds] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Help 4 Hounds"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    for t in soup(["script", "style", "nav", "footer", "header"]):
        t.decompose()
    main = soup.find("main") or soup.body
    if not main:
        return []
    dogs: list[Dog] = []
    SKIP_HEAD = re.compile(r"(dogs needing|browse dogs|below are|primary|sidebar|footer)", re.I)
    for name, body in iter_h2_bodies(main, SKIP_HEAD):
        if len(name) > 30:
            continue
        if not matches_breed(body):
            continue
        breed = extract_breed(body)
        age_m = re.search(r"(\d+\s*(?:years?|months?))", body)
        age = age_m.group(1) if age_m else None
        first = body[:300]
        sex = "Female" if re.search(r"\bspeyed\b|\bshe\b", first, re.I) else ("Male" if re.search(r"\bneutered\b|\bhe\b", first, re.I) else None)
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
        dogs.append(Dog(
            source="Help 4 Hounds",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Help 4 Hounds (Highbridge, Somerset)",
            url=f"{url}#{slug}",
            in_range=True,
        ))
    return dogs


# =====================================================================
# Oxfordshire Animal Sanctuary (Stadhampton, Oxon ~75mi)
# =====================================================================

def fetch_oxfordshire_as() -> list[Dog]:
    list_url = "https://oxfordshireanimalsanctuary.org.uk/animal-type/dog/"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Oxfordshire AS] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Oxfordshire AS"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dog_urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/animals/" not in h:
            continue
        if h.rstrip("/").endswith("/animals"):
            continue
        if h in seen:
            continue
        seen.add(h)
        dog_urls.append(h)

    def probe(url: str) -> Dog | None:
        try:
            rr = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        breed_m = re.search(r"Breed:\s*([A-Za-z][A-Za-z /x\-]+?)(?:\s+Age|\s+Can|\s+Gender|\s+Status|\s+Minimum|$)", text, re.I)
        if not breed_m:
            return None
        breed = breed_m.group(1).strip()
        if not matches_breed(breed):
            return None
        h1 = ss.find("h1")
        name = h1.get_text(strip=True) if h1 else url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        age_m = re.search(r"Age:\s*(\d+[^A-Z]*?)(?:\s+Can|\s+Gender|\s+Minimum|$)", text)
        age = age_m.group(1).strip() if age_m else None
        sex_m = re.search(r"Gender:\s*(Male|Female)", text, re.I)
        sex = sex_m.group(1).title() if sex_m else None
        return Dog(
            source="Oxfordshire Animal Sanctuary",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Oxfordshire Animal Sanctuary (Stadhampton, Oxon)",
            url=url,
            in_range=True,
        )
    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# Woodgreen Pets Charity (Cambs ~95mi)
# =====================================================================

def fetch_woodgreen() -> list[Dog]:
    list_url = "https://woodgreen.org.uk/pets/?species=dog"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Woodgreen] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Woodgreen"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dog_urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/pets/" not in h or h.rstrip("/").endswith("/pets"):
            continue
        if "?" in h or "#" in h:
            continue
        slug = h.rstrip("/").rsplit("/", 1)[-1]
        if not slug.endswith("-dog"):
            continue
        if h in seen:
            continue
        seen.add(h)
        dog_urls.append(h)

    def probe(url: str) -> Dog | None:
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        # Slug encodes breed: "bodie-lurcher-dog", "robin-french-bulldog-x-chinese-crested-dog"
        slug_breed = slug.replace("-dog", "").replace("-", " ")
        try:
            rr = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        combined = slug_breed + " " + text
        if not matches_breed(combined):
            return None
        h1 = ss.find("h1")
        name = h1.get_text(strip=True) if h1 else slug.split("-")[0].title()
        name = re.split(r"\s*[-–—|]\s*", name)[0].strip()
        breed = extract_breed(combined)
        age_m = re.search(r"(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?)", text)
        age = age_m.group(1) if age_m else None
        sex = "Male" if re.search(r"\bMale\b", text) else ("Female" if re.search(r"\bFemale\b", text) else None)
        return Dog(
            source="Woodgreen Pets Charity",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Woodgreen Pets Charity (Godmanchester, Cambs)",
            url=url,
            in_range=True,
        )
    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# Teckels Animal Sanctuary (Whitminster, Glos ~95mi)
# =====================================================================

_TECKELS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.google.com/",
}

def fetch_teckels() -> list[Dog]:
    url = "https://teckelsanimalsanctuaries.co.uk/dogs-for-adoption/"
    try:
        r = requests.get(url, headers=_TECKELS_HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Teckels] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Teckels"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dog_urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "teckelsanimalsanctuaries.co.uk/animals/" not in h:
            continue
        h = h.split("#", 1)[0].rstrip("/") + "/"
        if h in seen:
            continue
        seen.add(h)
        dog_urls.append(h)

    def probe(durl: str) -> Dog | None:
        try:
            rd = requests.get(durl, headers=_TECKELS_HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rd.status_code != 200:
            return None
        ds = BeautifulSoup(rd.text, "lxml")
        for t in ds(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ds.find("main") or ds.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        if not matches_breed(text):
            return None
        h1 = ds.find("h1")
        slug = durl.rstrip("/").rsplit("/", 1)[-1]
        name = h1.get_text(" ", strip=True) if h1 else slug.replace("-", " ").title()
        if len(name) > 40:
            name = slug.replace("-", " ").title()
        breed = extract_breed(text)
        age_m = re.search(r"(\d+\s*(?:years?|months?))", text, re.I)
        age = age_m.group(1) if age_m else None
        first = text[:400]
        sex = "Female" if re.search(r"\bshe\b|\bher\b", first, re.I) else ("Male" if re.search(r"\bhe\b|\bhim\b|\bhis\b", first, re.I) else None)
        return Dog(
            source="Teckels Animal Sanctuary",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Teckels Animal Sanctuary (Whitminster, Glos)",
            url=durl,
            in_range=True,
        )

    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=4) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# Pets4Homes Adoption (multi-rescue aggregator)
# =====================================================================

def fetch_pets4homes_adoption() -> list[Dog]:
    base = "https://www.pets4homes.co.uk"
    breed_urls = [
        (f"{base}/adoption/dogs/border-collie/", "Border Collie"),
        (f"{base}/adoption/dogs/english-springer-spaniel/", "English Springer Spaniel"),
        (f"{base}/adoption/dogs/welsh-springer-spaniel/", "Welsh Springer Spaniel"),
    ]
    dog_urls: list[tuple[str, str]] = []
    seen: set[str] = set()
    for list_url, default_breed in breed_urls:
        try:
            r = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"[Pets4Homes {default_breed}] {e}", file=sys.stderr)
            continue
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            h = a["href"]
            if not h.startswith("/classifieds/"):
                continue
            full_url = base + h
            if full_url in seen:
                continue
            seen.add(full_url)
            dog_urls.append((full_url, default_breed))

    def probe(item: tuple[str, str]) -> Dog | None:
        url, default_breed = item
        try:
            rr = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        # Reject non-rescue ads (private sellers aren't in /adoption/ but sanity-check)
        if "Rescue" not in text and "Charity" not in text:
            return None
        if not matches_breed(text):
            return None
        h1 = ss.find("h1")
        name_raw = h1.get_text(" ", strip=True) if h1 else url.rstrip("/").rsplit("/", 1)[-1]
        # "Molly 3yr old Border Collie" -> "Molly"
        name = re.split(r"\s+\d", name_raw, maxsplit=1)[0].strip()
        if not name:
            name = name_raw[:40]
        breed_m = re.search(r"Breed\s+([A-Za-z][A-Za-z /x\-]+?)\s+(?:Age|Ready|Sex|Health|Vaccin|Microchip|\d)", text)
        breed = breed_m.group(1).strip() if breed_m else default_breed
        age_m = re.search(r"Age\s+(\d+\s*(?:years?|months?))", text, re.I)
        age = age_m.group(1) if age_m else None
        loc_m = re.search(r"Location\s+([A-Z][A-Za-z' \-]+?),\s*([A-Z][A-Za-z' \-]+?)(?:\s+Breed|\s+£|\s+Ready|$)", text)
        location = f"{loc_m.group(1).strip()}, {loc_m.group(2).strip()}" if loc_m else None
        if not location:
            return None
        return Dog(
            source="Pets4Homes (Rescue)",
            name=name,
            breed=breed,
            age=age,
            sex=None,
            location=location,
            url=url,
            in_range=True,
        )
    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# SYESSR — South Yorkshire English Springer Spaniel Rescue (Sheffield)
# Wix site; listing at /items with per-dog pages at /items/<slug>
# Each per-dog page has structured "BREED : ...", "SEX : ...", "D.O.B : ..."
# =====================================================================
def fetch_syessr() -> list[Dog]:
    base = "https://www.syessr.org.uk"
    try:
        r = requests.get(f"{base}/items", headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[SYESSR] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"SYESSR"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    seen: set[str] = set()
    dog_urls: list[str] = []
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/items/" not in h:
            continue
        if h.startswith("/"):
            h = base + h
        if not h.startswith(base + "/items/"):
            continue
        slug = h.rsplit("/", 1)[-1]
        if not slug or slug == "items":
            continue
        if h in seen:
            continue
        seen.add(h)
        dog_urls.append(h)

    def probe(url: str) -> Dog | None:
        try:
            rr = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        if not matches_breed(text):
            return None
        breed_m = re.search(r"BREED\s*:\s*([A-Z][A-Z /\-]+?)(?:\s{2,}|\s+SEX|\s+STATUS|\s+D\.O\.B|\s+AGE|$)", text)
        breed = breed_m.group(1).strip().title() if breed_m else extract_breed(text)
        sex_m = re.search(r"SEX\s*:\s*(MALE|FEMALE)(?:\s*\(([A-Z ]+?)\))?", text)
        if sex_m:
            sex = sex_m.group(1).title()
            if sex_m.group(2):
                sex = f"{sex} ({sex_m.group(2).strip().title()})"
        else:
            sex = None
        dob_m = re.search(r"D\.O\.B\s*:\s*([0-9/A-Za-z ]+?)(?:\s{2,}|\s+BREED|\s+SEX|\s+STATUS|$)", text)
        age = None
        if dob_m:
            raw = dob_m.group(1).strip()
            iso = None
            dm = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
            if dm:
                iso = f"{dm.group(3)}-{int(dm.group(2)):02d}-{int(dm.group(1)):02d}"
            ym = re.match(r"(\d{1,2})/(\d{4})", raw)
            if not iso and ym:
                iso = f"{ym.group(2)}-{int(ym.group(1)):02d}-01"
            if not iso and re.match(r"\d{4}$", raw):
                iso = f"{raw}-01-01"
            age = age_from_dob(iso) if iso else raw
        status_m = re.search(r"STATUS\s*:\s*([A-Z]+)", text)
        reserved = bool(status_m and status_m.group(1).upper() in {"RESERVED", "PENDING"})
        slug = url.rsplit("/", 1)[-1]
        name = slug.replace("%23", "").replace("-", " ").strip().title()
        return Dog(
            source="SYESSR",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="SYESSR (Sheffield, South Yorkshire)",
            url=url,
            in_range=False,
            reserved=reserved,
        )

    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# NESSR — Northern English Springer Spaniel Rescue
# Wix blog layout; listing at /dogs-needing-homes -> /single-post/<slug>
# Per-dog pages expose full bio text (breed usually in slug + body).
# =====================================================================
def fetch_nessr() -> list[Dog]:
    base = "https://www.nessr.org"
    try:
        r = requests.get(f"{base}/dogs-needing-homes", headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[NESSR] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"NESSR"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    seen: set[str] = set()
    dog_urls: list[str] = []
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/single-post/" not in h:
            continue
        if h.startswith("/"):
            h = base + h
        if h in seen:
            continue
        seen.add(h)
        dog_urls.append(h)

    def probe(url: str) -> Dog | None:
        try:
            rr = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        title_tag = ss.find("title")
        title = title_tag.get_text(" ", strip=True) if title_tag else ""
        og_desc_tag = ss.find("meta", property="og:description")
        og_desc = og_desc_tag.get("content", "") if og_desc_tag else ""
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ss.find("main") or ss.body
        body_text = main.get_text(" ", strip=True) if main else ""
        combined = f"{title} {og_desc} {body_text}"
        if not matches_breed(combined):
            return None
        slug = url.rsplit("/", 1)[-1]
        # slug format: "buddy-6-yr-old-sprocker"
        slug_name = slug.split("-", 1)[0]
        name = slug_name.title()
        # Age: pattern "6 yr old" or "8 months old"
        age_m = re.search(r"(\d+(?:\.\d+)?\s*(?:yr|year|months?|mo)s?(?:\s*old)?)", title, re.I)
        age = age_m.group(1).strip() if age_m else None
        # Prefer the slug-trailing breed (authoritative) over body-text mentions.
        slug_breed_m = re.search(r"-(?:yr|year|months?|mo)s?-old-(.+)$", slug)
        if not slug_breed_m:
            slug_breed_m = re.search(r"-\d+-(?:yr|year|months?|mo)s?-(.+)$", slug)
        if slug_breed_m:
            breed = slug_breed_m.group(1).replace("-", " ").strip().title()
            breed = breed.replace("Wcs", "Working Cocker Spaniel")
        else:
            breed = extract_breed(title or og_desc)
        # Location: "fostered in <county>" or "based in <place>"
        loc_m = re.search(r"(?:fostered|based|being fostered)\s+(?:in|near)\s+([A-Z][A-Za-z \-]+?)(?:\s+and|\.|,|\s+you)", body_text)
        foster_loc = loc_m.group(1).strip() if loc_m else None
        location = f"NESSR ({foster_loc})" if foster_loc else "NESSR (North of England)"
        return Dog(
            source="NESSR",
            name=name,
            breed=breed,
            age=age,
            sex=None,
            location=location,
            url=url,
            in_range=False,
        )

    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# AgilityNet — private-owner rehoming listings for agility-suited dogs
# Single page; table rows inside #tabs-1 each carry one dog with labelled
# fields (Breed, Age, Sex, Where dog can be seen).
# =====================================================================
def fetch_agilitynet() -> list[Dog]:
    url = "https://agilitynet.co.uk/activepages/rescues.asp"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[AgilityNet] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"AgilityNet"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    active = soup.find(id="tabs-1")
    if not active:
        return []
    dogs: list[Dog] = []
    for row in active.find_all("tr"):
        title_div = row.find("div", class_="title")
        if not title_div:
            continue
        name = title_div.get_text(" ", strip=True)
        if not name:
            continue
        row_text = row.get_text(" ", strip=True)
        if not matches_breed(row_text):
            continue

        def label(pattern: str) -> str | None:
            m = re.search(r"\b" + pattern + r"\s*:\s*([^\n]+?)(?=\s{2,}|\s+(?:History|Description|Rehoming|Contact|Email|Tel|Date Added|Child friendly|Cat friendly|Age|Sex|Neutered|Vaccinated|Micro|Flea|Cost|Where)\s*:|$)", row_text)
            return m.group(1).strip() if m else None

        breed = label(r"Breed") or extract_breed(row_text)
        age = label(r"Age")
        sex_raw = label(r"Sex")
        sex = sex_raw.title() if sex_raw else None
        loc = label(r"Where dog can be seen")
        if loc:
            loc = re.sub(r"\(map\)\s*$", "", loc).strip()
        dogs.append(Dog(
            source="AgilityNet",
            name=name.title(),
            breed=breed,
            age=age,
            sex=sex,
            location=loc or "AgilityNet (UK)",
            url=url + f"#{name.lower().replace(' ', '-')}",
            in_range=False,
        ))
    return dogs


# =====================================================================
# Round 3: UK-wide breed specialists + reclaimed rescues
# =====================================================================

_R3_BREED_PATTERNS = [
    re.compile(r"border\s*collie", re.I),
    re.compile(r"\bbearded\s*collie\b", re.I),
    re.compile(r"\bbeardie\b", re.I),
    re.compile(r"\bcollie\b", re.I),
    re.compile(r"springer\s*spaniel", re.I),
    re.compile(r"\bspringer\b", re.I),
    re.compile(r"\bsprollie\b", re.I),
    re.compile(r"cocker\s*spaniel", re.I),
    re.compile(r"\bcocker\b", re.I),
    re.compile(r"\bsprocker\b", re.I),
    re.compile(r"\bspaniel\b", re.I),
]


def _r3_matches_breed(text: str) -> bool:
    return any(p.search(text or "") for p in _R3_BREED_PATTERNS)


_R3_RESERVED = re.compile(
    r"\b(reserved|adopted|rehomed|re\-?homed|on\s*hold|homed|home\s*found|"
    r"no\s*longer\s*available)\b",
    re.I,
)


def fetch_uk_spaniel_rescue() -> list[Dog]:
    urls = [
        "https://ukspanielrescue.co.uk/our-dogs-2/",
        "https://ukspanielrescue.co.uk/our-dogs/",
        "https://ukspanielrescue.co.uk/adoption/",
    ]
    r = None
    used_url = ""
    for u in urls:
        try:
            rr = requests.get(u, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"[UK Spaniel Rescue] {u}: {e}", file=sys.stderr)
            continue
        if rr.status_code == 200 and len(rr.text) > 2000:
            r = rr
            used_url = u
            break
    if r is None:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    content = soup.find("div", class_=re.compile(r"entry-content")) or soup
    dogs: list[Dog] = []
    seen: set[str] = set()
    for p in content.find_all("p"):
        body = p.get_text(" ", strip=True)
        if not body or "Status:" not in body or "Breed:" not in body:
            continue
        if not _r3_matches_breed(body):
            continue

        def field(label: str) -> str | None:
            m = re.search(
                rf"\b{label}\s*:\s*(.+?)(?=\s+(?:Status|Breed|Age|Sex|Location|"
                rf"Foster\s+or\s+adopt|Vaccinated|Neutered|Tail|OK\s+with|"
                rf"Must\s+be|Housetrained|Crate\s+trained|Health\s+issues|"
                rf"Needs\s+an|Adoption\s+fee|Has\s+separation)\s*:|$)",
                body, re.I)
            return m.group(1).strip() if m else None

        breed = field("Breed") or "Spaniel"
        age = field("Age")
        sex = field("Sex")
        loc = field("Location") or ""
        status = field("Status") or ""
        name = None
        prev = p
        for _ in range(30):
            prev = prev.find_previous(["p", "h1", "h2", "h3"])
            if prev is None:
                break
            t = prev.get_text(" ", strip=True)
            if not t or len(t) > 40:
                continue
            low = t.lower()
            if any(s in low for s in (
                    "adopt", "our dogs", "spaniel rescue", "status", "breed",
                    "life is a series", "change a life", "meet")):
                continue
            if re.fullmatch(r"[A-Z][A-Za-z\-' ]{1,30}", t):
                name = t
                break
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        reserved = bool(_R3_RESERVED.search(status))
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower() or "dog"
        dogs.append(Dog(
            source="UK Spaniel Rescue",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location=(f"UK Spaniel Rescue ({loc}; UK-wide fosters)"
                      if loc else "UK Spaniel Rescue (UK-wide fosters)"),
            url=f"{used_url}#{slug}",
            in_range=True,
            reserved=reserved,
        ))
    return dogs


def fetch_spaniel_rescue_foundation() -> list[Dog]:
    urls = [
        "https://www.spanielrescuefoundation.org/availabledogs",
        "https://www.spanielrescuefoundation.org/dogs-needing-foster",
    ]
    dogs: list[Dog] = []
    seen_urls: set[str] = set()
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"[Spaniel Rescue Foundation {url}] {e}", file=sys.stderr)
            continue
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        dogs.extend(_srf_parse(soup, seen_urls))
    return dogs


def _srf_parse(soup: "BeautifulSoup", seen_urls: set[str]) -> list[Dog]:
    dogs: list[Dog] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"/srf\d{2,4}\b", href, re.I)
        if not m:
            continue
        if href.startswith("/"):
            href = "https://www.spanielrescuefoundation.org" + href
        if href in seen_urls:
            continue
        tile = a
        for _ in range(8):
            if tile.parent is None:
                break
            tile = tile.parent
            txt = tile.get_text(" ", strip=True)
            if txt and len(txt) < 500 and _r3_matches_breed(txt):
                break
        tile_text = tile.get_text(" | ", strip=True) if tile else ""
        if not _r3_matches_breed(tile_text):
            parent = a.find_parent()
            scope = parent.find_parent() if parent else None
            tile_text = scope.get_text(" | ", strip=True) if scope else tile_text
            if not _r3_matches_breed(tile_text):
                continue
        parts = [p.strip() for p in tile_text.split("|") if p.strip()]
        breed_idx = None
        for i, p in enumerate(parts):
            if _r3_matches_breed(p):
                breed_idx = i
                break
        name = None
        breed = parts[breed_idx] if breed_idx is not None else "Spaniel"
        if breed_idx is not None:
            for j in range(breed_idx - 1, -1, -1):
                cand = parts[j]
                if (1 <= len(cand) <= 25
                        and re.fullmatch(r"[A-Z][A-Za-z\-' ]{1,24}", cand)
                        and cand.lower() not in {"read more", "happy tails", "donate", "about"}):
                    name = cand
                    break
        if not name:
            name = m.group(0).strip("/").upper()
        reserved = bool(_R3_RESERVED.search(tile_text))
        seen_urls.add(href)
        dogs.append(Dog(
            source="Spaniel Rescue Foundation",
            name=name,
            breed=breed,
            age=None,
            sex=None,
            location="Spaniel Rescue Foundation (Middle Rasen, Lincs; UK-wide fosters)",
            url=href,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


def fetch_collie_rescue_rs() -> list[Dog]:
    url = "https://collierescueroughandsmooth.co.uk/uk-or-foreign/collies-in-need-of-adoption-uk/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Collie Rescue R&S] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Collie Rescue R&S"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    cards = soup.find_all("a", class_=re.compile(r"need-adopting-card"))
    if not cards:
        return []
    dogs: list[Dog] = []
    seen: set[str] = set()
    for card in cards:
        href = card.get("href", "")
        if href.startswith("/"):
            href = "https://collierescueroughandsmooth.co.uk" + href
        tile_text = card.get_text(" | ", strip=True)
        parts = [p.strip() for p in tile_text.split("|")
                 if p.strip() and p.strip() != "Find Out More"]
        if not parts:
            continue
        name = parts[0]
        rest = parts[1:]
        breed_line = rest[0] if rest else "Collie"
        age = None
        sex = None
        for seg in rest[1:]:
            low = seg.lower()
            if re.search(r"\d+\s*(year|month|week)", low):
                age = seg
            elif low in {"male", "female", "dog", "bitch"}:
                sex = seg
        scope = f"{name} {breed_line} {' '.join(rest)}"
        if not _r3_matches_breed(scope):
            continue
        reserved = False
        for img in card.find_all("img"):
            src = img.get("src", "") or ""
            if re.search(r"/dog-banners/(rehomed|reserved|homed|adopted)", src, re.I):
                reserved = True
                break
        if _R3_RESERVED.search(tile_text):
            reserved = True
        key = href or name.lower()
        if key in seen:
            continue
        seen.add(key)
        dogs.append(Dog(
            source="Collie Rescue R&S",
            name=name,
            breed=breed_line,
            age=age,
            sex=sex,
            location="Collie Rescue R&S (Sheffield HQ; UK-wide home-check/fosters)",
            url=href or url,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


def fetch_save_our_spaniels() -> list[Dog]:
    url = "https://www.saveourspaniels.org.uk/dogs-needing-homes"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Save Our Spaniels] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Save Our Spaniels"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    for h2 in soup.find_all("h2"):
        raw = re.sub(r"\s+", " ", h2.get_text(" ", strip=True)).strip()
        if not raw:
            continue
        if "LONG TERM FOSTER HOME" in raw.upper() or "DOGS NEEDING" in raw.upper():
            if re.match(r"^[A-Z][A-Z\s/]+$", raw) and " - " not in raw:
                continue
        m = re.match(r"^([A-Za-z][A-Za-z0-9 /\-]{0,40}?)\s*-\s*([0-9A-Za-z ]{1,20}?)\b", raw)
        if not m:
            continue
        name = m.group(1).strip()
        age = m.group(2).strip()
        body_parts: list[str] = []
        sib = h2
        for _ in range(6):
            sib = sib.find_next(["p", "h2", "h3"])
            if sib is None or sib.name == "h2":
                break
            body_parts.append(sib.get_text(" ", strip=True))
        body = " ".join(body_parts)
        # Strip parenthetical size/comparison phrases so a bare "(springer
        # spaniel size)" mention doesn't falsely match a Breton as a springer.
        body_clean = re.sub(
            r"\([^)]*\b(?:size|sized|similar|lookalike|looking)\b[^)]*\)",
            "", body, flags=re.I,
        )
        body_clean = re.sub(
            r"\b(?:like|similar to|size of|thinks (?:he|she)(?:'s| is) (?:a|an))\s+"
            r"(?:a\s+)?(?:border\s*collie|springer\s*spaniel|cocker\s*spaniel|collie|springer|cocker)\b",
            "", body_clean, flags=re.I,
        )
        scope = f"{name} {body_clean}"
        if not _r3_matches_breed(scope):
            continue
        reserved = "RESERVED" in raw.upper() or "ADOPTED" in raw.upper()
        loc = None
        m2 = re.search(
            r"in foster (?:in|near)\s+([A-Z][A-Za-z\-\' ]+?(?:,\s*[A-Z][A-Za-z\-\' ]+)?)[\.\,]",
            body)
        if m2:
            loc = m2.group(1).strip()
        dogs.append(Dog(
            source="Save Our Spaniels",
            name=name.title(),
            breed="Breton / Spaniel mix",
            age=age,
            sex=None,
            location=(f"Save Our Spaniels ({loc}; UK-wide fosters)"
                      if loc else "Save Our Spaniels (UK-wide fosters)"),
            url=url,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


def fetch_spaniel_assist() -> list[Dog]:
    url = "https://www.spanielassist.com/adopt"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Spaniel Assist] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Spaniel Assist"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    noise = {
        "spaniels ready for adoption", "adoptions",
        "join our main facebook page", "spaniel assist rescue & re-homing",
    }
    for h in soup.find_all(["h2", "h3", "h4"]):
        raw = re.sub(r"\s+", " ", h.get_text(" ", strip=True)).strip()
        if not raw or len(raw) > 30:
            continue
        if raw.lower() in noise:
            continue
        if not re.match(r"^[A-Z][A-Za-z\-\' ]{1,20}$", raw):
            continue
        if raw in seen:
            continue
        seen.add(raw)
        # Spaniel Assist only publishes individual pages for a minority of dogs
        # (e.g. /winnie exists, /arlo 404s). The /adopt page holds a photo and
        # description for every dog, so point there with a per-dog fragment so
        # scan-level URL dedup keeps each one as its own entry.
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower() or "dog"
        dogs.append(Dog(
            source="Spaniel Assist",
            name=raw,
            breed="Cocker Spaniel / Working Cocker",
            age=None,
            sex=None,
            location="Spaniel Assist (UK-wide fosters)",
            url=f"{url}#{slug}",
            in_range=True,
        ))
    return dogs


def fetch_helping_dogs_cats_uk() -> list[Dog]:
    url = "https://helpingdogsandcatsuk.org/dogs"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Helping Dogs and Cats UK] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Helping Dogs and Cats UK"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    noise = {
        "meet our dogs", "dogs for adoption", "adopting – what to expect",
        "rehoming criteria", "how to apply", "meeting your new pet",
        "rehoming fees", "what's included?", "other costs to consider",
        "rescue support", "application form", "this website uses cookies.",
        "what’s included?",
    }
    for h in soup.find_all(["h3", "h4"]):
        raw = re.sub(r"\s+", " ", h.get_text(" ", strip=True)).strip()
        if not raw or raw.lower() in noise:
            continue
        if len(raw) > 30:
            continue
        if not re.match(r"^[A-Z][A-Za-z\-\' ]{1,25}$", raw):
            continue
        key = raw.upper()
        if key in seen:
            continue
        seen.add(key)
        scope = raw
        parent = h.find_parent()
        if parent is not None:
            scope = parent.get_text(" ", strip=True)
        if not _r3_matches_breed(scope):
            continue
        dogs.append(Dog(
            source="Helping Dogs and Cats UK",
            name=raw.title(),
            breed="Unknown",
            age=None,
            sex=None,
            location="Helping Dogs and Cats UK (Basingstoke, Hants)",
            url=url,
            in_range=True,
        ))
    return dogs


def fetch_lurcher_sos() -> list[Dog]:
    url = "https://www.lurchersos.org.uk/dogs-for-adoption"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Lurcher SOS] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Lurcher SOS"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    for h in soup.find_all(["h1", "h2", "h3"]):
        raw = re.sub(r"\s+", " ", h.get_text(" ", strip=True)).strip()
        if not raw or raw.lower() in ("meet our dogs", "lurcher sos sighthound rescue"):
            continue
        if len(raw) > 40:
            continue
        if not re.match(r"^[A-Z][A-Za-z\-\']{1,20}(?:\s+\(Reserved\))?$", raw):
            continue
        reserved = "reserved" in raw.lower()
        name = re.sub(r"\s*\(Reserved\).*", "", raw, flags=re.I).strip()
        if name.upper() in seen:
            continue
        seen.add(name.upper())
        scope = raw
        parent = h.find_parent()
        if parent is not None:
            scope = parent.get_text(" ", strip=True)
        if not _r3_matches_breed(scope):
            continue
        dogs.append(Dog(
            source="Lurcher SOS",
            name=name,
            breed="Lurcher / Sighthound",
            age=None,
            sex=None,
            location="Lurcher SOS (UK-wide fosters)",
            url=url,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


def fetch_forever_hounds() -> list[Dog]:
    url = "https://foreverhoundstrust.org/dogs/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Forever Hounds] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Forever Hounds"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    for art in soup.find_all("article"):
        link = art.find("a", href=True)
        if not link:
            continue
        href = link["href"]
        if "/dog/" not in href:
            continue
        h = art.find(["h1", "h2", "h3"])
        name = h.get_text(" ", strip=True).strip() if h else None
        if not name or name.lower() == "a dog for you":
            continue
        if href in seen:
            continue
        seen.add(href)
        text_blob = art.get_text(" ", strip=True)
        if not _r3_matches_breed(f"{name} {text_blob}"):
            continue
        reserved = "reserved" in text_blob.lower()
        age = None
        m = re.search(r"(\d+)\s*(?:years?|yrs?)", text_blob, re.I)
        if m:
            age = f"{m.group(1)} years"
        dogs.append(Dog(
            source="Forever Hounds Trust",
            name=name,
            breed="Greyhound / Lurcher / Sighthound",
            age=age,
            sex=None,
            location="Forever Hounds Trust (UK-wide fosters)",
            url=href,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


def fetch_nwessr() -> list[Dog]:
    url = "https://www.englishspringerrescue.co.uk/category/dogs-needing-homes/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[NWESSR] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"NWESSR"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    for art in soup.find_all("article"):
        link = art.find("a", href=True)
        h = art.find(["h1", "h2", "h3"])
        if not link or not h:
            continue
        name = h.get_text(" ", strip=True).strip()
        if not name or len(name) > 30:
            continue
        href = link["href"]
        if "guestbook" in href or href.endswith("#"):
            continue
        if not href.startswith("http"):
            href = "https://www.englishspringerrescue.co.uk" + href
        if href in seen:
            continue
        seen.add(href)
        text_blob = art.get_text(" ", strip=True)
        reserved = "reserved" in text_blob.lower()
        dogs.append(Dog(
            source="NWESSR",
            name=name,
            breed="English Springer Spaniel",
            age=None,
            sex=None,
            location="NWESSR (North West England)",
            url=href,
            in_range=False,
            reserved=reserved,
        ))
    return dogs


def fetch_animal_rescue_cymru() -> list[Dog]:
    url = "https://www.animalrescuecymru.co.uk/category/adoption/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Animal Rescue Cymru] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Animal Rescue Cymru"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    for art in soup.find_all("article"):
        link = art.find("a", href=True)
        if not link:
            continue
        href = link["href"]
        if "animalrescuecymru" not in href:
            continue
        m = re.search(r"/\d{4}/\d{2}/([a-z0-9\-]+)/?$", href)
        if not m:
            continue
        slug = m.group(1)
        name = slug.replace("-", " ").title()
        if href in seen:
            continue
        seen.add(href)
        text_blob = art.get_text(" ", strip=True)
        if not _r3_matches_breed(f"{name} {text_blob}"):
            continue
        reserved = "reserved" in text_blob.lower()
        breed = "Unknown"
        for b in ["Border Collie", "Bearded Collie", "Collie", "Springer",
                  "Cocker", "Sprocker", "Sprollie"]:
            if re.search(rf"\b{b}", text_blob, re.I):
                breed = b
                break
        age = None
        mage = re.search(r"(\d+)\s*yr", text_blob, re.I)
        if mage:
            age = f"{mage.group(1)} years"
        dogs.append(Dog(
            source="Animal Rescue Cymru",
            name=name,
            breed=breed,
            age=age,
            sex=None,
            location="Animal Rescue Cymru (West Wales)",
            url=href,
            in_range=False,
            reserved=reserved,
        ))
    return dogs


_MT_BASE = "https://www.manytearsrescue.org"
_MT_LIST = _MT_BASE + "/adopt/dogs/"


def _mt_parse_page(html: str) -> list[Dog]:
    s = BeautifulSoup(html, "lxml")
    out: list[Dog] = []
    for a in s.select("a.animal-card[href]"):
        href = a.get("href", "")
        if not re.search(r"/adopt/dogs/\d+/?$", href):
            continue
        h3 = a.select_one("h3")
        name = h3.get_text(strip=True) if h3 else (a.get("title") or "").strip()
        if not name:
            continue
        breed = ""
        age = None
        sex = None
        location = None
        for d in a.select("div.icon"):
            cls = " ".join(d.get("class", []))
            text = d.get_text(" ", strip=True)
            if "breed" in cls:
                breed = text
            elif "age" in cls:
                age = text
            elif "sex" in cls:
                sex = text
            elif "location" in cls:
                location = text
        full_url = _MT_BASE + href if href.startswith("/") else href
        out.append(Dog(
            source="Many Tears",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location=(f"Many Tears ({location}; UK-wide fosters)"
                      if location else "Many Tears (Llanelli; UK-wide fosters)"),
            url=full_url,
            in_range=True,
        ))
    return out


def fetch_many_tears() -> list[Dog]:
    dogs: list[Dog] = []
    seen_urls: set[str] = set()
    try:
        r = requests.get(_MT_LIST, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Many Tears] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Many Tears"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    html = r.text
    nums = [int(n) for n in re.findall(r"[?&]page=(\d+)", html)]
    max_page = max(nums) if nums else 1
    for d in _mt_parse_page(html):
        if d.url not in seen_urls:
            seen_urls.add(d.url)
            dogs.append(d)
    for p in range(2, max_page + 1):
        try:
            rr = requests.get(_MT_LIST, params={"page": p},
                              headers=HEADERS, timeout=TIMEOUT)
            if rr.status_code != 200:
                break
            for d in _mt_parse_page(rr.text):
                if d.url not in seen_urls:
                    seen_urls.add(d.url)
                    dogs.append(d)
        except requests.RequestException:
            break
    return [d for d in dogs if _r3_matches_breed(f"{d.breed} {d.name}")]


_LL_URL = "https://www.lurcher-link.org/dogs"
_LL_SKIP = re.compile(
    r"^(Slide\b|Slide\s*\d+$|Logo\b|.*\.(jpg|png|gif|webp)$)", re.I)
_LL_DOG = re.compile(
    r"^\s*([A-Z][A-Za-z'\-]{1,20}(?:\s+(?:and|AND|&|PUP|Pup|pup)\s+[A-Z][A-Za-z'\-]{1,20})?)"
    r"\s*(?:[-,(]|in\b|with\b|on\b|at\b|\d|$)")


def _ll_parse_alt(alt: str) -> tuple[str, str] | None:
    a = (alt or "").strip()
    if not a or _LL_SKIP.match(a):
        return None
    m = _LL_DOG.match(a)
    if not m:
        return None
    name = m.group(1).strip()
    rest = a[m.end(1):].strip(" -,:()")
    return name, rest


def fetch_lurcher_link() -> list[Dog]:
    try:
        r = requests.get(_LL_URL, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Lurcher Link] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Lurcher Link"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    s = BeautifulSoup(r.text, "lxml")
    for t in s(["nav", "header", "footer", "script", "style"]):
        t.decompose()
    seen_keys: set[tuple[str, str]] = set()
    dogs: list[Dog] = []
    for img in s.find_all("img", alt=True):
        parsed = _ll_parse_alt(img.get("alt", ""))
        if not parsed:
            continue
        name, loc_blob = parsed
        key = (name.upper(), loc_blob.upper())
        if key in seen_keys:
            continue
        seen_keys.add(key)
        m_breed = re.search(
            r"\b(spaniel|collie|springer|cocker|sprocker|sprollie|whippet|"
            r"greyhound|saluki|deerhound|lurcher|galgo|podenco)\b",
            loc_blob, re.I)
        breed = m_breed.group(1).title() if m_breed else "Lurcher / Sighthound"
        dogs.append(Dog(
            source="Lurcher Link",
            name=name.title(),
            breed=breed,
            age=None,
            sex=None,
            location=(f"Lurcher Link ({loc_blob})"
                      if loc_blob else "Lurcher Link (UK-wide fosters)"),
            url=_LL_URL,
            in_range=True,
        ))
    return [d for d in dogs
            if _r3_matches_breed(f"{d.breed} {d.name} {d.location or ''}")
            or re.search(r"\bspaniel\b", d.breed or "", re.I)]


# =====================================================================
# Round 4: additional rescue adapters
# =====================================================================

def fetch_foal_farm() -> list[Dog]:
    """Foal Farm (Biggin Hill, Kent) — listing links to /dogs/<slug>/ pages."""
    url = "https://foalfarm.org.uk/dogs-needing-a-home/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Foal Farm] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Foal Farm"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    href_rx = re.compile(r"foalfarm\.org\.uk/dogs/[^/]+/?$", re.I)
    dog_urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"].rstrip("/")
        if href_rx.search(h) and h not in seen:
            seen.add(h)
            dog_urls.append(h + "/")
    dogs: list[Dog] = []
    for durl in dog_urls:
        try:
            rd = requests.get(durl, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            continue
        if rd.status_code != 200:
            continue
        ds = BeautifulSoup(rd.text, "lxml")
        for t in ds(["script", "style"]):
            t.decompose()
        h1 = ds.find("h1")
        name = ""
        if h1:
            name = h1.get_text(" ", strip=True)
            name = re.sub(r"^\s*(meet|introducing)\s+", "", name, flags=re.I).strip()
        if not name:
            slug = durl.rstrip("/").rsplit("/", 1)[-1]
            name = slug.replace("-", " ").title()
        body_text = (ds.find("main") or ds.body or ds).get_text(" ", strip=True)
        breed = ""
        m = re.search(r"Breed\s*[:\-]\s*([A-Za-z][A-Za-z0-9 /&'+-]+?)"
                      r"(?=\s*[•·|]|\s+Gender|\s+Sex|\s+Age|\s+Can\s+I|\s*$)",
                      body_text, re.I)
        if m:
            breed = m.group(1).strip(" .,-")
        age = None
        m2 = re.search(r"Age\s*[:\-]\s*([0-9].{0,30}?Years?|[A-Za-z ]{3,30}?Years?|[A-Za-z ]{3,30}?(?:old))",
                       body_text, re.I)
        if m2:
            age = m2.group(1).strip()
        sex = None
        m3 = re.search(r"Gender\s*[:\-]\s*(Male|Female)\b", body_text, re.I)
        if m3:
            sex = m3.group(1).capitalize()
        if not _r3_matches_breed(breed) and not _r3_matches_breed(name + " " + body_text[:500]):
            continue
        # If the formal breed label is generic but the body text confirms a
        # collie/spaniel component, surface that in the display breed so users
        # aren't confused by a "Crossbreed" / "Staffy BT Cross" label.
        if not _r3_matches_breed(breed):
            hint_m = re.search(
                r"(border\s*collie|bearded\s*collie|beardie|\bcollie\b|"
                r"springer\s*spaniel|sprollie|sprocker|cocker\s*spaniel|"
                r"\bspringer\b|\bcocker\b)[^.]{0,60}\bcross\b|"
                r"\bcross\b[^.]{0,40}(border\s*collie|\bcollie\b|springer|\bcocker\b)|"
                r"(\bcollie\s+\w+\s*(?:mix|cross)|\w+\s*/\s*collie\s*(?:mix|cross)?|"
                r"collie\s*/\s*\w+\s*(?:mix|cross)?)",
                body_text[:2000], re.I,
            )
            if hint_m:
                hint = hint_m.group(0).strip()
                breed = f"{breed or 'Crossbreed'} (body: {hint[:50]})"
        reserved = bool(_R3_RESERVED.search(body_text[:2000]))
        dogs.append(Dog(
            source="Foal Farm",
            name=name,
            breed=breed or "(breed unstated)",
            age=age,
            sex=sex,
            location="Foal Farm (Biggin Hill, Kent)",
            url=durl,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


def fetch_dbarc() -> list[Dog]:
    """DBARC (Hurst, Wokingham) — Squarespace listing → per-dog /p/ pages."""
    url = "https://dbarc.org.uk/animals-for-rehoming/dog"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[DBARC] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        try:
            r = requests.get("https://dbarc.org.uk/animals-for-rehoming",
                             headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return []
        if r.status_code != 200:
            print(f"[{"DBARC"}] HTTP {r.status_code}", file=sys.stderr)
            return []
    soup = BeautifulSoup(r.text, "lxml")
    tiles = soup.select("a[href*='/animals-for-rehoming/p/']")
    seen: set[str] = set()
    entries: list[tuple[str, str]] = []
    for a in tiles:
        href = a.get("href") or ""
        full = href if not href.startswith("/") else urljoin("https://dbarc.org.uk", href)
        if full in seen:
            continue
        seen.add(full)
        name = a.get_text(" ", strip=True)
        if not name:
            continue
        entries.append((name, full))
    dogs: list[Dog] = []
    for name, durl in entries:
        try:
            rd = requests.get(durl, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            continue
        if rd.status_code != 200:
            continue
        ds = BeautifulSoup(rd.text, "lxml")
        for t in ds(["script", "style"]):
            t.decompose()
        body = (ds.find("main") or ds.body or ds).get_text(" ", strip=True)
        if re.search(r"\bBREED\s*[:\-]\s*Cat\b", body, re.I):
            continue
        if re.search(r"\b(Rabbit|Guinea Pig)\b", body[:400], re.I) and not re.search(r"\bDog\b", body[:400], re.I):
            continue
        breed = ""
        m = re.search(r"BREED\s*[:\-]\s*([^\n]+?)(?=\s+SEX\b|\s+AGE\b|\s+GENDER\b|\s*$)", body, re.I)
        if m:
            breed = m.group(1).strip(" .,-")
        age = None
        m2 = re.search(r"AGE\s*[:\-]\s*([^\n]+?)(?=\s+(?:SEX|BREED|SIZE|WEIGHT|GENDER)\b|\s+Please\b|\s+Purchase\b|\s*$)",
                       body, re.I)
        if m2:
            age = m2.group(1).strip(" .,-")[:60]
        sex = None
        m3 = re.search(r"SEX\s*[:\-]\s*(Male|Female)\b", body, re.I)
        if m3:
            sex = m3.group(1).capitalize()
        if not _r3_matches_breed(breed) and not _r3_matches_breed(name + " " + body[:500]):
            continue
        reserved = bool(_R3_RESERVED.search(body[:2000]))
        dogs.append(Dog(
            source="DBARC",
            name=name.title(),
            breed=breed or "(breed unstated)",
            age=age,
            sex=sex,
            location="DBARC (Hurst, Wokingham)",
            url=durl,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


def fetch_cinque_ports() -> list[Dog]:
    """Cinque Ports Rescue (Kent) — WP category, article title 'NAME: BREED'."""
    base = "https://cinqueportsrescue.org.uk/category/dogs-needing-adoption/"
    dogs: list[Dog] = []
    seen_urls: set[str] = set()
    page_url = base
    for _ in range(8):
        try:
            r = requests.get(page_url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"[Cinque Ports] {e}", file=sys.stderr)
            break
        if r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, "lxml")
        arts = soup.select("article.uagb-post__inner-wrap") or soup.select("article")
        if not arts:
            break
        for a in arts:
            ttl_el = a.select_one(".uagb-post__title") or a.find(["h1", "h2", "h3"])
            if not ttl_el:
                continue
            lnk = ttl_el.find("a") or a.find("a", href=True)
            if not lnk:
                continue
            durl = lnk.get("href") or ""
            if not durl or durl in seen_urls:
                continue
            seen_urls.add(durl)
            title = ttl_el.get_text(" ", strip=True)
            m = re.match(r"^([^:]+):\s*(.+)$", title)
            if m:
                name = m.group(1).strip().title()
                breed = m.group(2).strip()
            else:
                name = title.strip().title()
                breed = ""
            excerpt = a.get_text(" ", strip=True)
            if not _r3_matches_breed(breed) and not _r3_matches_breed(name + " " + excerpt[:500]):
                continue
            reserved = bool(_R3_RESERVED.search(excerpt[:800]))
            dogs.append(Dog(
                source="Cinque Ports Rescue",
                name=name,
                breed=breed or "(breed unstated)",
                age=None,
                sex=None,
                location="Cinque Ports Rescue (Kent)",
                url=durl,
                in_range=True,
                reserved=reserved,
            ))
        nxt = soup.select_one("a.next.page-numbers") or soup.find("a", class_="next")
        if nxt and nxt.get("href") and nxt["href"] not in (page_url, base):
            page_url = nxt["href"]
        else:
            break
    return dogs


def fetch_alldogsmatter() -> list[Dog]:
    """AllDogsMatter (London N2) — Elementor cards with inline Breed/Age/Gender."""
    url = "https://alldogsmatter.co.uk/dogs/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[AllDogsMatter] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"AllDogsMatter"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    for c in soup.select(".card"):
        a = c.find("a", href=True)
        durl = a.get("href") if a else ""
        if not durl or "/dogs/" not in durl or durl in seen:
            continue
        seen.add(durl)
        text = c.get_text(" ", strip=True)
        name = ""
        mn = re.match(r"^([A-Za-z][A-Za-z .'&-]{0,40}?)\s+Breed\s*:", text)
        if mn:
            name = mn.group(1).strip()
        else:
            slug = durl.rstrip("/").rsplit("/", 1)[-1]
            name = slug.replace("-", " ").title()
        breed = ""
        mb = re.search(r"Breed\s*:\s*(.+?)\s+Age\s*:", text, re.I)
        if mb:
            breed = mb.group(1).strip()
        age = None
        ma = re.search(r"Age\s*:\s*(.+?)\s+Gender\s*:", text, re.I)
        if ma:
            age = ma.group(1).strip()
        sex = None
        mx = re.search(r"Gender\s*:\s*(Male|Female)\b", text, re.I)
        if mx:
            sex = mx.group(1).capitalize()
        location_in_card = ""
        mloc = re.search(r"Location\s*:\s*([A-Za-z][A-Za-z0-9 ,.-]{2,60}?)\s+Can\s+", text, re.I)
        if mloc:
            location_in_card = mloc.group(1).strip(" .,-")
        if not _r3_matches_breed(breed) and not _r3_matches_breed(name + " " + text[:500]):
            continue
        reserved = bool(_R3_RESERVED.search(text[:500]))
        loc = f"AllDogsMatter ({location_in_card})" if location_in_card else "AllDogsMatter (London N2)"
        dogs.append(Dog(
            source="AllDogsMatter",
            name=name.title(),
            breed=breed or "(breed unstated)",
            age=age,
            sex=sex,
            location=loc,
            url=durl,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


def fetch_mayhew() -> list[Dog]:
    """Mayhew (London NW10) — known to 403 without rich headers."""
    url = "https://themayhew.org/dogs/"
    attempts = [
        {**HEADERS, "Referer": "https://themayhew.org/",
         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"),
         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
         "Accept-Language": "en-GB,en;q=0.9",
         "Referer": "https://www.google.com/", "Upgrade-Insecure-Requests": "1"},
    ]
    r = None
    for h in attempts:
        try:
            r = requests.get(url, headers=h, timeout=TIMEOUT)
        except requests.RequestException:
            r = None
            continue
        if r.status_code == 200:
            break
    if not r or r.status_code != 200:
        print(f"[Mayhew] blocked or non-200", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    href_rx = re.compile(r"^https?://themayhew\.org/dogs/[^/]+/?$")
    SKIP_SLUGS = {"not-sure-which-dog"}
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if not href_rx.match(h):
            continue
        slug = h.rstrip("/").rsplit("/", 1)[-1]
        if slug == "dogs" or slug in SKIP_SLUGS or h in seen:
            continue
        seen.add(h)
        card_text = ""
        p = a
        for _ in range(8):
            p = p.parent
            if p is None:
                break
            classes = p.get("class") or []
            if any("animal-block" in c for c in classes):
                card_text = p.get_text(" ", strip=True)
                break
        if not card_text:
            p = a
            for _ in range(6):
                p = p.parent
                if p is None:
                    break
                t = p.get_text(" ", strip=True)
                if 15 < len(t) < 400:
                    card_text = t
                    break
        ct = re.sub(r"^\s*(Star\s*Dog|New|Rehomed|Reserved|On\s*Hold)\s+", "", card_text, flags=re.I)
        ct = re.sub(r"\s+Meet\s+[A-Za-z'&-]+\s*$", "", ct, flags=re.I)
        name = ""
        age = None
        breed = ""
        mcore = re.match(
            r"^([A-Za-z][A-Za-z .'&-]{0,40}?)\s+"
            r"(\d+\s*years?(?:\s*\d+\s*months?)?\s*old|\d+\s*months?\s*old|Puppy|Young|Adult|Senior)\s+"
            r"(.+)$",
            ct, re.I,
        )
        if mcore:
            name = mcore.group(1).strip()
            age = mcore.group(2).strip()
            breed = mcore.group(3).strip()
        else:
            name = slug.replace("-", " ").title()
            breed = ct
        if not _r3_matches_breed(breed) and not _r3_matches_breed(name + " " + card_text):
            continue
        reserved = bool(_R3_RESERVED.search(card_text))
        dogs.append(Dog(
            source="Mayhew",
            name=name.title(),
            breed=breed or "(breed unstated)",
            age=age,
            sex=None,
            location="Mayhew (London NW10)",
            url=h,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


# --- Greyhound and Lurcher Rescue (aggregator) ---

_GLR_BASE = "https://greyhoundandlurcherrescue.co.uk"
_GLR_NOT_YET = re.compile(r"\bnot\s+(?:yet\s+)?available|not\s+ready|coming\s+soon|"
                          r"pending\s+assessment|not\s+available\s+yet\b", re.I)


def _glr_cards() -> list[tuple[str, str, str, str, str]]:
    records: list[tuple[str, str, str, str, str]] = []
    for page in range(1, 8):
        suffix = "" if page == 1 else f"?page={page}"
        url = f"{_GLR_BASE}/adopt-a-dog/{suffix}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"[G&LR] page {page}: {e}", file=sys.stderr)
            break
        if r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, "lxml")
        cards = soup.find_all("div", class_="dogtext")
        if not cards:
            break
        for c in cards:
            a = c.find("a", class_="doglistlink", href=True)
            if not a:
                continue
            name = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
            href = a["href"]
            if href.startswith("/"):
                href = _GLR_BASE + href
            ps = c.find_all("p")
            meta = re.sub(r"\s+", " ", ps[0].get_text(" ", strip=True)).strip() if ps else ""
            age = sex = breed = ""
            for p in [x.strip() for x in meta.split("|")]:
                lp = p.lower()
                if lp.startswith("age:"):
                    age = p.split(":", 1)[1].strip()
                elif lp in ("bitch", "dog") or "bitch" in lp:
                    sex = p
                else:
                    breed = p
            records.append((name, href, age, sex, breed))
    return records


def fetch_greyhound_lurcher_rescue() -> list[Dog]:
    records = _glr_cards()
    candidates: list[tuple[tuple[str, str, str, str, str], bool]] = []
    for rec in records:
        name, url, age, sex, breed = rec
        if _r3_matches_breed(f"{name} {breed}"):
            candidates.append((rec, True))
        elif "lurcher" in (breed or "").lower():
            candidates.append((rec, False))

    def probe(item):
        (name, url, age, sex, breed), already = item
        detail_text = ""
        rescue = None
        if not already:
            try:
                r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            except requests.RequestException:
                return None
            if r.status_code != 200:
                return None
            s = BeautifulSoup(r.text, "lxml")
            for t in s(["script", "style", "nav", "footer", "header"]):
                t.decompose()
            main = s.find("main") or s.body
            detail_text = main.get_text(" ", strip=True) if main else ""
            if not _r3_matches_breed(f"{name} {breed} {detail_text}"):
                return None
            m = re.search(r"Rescue\s*:?\s*([^\n\r]+?)(?:Website|Telephone|About\s+Me|$)",
                          detail_text)
            if m:
                rescue = re.sub(r"\s+", " ", m.group(1)).strip()[:80]
        final_breed = breed or "Lurcher"
        if detail_text:
            m2 = re.search(
                r"(border\s*collie[ \-]*(?:cross|x)?|bearded\s*collie|collie[ \-]*(?:cross|lurcher|x)?|"
                r"sprollie|springer\s*spaniel|springer|sprocker|cocker\s*spaniel)",
                detail_text, re.I,
            )
            if m2:
                final_breed = f"{breed} ({m2.group(1).strip()})" if breed else m2.group(1).strip()
        reserved = bool(detail_text and (_R3_RESERVED.search(detail_text)
                                         or _GLR_NOT_YET.search(detail_text)))
        loc = (f"Greyhound and Lurcher Rescue ({rescue})" if rescue
               else "Greyhound and Lurcher Rescue (aggregator; UK-wide)")
        return Dog(
            source="Greyhound and Lurcher Rescue",
            name=name,
            breed=final_breed,
            age=age or None,
            sex=sex or None,
            location=loc,
            url=url,
            in_range=True,
            reserved=reserved,
        )

    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, candidates):
            if d is not None:
                dogs.append(d)
    return dogs


# --- EGLR (Evesham Greyhound & Lurcher Rescue, Worcs; UK-wide home-check) ---

_EGLR_BASE = "https://lurcher.org.uk"


def _eglr_cards() -> list[tuple[str, str, str]]:
    cards: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for page in range(1, 10):
        url = f"{_EGLR_BASE}/category/adopt/" if page == 1 else f"{_EGLR_BASE}/category/adopt/page/{page}/"
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"[EGLR] page {page}: {e}", file=sys.stderr)
            break
        if r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, "lxml")
        arts = soup.find_all("article")
        if not arts:
            break
        for art in arts:
            h = art.find(["h2", "h3"])
            if not h:
                continue
            a = h.find("a", href=True)
            if not a:
                continue
            title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
            href = a["href"]
            if href in seen:
                continue
            seen.add(href)
            if "–" in title:
                name, status = title.split("–", 1)
            elif "-" in title:
                name, status = title.split("-", 1)
            else:
                name, status = title, ""
            cards.append((name.strip(), status.strip(), href))
    return cards


def fetch_eglr() -> list[Dog]:
    cards = _eglr_cards()
    keep: list[tuple[str, str, str]] = []
    for name, status, url in cards:
        if _GLR_NOT_YET.search(status.lower()):
            continue
        keep.append((name, status, url))

    def probe(item):
        name, status, url = item
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "lxml")
        for t in soup(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = soup.find("main") or soup.body
        text = main.get_text(" ", strip=True) if main else ""
        if not text or _GLR_NOT_YET.search(text[:500]):
            return None
        if not _r3_matches_breed(f"{name} {text}"):
            return None
        age_m = re.search(r"\b(\d+\s*(?:years?|yrs?|months?|mon))\b", text, re.I)
        age = age_m.group(1) if age_m else None
        sex = None
        if re.search(r"\bfemale\b", text, re.I):
            sex = "Female"
        elif re.search(r"\bmale\b", text, re.I):
            sex = "Male"
        coat_m = re.search(r"\b(smooth|broken|rough|fluffy|wire)\s*coat", text, re.I)
        coat = coat_m.group(1).title() + " Coat" if coat_m else None
        reserved = bool(re.search(r"\breserved\b", status, re.I)) or \
                   bool(re.search(r"\breserved\b", text[:600], re.I))
        bm = re.search(
            r"(border\s*collie[ \-]*(?:cross|x)?|bearded\s*collie|beardie|sprollie|"
            r"collie[ \-]*(?:cross|lurcher|x)|\bcollie\b|springer\s*spaniel|"
            r"\bsprocker\b|cocker\s*spaniel|\bspringer\b)",
            text, re.I,
        )
        breed = bm.group(1).strip().title() if bm else "Lurcher (collie family)"
        loc_base = "EGLR (Ashton-under-Hill, Worcs)"
        location = f"{loc_base} - {coat}" if coat else loc_base
        return Dog(
            source="EGLR",
            name=name.title() if name.isupper() else name,
            breed=breed,
            age=age,
            sex=sex,
            location=location,
            url=url,
            in_range=True,
            reserved=reserved,
        )

    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, keep):
            if d is not None:
                dogs.append(d)
    return dogs


# --- Mikey's Dog Rescue (Dorset, Shopify) ---

_MIKEY_BASE = "https://mikeysdogrescue.co.uk"
_MIKEY_PAGES = ["/pages/adopt-a-dog", "/pages/adopt-a-dog-page-2-of-2"]


def _mikey_parse_page(url: str) -> list[Dog]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Mikey's] {url}: {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Mikey's"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    for block in soup.select("div.image-with-text"):
        heading = block.select_one("h2.image-with-text__heading")
        text_div = block.select_one("div.image-with-text__text")
        if not heading or not text_div:
            continue
        name = heading.get_text(" ", strip=True)
        if not name:
            continue
        breed_h3 = text_div.find("h3")
        breed_line = breed_h3.get_text(" ", strip=True) if breed_h3 else ""
        block_text = text_div.get_text(" ", strip=True)
        if not breed_line or not _r3_matches_breed(breed_line):
            continue
        parts = [p.strip() for p in re.split(r"\s*[-–—]\s*", breed_line) if p.strip()]
        breed = parts[0] if parts else breed_line
        sex = None
        age = None
        for p in parts[1:]:
            pl = p.lower()
            if "male" in pl or "female" in pl:
                sex = "Female" if "female" in pl else "Male"
            elif re.search(r"\d", p):
                age = p
        reserved = bool(re.search(r"reserved|applications\s*closed|adopted", block_text, re.I))
        dogs.append(Dog(
            source="Mikey's Dog Rescue",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Mikey's Dog Rescue (Dorset)",
            url=url,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


def fetch_mikeys_dog_rescue() -> list[Dog]:
    seen_names: set[str] = set()
    out: list[Dog] = []
    for path in _MIKEY_PAGES:
        for d in _mikey_parse_page(_MIKEY_BASE + path):
            k = d.name.lower()
            if k in seen_names:
                continue
            seen_names.add(k)
            out.append(d)
    return out


# =====================================================================
# Dali Dog Rescue UK (Cyprus charity with UK foster network)
# =====================================================================

_DALI_BASE = "https://www.dalidogrescue.uk"
_DALI_LIST = f"{_DALI_BASE}/dogs-needing-homes/in-the-uk"
# Strip "Currently in (the) UK" / "In the UK" variants — they are status markers,
# not locations. A remaining parens group is a real town/region.
_DALI_UK_STATUS = re.compile(
    r"\b(?:currently\s+)?in(?:\s+the)?\s+uk\b", re.I
)


def _dali_parse_label(label: str) -> tuple[str, str | None]:
    """Return (name, location_or_None) from a Dali aria-label string."""
    s = label.strip()
    # Strip RESERVED marker first so name extraction isn't polluted.
    s = re.sub(r"\s*\bRESERVED\b\s*", " ", s, flags=re.I).strip()
    loc = None
    # Pull a trailing "(...)" group if it's not just a UK-status marker.
    m = re.search(r"\(([^)]+)\)\s*$", s)
    if m:
        inner = m.group(1).strip()
        if not _DALI_UK_STATUS.fullmatch(inner):
            loc = inner
        s = s[:m.start()].rstrip()
    # Strip trailing "- in the UK" / "- Currently in the UK" / "Currently in UK".
    s = re.sub(r"[\-–—]\s*(?:currently\s+)?in(?:\s+the)?\s+uk\s*$", "", s, flags=re.I)
    s = re.sub(r"\s*(?:currently\s+)?in(?:\s+the)?\s+uk\s*$", "", s, flags=re.I)
    name = s.strip(" -–—").strip()
    return name, loc


def fetch_dali() -> list[Dog]:
    try:
        r = requests.get(_DALI_LIST, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Dali] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Dali"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    for a in soup.select("a.product-list-item-link"):
        href = a.get("href", "")
        if not href or "/dogs-needing-homes/p/" not in href:
            continue
        if href.startswith("/"):
            href = _DALI_BASE + href
        if href in seen:
            continue
        seen.add(href)
        label = a.get("aria-label", "") or a.get_text(" ", strip=True)
        reserved = bool(re.search(r"\bRESERVED\b", label, re.I))
        name, loc = _dali_parse_label(label)
        if not name:
            continue
        # Fetch detail page once per dog; Dali puts breed clues in og:description
        # + body prose ("Mixed-Breed Girl", "collie-type", etc).
        breed_text = ""
        try:
            rd = requests.get(href, headers=HEADERS, timeout=TIMEOUT)
            if rd.status_code == 200:
                ds = BeautifulSoup(rd.text, "lxml")
                ogd = ds.find("meta", attrs={"property": "og:description"})
                if ogd and ogd.get("content"):
                    breed_text += " " + ogd["content"]
                for t in ds(["script", "style", "nav", "footer", "header"]):
                    t.decompose()
                body = ds.find("main") or ds.body
                if body:
                    breed_text += " " + body.get_text(" ", strip=True)[:4000]
        except requests.RequestException:
            pass
        scope = f"{name} {label} {breed_text}"
        if not _r3_matches_breed(scope):
            continue
        breed = extract_breed(breed_text) if breed_text else "Mixed-Breed (Dali)"
        loc_str = f"Dali ({loc})" if loc else "Dali foster (UK)"
        dogs.append(Dog(
            source="Dali Dog Rescue",
            name=name,
            breed=breed,
            age=None,
            sex=None,
            location=loc_str,
            url=href,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


# =====================================================================
# MossMania Dog Rescue (Romania charity with UK foster network)
# =====================================================================

_MOSS_BASE = "https://mossmaniadogrescue.co.uk"
_MOSS_LIST = f"{_MOSS_BASE}/dogs-in-the-uk/"


def fetch_mossmania() -> list[Dog]:
    try:
        r = requests.get(_MOSS_LIST, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[MossMania] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"MossMania"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    for block in soup.select("div.ultp-block-item"):
        ha = block.select_one("h3.ultp-block-title a")
        if not ha:
            continue
        href = ha.get("href", "")
        name = ha.get_text(" ", strip=True)
        if not name or not href or href in seen:
            continue
        seen.add(href)
        loc_el = block.select_one("p.ultp-dynamic-content-field-dc")
        loc_raw = loc_el.get_text(" ", strip=True) if loc_el else ""
        # Ignore locations that are clearly data-entry noise (equals the name,
        # very short, or has no letters).
        if loc_raw.lower() == name.lower() or len(loc_raw) < 3:
            loc_raw = ""
        # Fetch detail for breed text.
        breed_text = ""
        reserved = False
        try:
            rd = requests.get(href, headers=HEADERS, timeout=TIMEOUT)
            if rd.status_code == 200:
                ds = BeautifulSoup(rd.text, "lxml")
                for t in ds(["script", "style", "nav", "footer", "header"]):
                    t.decompose()
                body = ds.find("main") or ds.body
                if body:
                    btxt = body.get_text(" ", strip=True)
                    breed_text = btxt[:5000]
                    reserved = bool(_R3_RESERVED.search(btxt[:1500]))
        except requests.RequestException:
            pass
        scope = f"{name} {breed_text}"
        if not _r3_matches_breed(scope):
            continue
        breed = extract_breed(breed_text, fallback="Crossbreed")
        loc_str = f"MossMania ({loc_raw})" if loc_raw else "MossMania foster (UK)"
        dogs.append(Dog(
            source="MossMania",
            name=name,
            breed=breed,
            age=None,
            sex=None,
            location=loc_str,
            url=href,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


# =====================================================================
# Hampshire Paws (Wix Pro Gallery, Hampshire)
# =====================================================================

_HP_URL = "https://www.hampshirepaws.co.uk/adoptme"


def fetch_hampshire_paws() -> list[Dog]:
    try:
        r = requests.get(_HP_URL, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Hampshire Paws] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Hampshire Paws"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    for item in soup.find_all(attrs={"data-testid": "gallery-item-item"}):
        img = item.find("img")
        name = (img.get("alt") if img else "") or ""
        name = re.sub(r"^\s*[\U0001F400-\U0001FAFF\s]+", "", name).strip()
        if not name or name.lower().startswith("rabbit"):
            continue
        if name in seen:
            continue
        seen.add(name)
        caption = item.get_text("\n", strip=True)
        # Strip emoji/name header lines and "press to zoom" footer.
        caption = re.sub(r"press to zoom", "", caption, flags=re.I).strip()
        # Extract location line.
        loc_m = re.search(r"Location\s*:\s*([^\n]+)", caption, re.I)
        location_field = loc_m.group(1).strip() if loc_m else ""
        # Breed/age pipe-delimited ("6 years | Female | Chow Chow") or labelled
        # ("Breed/size: Terrier, small").
        breed_raw = ""
        age = None
        sex = None
        mbs = re.search(r"Breed(?:/size)?\s*:\s*([^\n]+)", caption, re.I)
        if mbs:
            breed_raw = mbs.group(1).strip()
        pipe_line = ""
        for line in caption.split("\n"):
            if line.count("|") >= 1 and re.search(r"\b(year|month|male|female)\b", line, re.I):
                pipe_line = line
                break
        if pipe_line:
            parts = [p.strip() for p in pipe_line.split("|") if p.strip()]
            for p in parts:
                pl = p.lower()
                if re.search(r"\d+\s*(?:year|month)", pl):
                    age = p
                elif pl in ("male", "female"):
                    sex = p.capitalize()
                elif not breed_raw:
                    breed_raw = p
        # Age on its own labelled line ("Age: 6 years").
        if not age:
            ma = re.search(r"Age\s*:\s*([^\n|]+)", caption, re.I)
            if ma:
                age = ma.group(1).strip()
        if not sex:
            mg = re.search(r"Gender\s*:\s*(Male|Female)\b", caption, re.I)
            if mg:
                sex = mg.group(1).capitalize()
        scope = f"{name} {breed_raw} {caption}"
        if not _r3_matches_breed(scope):
            continue
        breed = extract_breed(breed_raw or caption, fallback=breed_raw or "(breed unstated)")
        loc_str = f"Hampshire Paws ({location_field})" if location_field else "Hampshire Paws (Hampshire)"
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
        dogs.append(Dog(
            source="Hampshire Paws",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location=loc_str,
            url=f"{_HP_URL}#{slug}",
            in_range=True,
        ))
    return dogs


# =====================================================================
# STAR Cyprus (WP, UK-foster ribbon filter)
# =====================================================================

_STAR_LIST = "https://staradopt.org/list-dogs/"
_STAR_UK_RIBBON = re.compile(r"in\s*uk\s*foster", re.I)
_STAR_FOSTER_SUFFIX = re.compile(
    r"\s*(?:in\s+)?[Ff]oster(?:ed)?\s+in\s+(.+?)\s*$"
)


def _star_clean_name(raw: str) -> tuple[str, str | None]:
    """Strip 'foster in X' suffix and leading emojis/ribbons; return (name, loc)."""
    s = re.sub(r"^\s*[\U0001F300-\U0001FAFF\s]+", "", raw).strip()
    s = re.sub(r"\bAka\s+\w+\b", "", s, flags=re.I).strip()
    loc = None
    m = _STAR_FOSTER_SUFFIX.search(s)
    if m:
        loc = m.group(1).strip()
        s = s[:m.start()].strip()
    return s, loc


def fetch_star_cyprus() -> list[Dog]:
    try:
        r = requests.get(_STAR_LIST, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[STAR Cyprus] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"STAR Cyprus"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    for c in soup.select("div.card"):
        ribbon = c.select_one(".ribbon")
        if not ribbon or not _STAR_UK_RIBBON.search(ribbon.get_text(" ", strip=True)):
            continue
        a = c.find("a", href=True)
        title = c.select_one("h5.card-title")
        if not a or not title:
            continue
        href = a["href"]
        if href.startswith("/"):
            href = "https://staradopt.org" + href
        if href in seen:
            continue
        seen.add(href)
        name, list_loc = _star_clean_name(title.get_text(" ", strip=True))
        if not name:
            continue
        # Detail page has structured <li><strong>Field:</strong> Value</li>.
        breed = ""
        age = None
        sex = None
        detail_loc = None
        try:
            rd = requests.get(href, headers=HEADERS, timeout=TIMEOUT)
            if rd.status_code == 200:
                ds = BeautifulSoup(rd.text, "lxml")
                for li in ds.find_all("li"):
                    strong = li.find("strong")
                    if not strong:
                        continue
                    label = strong.get_text(" ", strip=True).rstrip(":").lower()
                    val = li.get_text(" ", strip=True)
                    val = re.sub(r"^[^:]*:\s*", "", val).strip()
                    if label == "breed":
                        breed = val
                    elif label == "age":
                        age = val
                    elif label == "sex":
                        sex = val
                body = ds.find("main") or ds.body
                if body:
                    btxt = body.get_text(" ", strip=True)
                    mloc = re.search(r"Currently\s+in\s*:?\s*([A-Z][A-Za-z .'&-]{2,40})", btxt)
                    if mloc:
                        detail_loc = mloc.group(1).strip()
        except requests.RequestException:
            pass
        # Filter: breed must match. "Mixed Breed/English Springer Spaniel" style.
        if not _r3_matches_breed(f"{breed} {name}"):
            continue
        location = detail_loc or list_loc or "UK foster"
        loc_str = f"STAR Cyprus ({location})"
        dogs.append(Dog(
            source="STAR Cyprus",
            name=name,
            breed=breed or "Mixed Breed",
            age=age,
            sex=sex,
            location=loc_str,
            url=href,
            in_range=True,
        ))
    return dogs


# =====================================================================
# A New Leash For Life (Google Sites, text-marker parse)
# =====================================================================

_ANLFL_URL = "https://www.anewleashforliferescue.co.uk/available-doggies"
# Matches "Name <heart> Available for adoption/foster/...". Name must start
# with a real uppercase letter (Google Sites occasionally splits a name across
# <span>s, producing "Marn ie" — a single trailing lowercase fragment is
# tolerated but not a second uppercase word, which would be a different dog).
_ANLFL_HEARTS = "❤♥\U0001F495\U0001F497\U0001F498\U0001F499\U0001F49A\U0001F49B\U0001F49C\U0001F49D\U0001F49E\U0001F49F\U0001F496❣\U0001F90D\U0001F90E\U0001F5A4"
_ANLFL_ENTRY = re.compile(
    r"([A-Z][A-Za-z]+(?:\s+[a-z]+)?(?:\s*\(aka\s+[A-Za-z]+\))?)"
    r"\s*[" + _ANLFL_HEARTS + r"️]+\s*"
    r"[Aa]vailable\s+for\s+"
)


def fetch_anewleash() -> list[Dog]:
    try:
        r = requests.get(_ANLFL_URL, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[ANewLeash] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"ANewLeash"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    for t in soup(["script", "style"]):
        t.decompose()
    text = soup.get_text(" ", strip=True)
    dogs: list[Dog] = []
    seen: set[str] = set()
    matches = list(_ANLFL_ENTRY.finditer(text))
    for i, m in enumerate(matches):
        raw_name = m.group(1).strip()
        # Rejoin a mid-name split like "Marn ie" → "Marnie", but preserve a
        # trailing "(aka X)" alias intact.
        alias_m = re.search(r"\s*\(aka\s+[A-Za-z]+\)\s*$", raw_name)
        if alias_m:
            base = raw_name[:alias_m.start()].strip()
            alias = alias_m.group(0).strip()
            base_clean = re.sub(r"\s+", "", base) if re.fullmatch(r"[A-Z][a-z]+\s+[a-z]+", base) else base
            name = f"{base_clean} {alias}"
        else:
            name = re.sub(r"\s+", "", raw_name) if re.fullmatch(r"[A-Z][a-z]+\s+[a-z]+", raw_name) else raw_name
        if name.lower() in seen:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end]
        if not _r3_matches_breed(name + " " + body):
            continue
        seen.add(name.lower())
        breed = extract_breed(body, fallback="(breed unstated)")
        loc_m = re.search(r"(?:[Ii]n [Ff]oster(?:ed)?|[Ff]ostered)(?:\s+in|\s+with)?\s+([A-Z][A-Za-z]+(?:shire)?)", body)
        location = loc_m.group(1).strip() if loc_m else ""
        age_m = re.search(r"(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?)\s*old", body, re.I)
        age = age_m.group(1) if age_m else None
        sex = None
        head = body[:400]
        if re.search(r"\bshe\b|\bher\b", head, re.I):
            sex = "Female"
        elif re.search(r"\bhe\b|\bhim\b|\bhis\b", head, re.I):
            sex = "Male"
        loc_str = f"A New Leash ({location})" if location else "A New Leash foster (SE England)"
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
        dogs.append(Dog(
            source="A New Leash For Life",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location=loc_str,
            url=f"{_ANLFL_URL}#{slug}",
            in_range=True,
        ))
    return dogs


# =====================================================================
# Hairy Hounz (Ropley, Hampshire) — single-page custom HTML
# =====================================================================

_HAIRY_URL = "https://hairyhounz.co.uk/"


def fetch_hairy_hounz() -> list[Dog]:
    try:
        r = requests.get(_HAIRY_URL, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[Hairy Hounz] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[{"Hairy Hounz"}] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    # The "DOGS FOR URGENT ADOPTION" block contains today's dogs. Skip any
    # "successfully re-homed" block if present.
    urgent_h3 = None
    for h in soup.find_all("h3"):
        if re.search(r"urgent\s+adoption", h.get_text(" ", strip=True), re.I):
            urgent_h3 = h
            break
    if not urgent_h3:
        return []
    # Collect text from the enclosing container3 (if any) or siblings until we
    # hit another h3 / the re-homed block.
    container = urgent_h3.find_parent(class_="container3") or urgent_h3.parent
    if not container:
        return []
    block_text = container.get_text(" ", strip=True)
    # Cut at "re-homed" / "rehomed" / "old friends" if those strings appear.
    cut = re.search(r"(old\s+friends|successfully\s+re-?homed)", block_text, re.I)
    if cut:
        block_text = block_text[:cut.start()]
    dogs: list[Dog] = []
    # Parse entries of the shape "Name is a [female/male] <breed>, <age>[, ...]".
    # Greedy breed capture up to the next comma/period so "Dachshund" isn't
    # truncated to "Da" by a lazy quantifier meeting an optional age group.
    pattern = re.compile(
        r"\b([A-Z][a-z]+)\s+[Ii]s\s+a\s+"
        r"(?:(female|male)\s+)?"
        r"([A-Z][A-Za-z /-]+)"
        r"(?:,\s*(\d+\s*(?:years?|months?)(?:\s+\d+\s*months?)?\s*old))?",
    )
    seen: set[str] = set()
    for m in pattern.finditer(block_text):
        name = m.group(1).strip()
        if name.lower() in seen:
            continue
        sex = m.group(2).capitalize() if m.group(2) else None
        breed = m.group(3).strip()
        age = m.group(4)
        scope = f"{name} {breed} {block_text[max(0,m.start()-50):m.end()+200]}"
        if not _r3_matches_breed(scope):
            continue
        seen.add(name.lower())
        slug = name.lower()
        dogs.append(Dog(
            source="Hairy Hounz",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="Hairy Hounz (Ropley, Hampshire)",
            url=f"{_HAIRY_URL}#{slug}",
            in_range=True,
        ))
    return dogs


# =====================================================================
# Animal Rescue Charity (Bishops Stortford, Herts CM23 ~80mi)
# WordPress /category/dog-rehoming/ archive — title format "Name: Breed".
# =====================================================================

_ARC_BS_BASE = "https://www.animalrescuecharity.org.uk"

# ARC's WordPress 403s the default UA; Safari + Referer works (same trick as
# Teckels/Margaret Green per adapter_url_gotchas.md).
_ARC_BS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.google.com/",
}


def fetch_arc_bishops_stortford() -> list[Dog]:
    list_url = f"{_ARC_BS_BASE}/category/dog-rehoming/"
    try:
        r = requests.get(list_url, headers=_ARC_BS_HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[ARC Bishops Stortford] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[ARC Bishops Stortford] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    dogs: list[Dog] = []
    seen: set[str] = set()
    for art in soup.find_all("article"):
        a = art.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        if not href.startswith(_ARC_BS_BASE) or "/category/" in href or "/tag/" in href:
            continue
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        if not slug or slug in {"dog-rehoming", "category"}:
            continue
        if href in seen:
            continue
        title_el = art.find(["h1", "h2", "h3"]) or a
        title = title_el.get_text(" ", strip=True)
        body = art.get_text(" ", strip=True)
        if not matches_breed(title) and not matches_breed(body[:600]):
            continue
        if ":" in title:
            name_part, _, breed_part = title.partition(":")
            name = name_part.strip()
            breed = breed_part.strip()
        else:
            name = title.split()[0] if title else slug.replace("-", " ").title()
            breed = "(see listing)"
        if is_breed_noise(breed):
            continue
        seen.add(href)
        sex = "Male" if re.search(r"\b(?:male|boy|he is|him)\b", body, re.I) else \
              ("Female" if re.search(r"\b(?:female|girl|she is|her)\b", body, re.I) else None)
        age_m = re.search(
            r"(\d+(?:\.\d+)?\s*(?:and\s*a\s*half\s*)?(?:years?|months?)(?:\s+\d+\s*months?)?)\s*(?:old)?",
            body, re.I,
        )
        age = age_m.group(1) if age_m else None
        reserved = bool(re.search(r"\*Pending application\*|application pending|reserved", body, re.I))
        dogs.append(Dog(
            source="ARC Bishops Stortford",
            name=name,
            breed=breed,
            age=age,
            sex=sex,
            location="ARC (Bishops Stortford, Hertfordshire CM23)",
            url=href,
            in_range=True,
            reserved=reserved,
        ))
    return dogs


# =====================================================================
# RSPCA Solent Branch — Stubbington Ark (PO14 ~10mi)
# Branch's own WordPress site. Distinct from fetch_rspca_solent which uses
# the central rspca.org.uk findapet API. dedup_cross_source will collapse
# any overlap.
# =====================================================================

_RSPCA_SOLENT_BRANCH_BASE = "https://www.rspcasolentbranch.org.uk"

_RSPCA_SOLENT_BRANCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.google.com/",
}


def fetch_rspca_solent_branch() -> list[Dog]:
    list_url = f"{_RSPCA_SOLENT_BRANCH_BASE}/dogsforadoption/"
    try:
        r = requests.get(list_url, headers=_RSPCA_SOLENT_BRANCH_HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[RSPCA Solent Branch] {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"[RSPCA Solent Branch] HTTP {r.status_code}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    SKIP = {
        "", "dogsforadoption", "catsforadoption", "rabbitsforadoption",
        "about-us", "about", "contact", "donate", "volunteer",
        "adopt", "adoption", "fostering", "foster", "blog", "home",
        "shop", "news", "privacy-policy", "terms", "wp-content",
        "wp-includes", "wp-admin", "feed", "category", "tag", "page",
        "subscribe", "sitemap", "search",
    }
    dog_urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "rspcasolentbranch.org.uk" not in h and not h.startswith("/"):
            continue
        path = h.split("rspcasolentbranch.org.uk")[-1] if "rspcasolentbranch" in h else h
        path = path.strip("/").split("?")[0].split("#")[0]
        if not path or "/" in path or path.lower() in SKIP:
            continue
        full = h if h.startswith("http") else f"{_RSPCA_SOLENT_BRANCH_BASE}/{path}/"
        if full in seen:
            continue
        seen.add(full)
        dog_urls.append(full)

    def probe(url: str) -> Dog | None:
        try:
            rr = requests.get(url, headers=_RSPCA_SOLENT_BRANCH_HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if rr.status_code != 200:
            return None
        ss = BeautifulSoup(rr.text, "lxml")
        for t in ss(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        main = ss.find("main") or ss.body
        if not main:
            return None
        text = main.get_text(" ", strip=True)
        if re.search(r"\b(cat|kitten|rabbit|guinea pig)\b", text, re.I) and \
           not re.search(r"\b(dog|puppy|breed)\b", text, re.I):
            return None
        breed_m = re.search(
            r"Breed[:\s]+([A-Za-z][A-Za-z /x\-]+?)(?:\s{2,}|Ref\b|Age\b|Sex\b|Personality\b|Behaviour\b|$)",
            text, re.I,
        )
        breed = breed_m.group(1).strip() if breed_m else ""
        if not matches_breed(text) or is_breed_noise(breed):
            return None
        h1 = ss.find("h1")
        name_m = re.search(r"Name[:\s]+([A-Za-z][A-Za-z &-]+?)(?=\s+Age\b|\s{2,})", text, re.I)
        if name_m:
            name = name_m.group(1).strip()
        elif h1:
            name = h1.get_text(strip=True)
        else:
            name = url.rstrip("/").split("/")[-1].replace("-", " ").title()
        age_m = re.search(r"Age[:\s]+([^\n]+?)(?=\s+Sex\b|\s+Breed\b|\s{2,})", text, re.I)
        age = age_m.group(1).strip() if age_m else None
        sex_m = re.search(r"Sex[:\s]+(Male\s*&\s*Female|Male|Female)\b", text, re.I)
        sex = sex_m.group(1) if sex_m else None
        reserved = bool(re.search(r"\breserved\b", text, re.I))
        if "ashley heath" in text.lower():
            loc = "RSPCA Solent Branch via Ashley Heath (Ringwood BH24)"
        else:
            loc = "RSPCA Solent Branch (Stubbington Ark, Fareham PO14)"
        return Dog(
            source="RSPCA Solent Branch",
            name=name,
            breed=breed or "(see listing)",
            age=age,
            sex=sex,
            location=loc,
            url=url,
            in_range=True,
            reserved=reserved,
        )

    dogs: list[Dog] = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for d in pool.map(probe, dog_urls):
            if d:
                dogs.append(d)
    return dogs


# =====================================================================
# Cross-source dedup
# =====================================================================

_AGGREGATOR_SOURCES = {"Dogsblog", "Pets4Homes (Rescue)", "AgilityNet", "Oldies Club", "Lurcher Link"}
_GENERIC_LOCS = {"", "uk", "ukwide", "national", "nationwide", "england", "britain"}
_PLACEHOLDER_LOCS = {"locationnottagged", "notspecified", "nottagged", "tbc", "tba", "unknown"}
_BREED_TOKENS = ("border collie", "springer spaniel", "cocker spaniel",
                 "sprollie", "sprocker", "collie", "springer")


def _primary_breed_key(breed: str) -> str:
    b = (breed or "").lower()
    for tok in _BREED_TOKENS:
        if tok in b:
            return tok.split()[-1]
    return "other"


def _name_key(name: str) -> str:
    m = re.match(r"[a-z]+", (name or "").lower())
    return m.group(0) if m else ""


_REGION_EXPANSIONS: dict[str, set[str]] = {
    "wales": {"wales", "cymru", "welsh", "cardigan", "ceredigion", "pembrokeshire",
              "carmarthenshire", "gwynedd", "powys", "swansea", "glamorgan",
              "newport", "wrexham", "conwy", "denbighshire", "flintshire",
              "anglesey", "torfaen", "caerphilly", "monmouthshire", "bridgend",
              "rhondda", "merthyr", "llanelli", "neath"},
    "scotland": {"scotland", "scots", "aberdeen", "aberdeenshire", "edinburgh",
                 "glasgow", "highland", "fife", "lothian", "stirling", "dumfries",
                 "ayrshire", "perthshire", "galloway"},
    "ni": {"antrim", "armagh", "tyrone", "fermanagh", "belfast", "derry",
           "londonderry"},
}


def _loc_tokens(loc: str | None) -> set[str]:
    if not loc:
        return set()
    toks = set(re.findall(r"[a-z]{3,}", loc.lower()))
    # Expand with sub-UK region tags so 'Cardigan, Ceredigion' and 'West Wales'
    # both carry a shared 'wales' token for dedup location-compatibility checks.
    for region, members in _REGION_EXPANSIONS.items():
        if toks & members:
            toks.add(region)
    return toks


def _loc_is_generic(loc: str | None) -> bool:
    if not loc:
        return True
    normalized = re.sub(r"[^a-z]", "", loc.lower())
    return normalized in _GENERIC_LOCS or normalized in _PLACEHOLDER_LOCS


def _locations_compatible(a: str | None, b: str | None) -> bool:
    if _loc_is_generic(a) or _loc_is_generic(b):
        return True
    ta, tb = _loc_tokens(a), _loc_tokens(b)
    if not ta or not tb:
        return True
    return bool(ta & tb)


def dedup_cross_source(dogs: list[Dog]) -> tuple[list[Dog], list[Dog]]:
    """Collapse cross-source duplicates (same dog listed by an aggregator
    AND by the original rescue, or by two aggregators).

    Groups by (first-name-token, primary-breed-token); within each group,
    partitions by location compatibility (shared token, or one side is
    generic like 'UK-wide' / 'Location not tagged').

    Collapse only fires when at least one dog in a cluster is from a
    known aggregator — protects against hiding two genuinely distinct
    dogs that happen to share name + breed + town.

    Returns (kept, dropped). Both lists preserve insertion order.
    """
    buckets: dict[tuple[str, str], list[Dog]] = {}
    for d in dogs:
        nk = _name_key(d.name)
        if not nk:
            buckets.setdefault(("__UNNAMED__", d.url), []).append(d)
            continue
        buckets.setdefault((nk, _primary_breed_key(d.breed)), []).append(d)

    kept: list[Dog] = []
    dropped: list[Dog] = []
    for key, group in buckets.items():
        if key[0] == "__UNNAMED__" or len(group) == 1:
            kept.extend(group)
            continue
        clusters: list[list[Dog]] = []
        for d in group:
            placed = False
            for cl in clusters:
                if all(_locations_compatible(d.location, x.location) for x in cl):
                    cl.append(d)
                    placed = True
                    break
            if not placed:
                clusters.append([d])

        for cl in clusters:
            if len(cl) == 1:
                kept.append(cl[0])
                continue
            has_aggregator = any(d.source in _AGGREGATOR_SOURCES for d in cl)
            if not has_aggregator:
                kept.extend(cl)
                continue
            t1 = [d for d in cl if d.source not in _AGGREGATOR_SOURCES]
            t2 = [d for d in cl if d.source in _AGGREGATOR_SOURCES]
            if t1:
                kept.extend(t1)
                dropped.extend(t2)
            else:
                kept.append(t2[0])
                dropped.extend(t2[1:])
    return kept, dropped


# =====================================================================
# Orchestration
# =====================================================================

ADAPTERS: list[tuple[str, Callable[[], list[Dog]]]] = [
    ("Dogsblog", fetch_dogsblog),
    ("Battersea", fetch_battersea),
    ("Dogs Trust", fetch_dogs_trust),
    ("Blue Cross", fetch_blue_cross),
    ("NAWT", fetch_nawt),
    ("Wiccaweys", fetch_wiccaweys),
    ("ESSW", fetch_essw),
    ("Spaniel Aid", fetch_spaniel_aid),
    ("Oldies Club", fetch_oldies_club),
    ("CAESSR", fetch_caessr),
    ("Last Chance", fetch_last_chance),
    ("Holbrook", fetch_holbrook),
    ("Helping Hounds", fetch_helping_hounds),
    # --- Beta additions ---
    ("Border Collie Spot", fetch_bc_spot),
    ("FOSTBC", fetch_fostbc),
    ("PPBC", fetch_ppbc),
    ("Sprocker Assist", fetch_sprocker_assist),
    ("Ferne", fetch_ferne),
    ("Margaret Green", fetch_margaret_green),
    ("RRAUK", fetch_rrauk),
    ("Pawprints to Freedom", fetch_pawprints),
    ("Epsom Canine", fetch_epsom_canine),
    # --- Round 2: Tier 1 (≤30mi) ---
    ("Phoenix Rehoming", fetch_phoenix_rehoming),
    ("Fareham/Gosport Ocella", fetch_fareham_gosport_ocella),
    ("RSPCA Solent", fetch_rspca_solent),
    ("RSPCA Sussex West", fetch_rspca_sussex_west),
    ("RSPCA Isle of Wight", fetch_rspca_isle_of_wight),
    ("St Francis", fetch_st_francis),
    ("Second Chance AR", fetch_second_chance),
    ("Clymping", fetch_clymping),
    ("Wadars", fetch_wadars),
    ("Arundawn", fetch_arundawn),
    # --- Round 2: Tier 2 (30–70mi) ---
    ("Pro Dogs Direct", fetch_pro_dogs_direct),
    ("FurBuddies", fetch_furbuddies),
    ("Dogs N Homes", fetch_dogs_n_homes),
    ("Waggy Tails", fetch_waggy_tails),
    ("Chilterns Dog Rescue", fetch_chilterns),
    # --- Round 2: Tier 3 (70–100mi edge) ---
    ("Heathlands Animal Sanctuary", fetch_heathlands),
    ("Mutts in Distress", fetch_mutts_in_distress),
    ("Help 4 Hounds", fetch_help4hounds),
    ("Oxfordshire Animal Sanctuary", fetch_oxfordshire_as),
    ("Woodgreen Pets Charity", fetch_woodgreen),
    ("Teckels Animal Sanctuary", fetch_teckels),
    # --- Round 2: Platform aggregators ---
    ("Pets4Homes (Rescue)", fetch_pets4homes_adoption),
    # --- Round 2: Breed specialists ---
    ("SYESSR", fetch_syessr),
    ("NESSR", fetch_nessr),
    ("AgilityNet", fetch_agilitynet),
    # --- Round 3: UK-wide breed specialists + reclaimed rescues ---
    ("UK Spaniel Rescue", fetch_uk_spaniel_rescue),
    ("Spaniel Rescue Foundation", fetch_spaniel_rescue_foundation),
    ("Collie Rescue R&S", fetch_collie_rescue_rs),
    ("Save Our Spaniels", fetch_save_our_spaniels),
    ("Spaniel Assist", fetch_spaniel_assist),
    ("Helping Dogs and Cats UK", fetch_helping_dogs_cats_uk),
    ("Lurcher SOS", fetch_lurcher_sos),
    ("Forever Hounds Trust", fetch_forever_hounds),
    ("NWESSR", fetch_nwessr),
    ("Animal Rescue Cymru", fetch_animal_rescue_cymru),
    ("Many Tears", fetch_many_tears),
    ("Lurcher Link", fetch_lurcher_link),
    # --- Round 4: additional rescue adapters ---
    ("Foal Farm", fetch_foal_farm),
    ("DBARC", fetch_dbarc),
    ("Cinque Ports Rescue", fetch_cinque_ports),
    ("AllDogsMatter", fetch_alldogsmatter),
    ("Mayhew", fetch_mayhew),
    ("Greyhound and Lurcher Rescue", fetch_greyhound_lurcher_rescue),
    ("EGLR", fetch_eglr),
    ("Mikey's Dog Rescue", fetch_mikeys_dog_rescue),
    # --- Round 5: Hampshire/Sussex local + foster-network rescues ---
    ("Dali Dog Rescue", fetch_dali),
    ("MossMania", fetch_mossmania),
    ("Hampshire Paws", fetch_hampshire_paws),
    ("STAR Cyprus", fetch_star_cyprus),
    ("A New Leash For Life", fetch_anewleash),
    ("Hairy Hounz", fetch_hairy_hounz),
    # --- Round 6: in-range additions (research 2026-04-30) ---
    ("ARC Bishops Stortford", fetch_arc_bishops_stortford),
    ("RSPCA Solent Branch", fetch_rspca_solent_branch),
]


def scan_iter():
    """Yield (source_name, done_count, total_count, dogs_so_far) after each
    adapter completes, then a final (None, total, total, sorted_unique_dogs)."""
    all_dogs: list[Dog] = []
    total = len(ADAPTERS)
    done = 0
    with futures.ThreadPoolExecutor(max_workers=min(total, 20)) as pool:
        fut_to_name = {pool.submit(fn): name for name, fn in ADAPTERS}
        for fut in futures.as_completed(fut_to_name):
            name = fut_to_name[fut]
            try:
                results = fut.result()
                print(f"[{name}] {len(results)} match(es)", file=sys.stderr)
                all_dogs.extend(results)
            except Exception as e:
                print(f"[{name}] FAILED: {e}", file=sys.stderr)
            done += 1
            yield name, done, total, all_dogs

    seen: set[str] = set()
    unique: list[Dog] = []
    for d in all_dogs:
        if is_breed_noise(d.breed):
            print(f"[noise] dropped {d.source}:{d.name} "
                  f"(breed={d.breed!r})", file=sys.stderr)
            continue
        key = d.url
        if key in seen:
            continue
        seen.add(key)
        unique.append(d)

    unique, cross_dropped = dedup_cross_source(unique)
    for d in cross_dropped:
        kept_match = next(
            (k for k in unique
             if _name_key(k.name) == _name_key(d.name)
             and _primary_breed_key(k.breed) == _primary_breed_key(d.breed)
             and _locations_compatible(k.location, d.location)),
            None,
        )
        if kept_match:
            print(f"[dedup] dropped {d.source}:{d.url} "
                  f"(duplicate of {kept_match.source}:{kept_match.url})",
                  file=sys.stderr)

    def _sort_key(d: Dog) -> tuple:
        dist = d.distance_miles if d.distance_miles is not None else 9999.0
        return (not d.in_range, d.reserved, dist, d.source, d.name.lower())
    unique.sort(key=_sort_key)
    yield None, total, total, unique


def scan() -> list[Dog]:
    for name, _done, _total, dogs in scan_iter():
        if name is None:
            return dogs
    return []


def print_dogs(dogs: list[Dog]) -> None:
    if not dogs:
        print("No matching dogs found.")
        return
    for d in dogs:
        bits = [d.breed]
        if d.age:
            bits.append(d.age)
        if d.sex:
            bits.append(d.sex)
        if d.location:
            bits.append(d.location)
        flags = []
        if d.reserved:
            flags.append("RESERVED")
        if not d.in_range:
            flags.append("range uncertain")
        suffix = f"  ({' · '.join(flags)})" if flags else ""
        print(f"• {d.name}  [{d.source}]{suffix}")
        print(f"    {' · '.join(bits)}")
        print(f"    {d.url}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan UK rescues for Border Collie / Springer Spaniel / crosses near PO11")
    parser.add_argument("--json", action="store_true", help="output JSON")
    args = parser.parse_args()
    dogs = scan()
    if args.json:
        print(json.dumps([asdict(d) for d in dogs], indent=2))
    else:
        print(f"\n=== {len(dogs)} matching dog(s) ===\n")
        print_dogs(dogs)


if __name__ == "__main__":
    main()
