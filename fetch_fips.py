"""Build data/ar_county_fips.csv from the Census county-code file.

Fetched, not hardcoded. FIPS codes are not guessable from an alphabetical walk
-- "St. Francis" does not sort where you would expect it to -- and a wrong code
silently joins a project to the wrong county in whatever GIS consumes it. For a
dataset going to an outside researcher that is the kind of error nobody catches
downstream, so the codes come from Census or they do not come at all.

Run before parse.py. Idempotent: skips the fetch if the file already exists.
"""

import csv
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "ar_county_fips.csv"
USER_AGENT = "OzarkBioacoustics-research/0.1 (contact: michael@seismicagency.com)"

# Census national county file. Layout: STATE,STATEFP,COUNTYFP,COUNTYNAME,CLASSFP
SOURCES = [
    "https://www2.census.gov/geo/docs/reference/codes2020/national_county2020.txt",
    "https://www2.census.gov/geo/docs/reference/codes/files/national_county.txt",
]
AR_STATE_FP = "05"
EXPECTED_COUNTIES = 75  # Arkansas has 75; anything else means we parsed the wrong thing


def parse(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 4:
            continue
        state, statefp, countyfp, name = parts[0], parts[1], parts[2], parts[3]
        if statefp != AR_STATE_FP or state.upper() != "AR":
            continue
        clean = name.strip()
        for suffix in (" County", " Parish"):
            if clean.endswith(suffix):
                clean = clean[: -len(suffix)]
        rows.append(
            {
                "county": clean,
                "county_fips": f"{statefp}{countyfp.zfill(3)}",
                "state_fips": statefp,
            }
        )
    return rows


def main() -> None:
    if OUT.exists():
        with OUT.open(newline="", encoding="utf-8") as fh:
            n = sum(1 for _ in csv.DictReader(fh))
        print(f"{OUT.name} already present with {n} counties -- skipping fetch")
        return

    for url in SOURCES:
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
        except requests.RequestException as exc:
            print(f"  {url} -> {type(exc).__name__}", file=sys.stderr)
            continue
        if resp.status_code != 200:
            print(f"  {url} -> HTTP {resp.status_code}", file=sys.stderr)
            continue
        rows = parse(resp.text)
        if len(rows) != EXPECTED_COUNTIES:
            # Wrong count means the layout changed or we hit the wrong file.
            # Writing it anyway would put bad codes in front of a researcher.
            print(
                f"  {url} -> parsed {len(rows)} Arkansas counties, expected "
                f"{EXPECTED_COUNTIES}; refusing to write",
                file=sys.stderr,
            )
            continue
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with OUT.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["county", "county_fips", "state_fips"])
            w.writeheader()
            w.writerows(sorted(rows, key=lambda r: r["county"]))
        print(f"wrote {OUT.name}: {len(rows)} Arkansas counties from {url}")
        return

    # Fail soft. parse.py leaves county_fips blank and flags it, which is a
    # visible gap; inventing codes would be an invisible error.
    print(
        "could not obtain county FIPS from any source -- county_fips will be blank",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
