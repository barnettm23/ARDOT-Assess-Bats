"""Stage 4 -- bridges.

Download the Arkansas file of the FHWA National Bridge Inventory (NBI) and
flatten it into data/bridges.csv (+ a per-county summary). Every public-road
bridge over 20 ft in the state is in it, with coordinates, so this is a
download-and-clean job rather than a scrape.

    python bridges.py            # fetch (cached) + parse, all ~12,700 rows
    python bridges.py 100        # parse only the first 100 rows -- use first
    python bridges.py --file X   # parse a local NBI delimited file (offline)

Environment:
    NBI_YEAR   four-digit FHWA release year (default 2024)
    NBI_URL    override the download URL entirely

Why this exists: "Str. & Apprs." (structure and approaches) jobs are the bulk
of the bat-triggering ARDOT projects in records.csv, and bridges over water are
gray-bat roosts. NBI gives every one of them a coordinate, a condition, and --
in items 75A/97 -- ARDOT's own statement of what it plans to do and when. Poor
bridges are the replacement pipeline: the forward-looking project-years.

Format notes (FHWA Recording and Coding Guide, 1995 ed., as revised):
  * The delimited files (2011+) carry a header row whose names end in the NBI
    item number, e.g. LAT_016, YEAR_BUILT_027, DECK_COND_058. Columns are
    resolved by that trailing item number, not by the full name, so a header
    rename does not break the parser.
  * Item 16 latitude is DDMMSS.SS packed as 8 digits; item 17 longitude is
    DDDMMSS.SS packed as 9 digits and is POSITIVE for the western hemisphere.
  * Condition (Good/Fair/Poor) is FHWA's official schema: the lowest of items
    58/59/60 (or 62 for culverts) -- 7-9 Good, 5-6 Fair, 0-4 Poor. "Poor" is
    the term that replaced "structurally deficient" in 2018.
  * 2025+ submittals move to the SNBI specification with different item codes.
    This parser targets the legacy format; if a newer file parses to nothing,
    that is why. Pin NBI_YEAR=2024 until an SNBI mapping is written.
"""

import csv
import io
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "cache" / "nbi"
OUT_CSV = ROOT / "data" / "bridges.csv"
OUT_SUMMARY = ROOT / "data" / "bridges_summary.csv"
USER_AGENT = "OzarkBioacoustics-research/0.1 (contact: michael@seismicagency.com)"

NBI_YEAR = int(os.environ.get("NBI_YEAR", "2024"))
NBI_URL = os.environ.get(
    "NBI_URL",
    f"https://www.fhwa.dot.gov/bridge/nbi/{NBI_YEAR}/delimited/AR{NBI_YEAR % 100:02d}.txt",
)

# Arkansas bounding box, generous. Anything outside is a placeholder or a typo.
LAT_RANGE = (32.9, 36.6)
LON_RANGE = (-94.7, -89.5)

# 75 counties, FIPS = 2*i - 1 in this order (FIPS sorts "St. Francis" as Saint,
# ahead of Saline). Also the closed set RE_COUNTY in parse.py should validate against.
AR_COUNTIES = [
    "Arkansas", "Ashley", "Baxter", "Benton", "Boone", "Bradley", "Calhoun",
    "Carroll", "Chicot", "Clark", "Clay", "Cleburne", "Cleveland", "Columbia",
    "Conway", "Craighead", "Crawford", "Crittenden", "Cross", "Dallas", "Desha",
    "Drew", "Faulkner", "Franklin", "Fulton", "Garland", "Grant", "Greene",
    "Hempstead", "Hot Spring", "Howard", "Independence", "Izard", "Jackson",
    "Jefferson", "Johnson", "Lafayette", "Lawrence", "Lee", "Lincoln",
    "Little River", "Logan", "Lonoke", "Madison", "Marion", "Miller",
    "Mississippi", "Monroe", "Montgomery", "Nevada", "Newton", "Ouachita",
    "Perry", "Phillips", "Pike", "Poinsett", "Polk", "Pope", "Prairie",
    "Pulaski", "Randolph", "St. Francis", "Saline", "Scott", "Searcy",
    "Sebastian", "Sevier", "Sharp", "Stone", "Union", "Van Buren",
    "Washington", "White", "Woodruff", "Yell",
]
assert len(AR_COUNTIES) == 75
COUNTY_BY_FIPS = {2 * i + 1: name for i, name in enumerate(AR_COUNTIES)}

# Item 22 owner codes (subset that occurs in Arkansas).
OWNER = {
    1: "State Highway Agency", 2: "County Highway Agency", 3: "Town or City",
    4: "City or Municipal Highway Agency", 11: "State Park/Forest/Reservation",
    12: "Local Park/Forest/Reservation", 21: "Other State Agency",
    25: "Other Local Agency", 26: "Private (other than railroad)",
    27: "Railroad", 31: "State Toll Authority", 32: "Local Toll Authority",
    60: "Other Federal", 62: "Bureau of Indian Affairs", 64: "USFS",
    66: "NPS", 68: "BLM", 69: "Bureau of Reclamation", 70: "USACE",
    80: "Unknown",
}

# Item 43A main-structure material.
MATERIAL = {
    1: "Concrete", 2: "Concrete continuous", 3: "Steel", 4: "Steel continuous",
    5: "Prestressed concrete", 6: "Prestressed concrete continuous",
    7: "Wood/timber", 8: "Masonry", 9: "Aluminum/wrought iron/cast iron",
    0: "Other",
}

# Item 42B service under the structure. Anything with a waterway is roost habitat.
SERVICE_UNDER = {
    1: "Highway", 2: "Railroad", 3: "Pedestrian/bicycle", 4: "Highway-railroad",
    5: "Waterway", 6: "Highway-waterway", 7: "Railroad-waterway",
    8: "Highway-waterway-railroad", 9: "Relief for waterway", 0: "Other",
}
OVER_WATER = {5, 6, 7, 8, 9}

# Item 75A work proposed. Presence of any code means the owner has a project in mind.
WORK_PROPOSED = {
    31: "Replacement because of substandard load capacity or structural condition",
    32: "Replacement because of substandard geometry",
    33: "Replacement because of relocation of road",
    34: "Widening without deck rehabilitation",
    35: "Widening with deck rehabilitation",
    36: "Deck rehabilitation",
    37: "Deck replacement",
    38: "Other structural work, including hydraulic replacements",
}
REPLACEMENT_CODES = {31, 32, 33}

# Official FHWA condition schema, plus a color for the map.
CONDITION_COLOR = {"Good": "#2e7d32", "Fair": "#f9a825", "Poor": "#c62828", "": "#9e9e9e"}

ITEM_RE = re.compile(r"_(\d{3}[A-C]?)$")


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------
def fetch(url: str = NBI_URL) -> Path:
    """Download the state file once; the cache is the source of truth after that."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / url.rsplit("/", 1)[-1]
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    print(f"fetching {url}")
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=300)
    resp.raise_for_status()
    text = resp.text
    if "STRUCTURE_NUMBER" not in text[:4000].upper():
        raise SystemExit(
            "ABORT: response has no NBI header row. Either the URL pattern changed "
            "or this release is in SNBI format (see module docstring)."
        )
    dest.write_text(text, encoding="utf-8")
    time.sleep(1.0)
    return dest


# --------------------------------------------------------------------------
# parse helpers
# --------------------------------------------------------------------------
def item_map(fieldnames: list[str]) -> dict[str, str]:
    """Map NBI item number ('016', '043A', ...) -> actual column name."""
    out = {}
    for name in fieldnames or []:
        if m := ITEM_RE.search(name.strip()):
            out[m.group(1)] = name
    # Derived fields FHWA adds without item numbers.
    for name in fieldnames or []:
        if name.strip().upper() in {"BRIDGE_CONDITION", "LOWEST_RATING", "DECK_AREA"}:
            out[name.strip().upper()] = name
    return out


def to_int(s: str | None) -> int | None:
    s = (s or "").strip()
    if not s or not re.fullmatch(r"-?\d+", s):
        return None
    return int(s)


def to_float(s: str | None) -> float | None:
    s = (s or "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def dms_packed(raw: str | None, deg_digits: int) -> float | None:
    """Decode NBI packed DMS: D{deg_digits}MMSSSS with two implied decimals on
    seconds. Zero or malformed values mean 'not recorded'."""
    s = re.sub(r"\D", "", raw or "")
    if not s or int(s) == 0:
        return None
    s = s.zfill(deg_digits + 6)
    if len(s) != deg_digits + 6:
        return None
    deg = int(s[:deg_digits])
    mins = int(s[deg_digits : deg_digits + 2])
    secs = int(s[deg_digits + 2 :]) / 100.0
    if mins >= 60 or secs >= 60:
        return None
    return deg + mins / 60 + secs / 3600


def rating(s: str | None) -> int | None:
    """NBI condition ratings are 0-9, 'N' = not applicable."""
    s = (s or "").strip().upper()
    return int(s) if s.isdigit() else None


def condition(deck, sup, sub, culv) -> tuple[str, int | None]:
    """FHWA Good/Fair/Poor from the lowest applicable component rating."""
    parts = [r for r in (deck, sup, sub, culv) if r is not None]
    if not parts:
        return "", None
    low = min(parts)
    if low >= 7:
        return "Good", low
    if low >= 5:
        return "Fair", low
    return "Poor", low


# --------------------------------------------------------------------------
# parse
# --------------------------------------------------------------------------
FIELDS = [
    "structure_number", "county", "county_fips", "owner_code", "owner",
    "facility_carried", "feature_intersected", "location",
    "route_prefix", "route_number", "functional_class",
    "lat", "lon", "coord_note",
    "year_built", "year_reconstructed", "age_yrs",
    "material", "structure_type_code", "service_under", "over_water",
    "n_main_spans", "structure_len_m", "deck_width_m", "adt", "adt_year",
    "deck_cond", "superstructure_cond", "substructure_cond", "culvert_cond",
    "channel_cond", "waterway_adequacy", "scour_critical",
    "condition", "lowest_rating", "fhwa_condition", "condition_color",
    "work_proposed_code", "work_proposed", "replacement_proposed",
    "year_of_improvement", "improvement_cost_k",
    "inspection_date", "nbi_year", "parse_notes",
]


def parse_row(r: dict, col: dict[str, str], nbi_year: int) -> dict:
    g = lambda item: (r.get(col.get(item, ""), "") or "").strip()  # noqa: E731
    notes = []

    fips = to_int(g("003"))
    county = COUNTY_BY_FIPS.get(fips or -1, "")
    if not county:
        notes.append(f"unknown-county-fips:{g('003')}")

    lat = dms_packed(g("016"), 2)
    lon = dms_packed(g("017"), 3)
    if lon is not None:
        lon = -abs(lon)  # NBI stores western longitude as positive
    coord_note = ""
    if lat is None or lon is None:
        coord_note = "missing"
    elif not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
        coord_note = "out-of-state"
    if coord_note:
        notes.append(f"coord-{coord_note}")

    deck, sup, sub, culv = (rating(g(i)) for i in ("058", "059", "060", "062"))
    cond, low = condition(deck, sup, sub, culv)
    fhwa_cond = g("BRIDGE_CONDITION").upper()[:1]
    fhwa_cond = {"G": "Good", "F": "Fair", "P": "Poor"}.get(fhwa_cond, "")
    if fhwa_cond and cond and fhwa_cond != cond:
        notes.append(f"condition-disagrees:computed={cond},fhwa={fhwa_cond}")
    if not cond:
        notes.append("no-condition-rating")

    owner_code = to_int(g("022"))
    material_code = to_int(g("043A"))
    service_under = to_int(g("042B"))
    work_code = to_int(g("075A"))
    year_built = to_int(g("027"))
    year_recon = to_int(g("106"))
    if year_recon == 0:
        year_recon = None

    return {
        "structure_number": g("008"),
        "county": county,
        "county_fips": f"{fips:03d}" if fips is not None else "",
        "owner_code": owner_code if owner_code is not None else "",
        "owner": OWNER.get(owner_code, "") if owner_code is not None else "",
        "facility_carried": g("007").strip("'\" "),
        "feature_intersected": g("006A").strip("'\" "),
        "location": g("009").strip("'\" "),
        "route_prefix": g("005B"),
        "route_number": g("005D").lstrip("0"),
        "functional_class": g("026"),
        "lat": f"{lat:.6f}" if lat is not None else "",
        "lon": f"{lon:.6f}" if lon is not None else "",
        "coord_note": coord_note,
        "year_built": year_built if year_built else "",
        "year_reconstructed": year_recon if year_recon else "",
        "age_yrs": (nbi_year - year_built) if year_built else "",
        "material": MATERIAL.get(material_code, "") if material_code is not None else "",
        "structure_type_code": g("043B"),
        "service_under": SERVICE_UNDER.get(service_under, "") if service_under is not None else "",
        "over_water": int(service_under in OVER_WATER) if service_under is not None else "",
        "n_main_spans": to_int(g("045")) if g("045") else "",
        "structure_len_m": to_float(g("049")) if g("049") else "",
        "deck_width_m": to_float(g("052")) if g("052") else "",
        "adt": to_int(g("029")) if g("029") else "",
        "adt_year": g("030"),
        "deck_cond": deck if deck is not None else "",
        "superstructure_cond": sup if sup is not None else "",
        "substructure_cond": sub if sub is not None else "",
        "culvert_cond": culv if culv is not None else "",
        "channel_cond": g("061"),
        "waterway_adequacy": g("071"),
        "scour_critical": g("113"),
        "condition": cond,
        "lowest_rating": low if low is not None else "",
        "fhwa_condition": fhwa_cond,
        "condition_color": CONDITION_COLOR[cond],
        "work_proposed_code": work_code if work_code else "",
        "work_proposed": WORK_PROPOSED.get(work_code, "") if work_code else "",
        "replacement_proposed": int(work_code in REPLACEMENT_CODES) if work_code else 0,
        "year_of_improvement": to_int(g("097")) or "",
        "improvement_cost_k": to_int(g("096")) or "",
        "inspection_date": g("090"),
        "nbi_year": nbi_year,
        "parse_notes": ";".join(notes),
    }


def parse_file(path: Path, limit: int | None = None, nbi_year: int = NBI_YEAR) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    col = item_map(reader.fieldnames or [])
    required = {"008", "003", "016", "017", "058", "059", "060"}
    missing = required - set(col)
    if missing:
        raise SystemExit(
            f"ABORT: header lacks NBI items {sorted(missing)}. Got {len(col)} items. "
            "Not a legacy-format NBI delimited file (SNBI release?)."
        )
    rows = []
    for i, r in enumerate(reader):
        if limit and i >= limit:
            break
        rows.append(parse_row(r, col, nbi_year))
    return rows


def summarize(rows: list[dict]) -> list[dict]:
    """Per-county pipeline: condition counts, poor-over-water, and owner-declared work."""
    by_county = defaultdict(list)
    for r in rows:
        by_county[r["county"] or "UNKNOWN"].append(r)
    out = []
    for county in sorted(by_county):
        rs = by_county[county]
        c = Counter(r["condition"] for r in rs)
        out.append(
            {
                "county": county,
                "bridges": len(rs),
                "good": c["Good"],
                "fair": c["Fair"],
                "poor": c["Poor"],
                "unrated": c[""],
                "poor_pct": round(100 * c["Poor"] / len(rs), 1),
                "over_water": sum(1 for r in rs if r["over_water"] == 1),
                "poor_over_water": sum(1 for r in rs if r["over_water"] == 1 and r["condition"] == "Poor"),
                "state_owned": sum(1 for r in rs if r["owner_code"] == 1),
                "work_proposed": sum(1 for r in rs if r["work_proposed_code"]),
                "replacement_proposed": sum(1 for r in rs if r["replacement_proposed"] == 1),
                "state_owned_poor": sum(1 for r in rs if r["owner_code"] == 1 and r["condition"] == "Poor"),
                "median_age_yrs": _median([r["age_yrs"] for r in rs if r["age_yrs"] != ""]),
            }
        )
    return out


def _median(xs: list) -> str | int:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else ""


def write(rows: list[dict], summary: list[dict]) -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["county"], r["structure_number"])))
    with OUT_SUMMARY.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary[0].keys()) if summary else ["county"])
        w.writeheader()
        w.writerows(summary)


def report(rows: list[dict]) -> None:
    c = Counter(r["condition"] for r in rows)
    coords = Counter(r["coord_note"] for r in rows)
    print(
        f"bridges={len(rows)} good={c['Good']} fair={c['Fair']} poor={c['Poor']} "
        f"unrated={c['']} | coords ok={coords['']} missing={coords['missing']} "
        f"out-of-state={coords['out-of-state']}"
    )
    print(
        f"pipeline: poor={c['Poor']} poor-over-water="
        f"{sum(1 for r in rows if r['condition'] == 'Poor' and r['over_water'] == 1)} "
        f"work-proposed={sum(1 for r in rows if r['work_proposed_code'])} "
        f"replacement-proposed={sum(1 for r in rows if r['replacement_proposed'] == 1)} "
        f"state-owned-poor={sum(1 for r in rows if r['owner_code'] == 1 and r['condition'] == 'Poor')}"
    )
    disagree = sum(1 for r in rows if "condition-disagrees" in r["parse_notes"])
    if disagree:
        print(f"WARNING: {disagree} rows where computed condition != FHWA BRIDGE_CONDITION")


def main(argv: list[str]) -> None:
    limit, path = None, None
    args = list(argv)
    if "--file" in args:
        i = args.index("--file")
        path = Path(args[i + 1])
        del args[i : i + 2]
    if args:
        limit = int(args[0])
    if path is None:
        path = fetch()
    rows = parse_file(path, limit=limit)
    if not rows:
        raise SystemExit("ABORT: zero rows parsed.")
    summary = summarize(rows)
    write(rows, summary)
    report(rows)
    print(f"wrote {OUT_CSV.relative_to(ROOT)} and {OUT_SUMMARY.relative_to(ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1:])
