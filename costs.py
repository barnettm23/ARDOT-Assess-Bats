"""Stage 5 -- costs.

Estimate, for every bridge in data/bridges.csv:

  * `est_rebuild_today_usd`  -- what it would cost to rebuild the structure now
  * `est_original_cost_usd`  -- what that build plausibly cost in the year it
                                was actually built, in THAT year's dollars

and write both, adjacent, into data/bridges_costed.csv.

    python costs.py                      # read data/bridges.csv, write costed file
    python costs.py --in X --out Y       # explicit paths (tests)
    python costs.py --index-file F       # swap the deflator

## These are estimates. The NBI has no original-cost field.

Confirmed against the live 2024 Arkansas file: item 96 is populated on 2,058
rows, of which 2,050 also carry a proposed-work code. It is the cost of work
PROPOSED, not the cost of the build. A bridge built in 1934 carries $500k in
that column -- a modern replacement estimate, not a Depression-era contract.
So original cost cannot be read from this data. It can only be modelled, and
this module models it in two steps.

### Step 1 -- today's rebuild cost, calibrated from the file itself

No external cost assumption is imported. The rate comes from ARDOT's own
replacement estimates for its own bridges: the ~1,480 structures that carry
both a declared replacement (item 75A in 31/32/33) and a cost estimate (item
96), divided by deck area (item 49 x item 52).

Cost per square foot falls steeply with size -- fixed costs spread over more
deck. Measured on the live file:

      deck sq ft        median $/sq ft
      under 1,000                 361
      1,000-2,500                 202
      2,500-5,000                 151
      5,000-10,000                114
      10,000-25,000                88
      25,000+                      70

A power-law fit reproduces the middle of that range but underestimates the
largest bridges by about 23% ($54 fitted against $70 observed), so this module
does NOT fit a curve. It interpolates log-linearly between the observed medians
at each bin's median deck area, and clamps outside the end bins. That matches
the observed data by construction and makes every rate traceable to a bin.

Because size drives the rate, material is deliberately NOT used: once deck area
is controlled the material signal is weak and confounded (prestressed concrete
shows a low $/sq ft mainly because it is used on large spans).

### Step 2 -- deflate to the year built

    est_original = est_rebuild_today * (index[year_built] / index[today])

using data/cost_index.csv, an Engineering News-Record Construction Cost Index
table (1913 = 100), interpolated geometrically between anchor years.

## Read this before quoting any number

  * **The index values in data/cost_index.csv are unverified.** They were
    written from recollection of the ENR CCI series, not transcribed from an
    ENR publication, and the 2025-2026 rows are extrapolated outright. They set
    the entire scale of `est_original_cost_usd`. Replace the file with a
    primary-source series before any of these figures leave the repo. The
    script prints a warning on every run for this reason.
  * **Spread is wide.** Actual-over-predicted on the calibration set runs 0.73
    at the 10th percentile to 1.63 at the 90th. Treat any single row as an
    order-of-magnitude figure; the column is meaningful in aggregate.
  * **Deck area ignores what actually drives bridge cost** -- foundation depth,
    span arrangement, stream crossing conditions, traffic staging.
  * **Item 96 is total project cost**, including roadway approach work, so the
    calibration rate is a project rate and slightly above a pure structure rate.
  * **A reconstructed bridge is dated by its ORIGINAL build.** `year_built`
    drives the deflation even where `year_reconstructed` is set, so the original
    estimate for those rows describes the first build, not the rebuild.
"""

import argparse
import csv
import math
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IN_CSV = ROOT / "data" / "bridges.csv"
OUT_CSV = ROOT / "data" / "bridges_costed.csv"
INDEX_CSV = ROOT / "data" / "cost_index.csv"

SQM_TO_SQFT = 10.7639
TODAY_YEAR = 2026

# Deck-area bins for the calibration. Upper bound exclusive.
BINS = [(0, 1000), (1000, 2500), (2500, 5000), (5000, 10000), (10000, 25000), (25000, 10**9)]

# A calibration bin must hold at least this many structures to be trusted.
MIN_BIN_N = 20

NEW_FIELDS = [
    "deck_area_sqft",
    "est_original_cost_usd",   # before: year-built dollars
    "est_rebuild_today_usd",   # after:  today's dollars
    "cost_ratio_today_to_original",
    "rate_today_per_sqft",
    "cost_index_built",
    "cost_index_today",
    "cost_basis_note",
]


# --------------------------------------------------------------------------
# cost index
# --------------------------------------------------------------------------
def load_index(path: Path) -> tuple[dict[int, float], set[int]]:
    """Return {year: index} over the full covered span, plus the projected years.

    Anchors are interpolated geometrically -- a cost index compounds, so a
    straight line between anchors understates the middle years.
    """
    anchors, projected = {}, set()
    with path.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            y, v = int(r["year"]), float(r["index"])
            anchors[y] = v
            if (r.get("kind") or "").strip() == "projected":
                projected.add(y)
    if len(anchors) < 2:
        raise SystemExit(f"ABORT: {path} needs at least two anchor years.")

    years = sorted(anchors)
    out = {}
    for a, b in zip(years, years[1:]):
        va, vb = anchors[a], anchors[b]
        for y in range(a, b + 1):
            # geometric interpolation
            out[y] = va * (vb / va) ** ((y - a) / (b - a)) if b > a else va
    out[years[-1]] = anchors[years[-1]]
    return out, projected


# --------------------------------------------------------------------------
# rate calibration
# --------------------------------------------------------------------------
def deck_area_sqft(row: dict) -> float | None:
    try:
        L = float(row.get("structure_len_m") or 0)
        W = float(row.get("deck_width_m") or 0)
    except ValueError:
        return None
    a = L * W * SQM_TO_SQFT
    return a if a > 50 else None


def calibrate(rows: list[dict]) -> list[tuple[float, float]]:
    """Build (median deck area, median $/sq ft) knots from the NBI's own
    replacement estimates. Returns knots sorted by area."""
    pts = []
    for r in rows:
        if r.get("replacement_proposed") != "1":
            continue
        if not r.get("improvement_cost_k"):
            continue
        a = deck_area_sqft(r)
        if a is None:
            continue
        cost = int(r["improvement_cost_k"]) * 1000
        if cost <= 1000:
            continue
        pts.append((a, cost))

    knots = []
    for lo, hi in BINS:
        g = [(a, c) for a, c in pts if lo <= a < hi]
        if len(g) < MIN_BIN_N:
            continue
        knots.append(
            (
                statistics.median(a for a, _ in g),
                statistics.median(c / a for a, c in g),
                len(g),
            )
        )
    if len(knots) < 2:
        raise SystemExit(
            f"ABORT: only {len(knots)} usable calibration bins from {len(pts)} "
            "costed replacements. Cannot build a rate curve."
        )
    return sorted(knots)


def rate_for(area: float, knots: list[tuple[float, float, int]]) -> float:
    """Log-linear interpolation between knots; clamp beyond the ends."""
    if area <= knots[0][0]:
        return knots[0][1]
    if area >= knots[-1][0]:
        return knots[-1][1]
    for (a0, r0, _), (a1, r1, _) in zip(knots, knots[1:]):
        if a0 <= area <= a1:
            t = (math.log(area) - math.log(a0)) / (math.log(a1) - math.log(a0))
            return math.exp(math.log(r0) + t * (math.log(r1) - math.log(r0)))
    return knots[-1][1]


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def cost_rows(
    rows: list[dict],
    knots: list[tuple[float, float, int]],
    index: dict[int, float],
    projected: set[int],
    calib_year: int,
) -> list[dict]:
    idx_today = index.get(TODAY_YEAR)
    if idx_today is None:
        raise SystemExit(f"ABORT: cost index has no value for {TODAY_YEAR}.")
    idx_calib = index.get(calib_year, idx_today)
    lo_year, hi_year = min(index), max(index)

    for r in rows:
        notes = []
        area = deck_area_sqft(r)
        if area is None:
            r.update({f: "" for f in NEW_FIELDS})
            # NBI codes item 52 (deck width) as 0 for culverts -- they have no
            # deck out-to-out. Every zero-width row in the 2024 Arkansas file is
            # a culvert (structure type 19). Name that reason distinctly so the
            # gap is legible; costing culverts needs item 51 (roadway width) and
            # a culvert-specific rate, neither of which this module has.
            r["cost_basis_note"] = (
                "culvert-no-deck-width" if r.get("culvert_cond") else "no-deck-area"
            )
            continue

        rate = rate_for(area, knots)
        # The calibration estimates are NBI-vintage; escalate to today.
        rate_today = rate * (idx_today / idx_calib)
        rebuild = area * rate_today

        r["deck_area_sqft"] = f"{area:.0f}"
        r["rate_today_per_sqft"] = f"{rate_today:.0f}"
        r["est_rebuild_today_usd"] = f"{rebuild:.0f}"
        r["cost_index_today"] = f"{idx_today:.0f}"

        yb = r.get("year_built")
        yb = int(yb) if (yb or "").isdigit() else None
        if yb is None:
            r["est_original_cost_usd"] = ""
            r["cost_index_built"] = ""
            r["cost_ratio_today_to_original"] = ""
            notes.append("no-year-built")
        elif yb < lo_year:
            r["est_original_cost_usd"] = ""
            r["cost_index_built"] = ""
            r["cost_ratio_today_to_original"] = ""
            notes.append(f"year-{yb}-before-index-coverage-{lo_year}")
        else:
            y = min(yb, hi_year)
            idx_built = index[y]
            original = rebuild * (idx_built / idx_today)
            r["est_original_cost_usd"] = f"{original:.0f}"
            r["cost_index_built"] = f"{idx_built:.0f}"
            r["cost_ratio_today_to_original"] = f"{rebuild / original:.1f}"
            if y in projected:
                notes.append("index-year-projected")
            if r.get("year_reconstructed"):
                notes.append("reconstructed-original-cost-is-first-build")

        if area >= BINS[-1][0]:
            notes.append("rate-clamped-largest-bin")
        r["cost_basis_note"] = ";".join(notes)
    return rows


def report(rows: list[dict], knots, calib_year: int) -> None:
    print("\ncalibration knots (from the NBI's own replacement estimates):")
    print(f"  {'median deck sq ft':>18}{'$/sq ft':>10}{'n':>7}")
    for a, r, n in knots:
        print(f"  {a:>18,.0f}{r:>10,.0f}{n:>7}")

    costed = [r for r in rows if r["est_rebuild_today_usd"]]
    orig = [r for r in rows if r["est_original_cost_usd"]]
    print(f"\nrows={len(rows)}  rebuild estimated={len(costed)}  original estimated={len(orig)}")
    if costed:
        v = sorted(float(r["est_rebuild_today_usd"]) for r in costed)
        print(
            f"  rebuild today  total ${sum(v)/1e9:,.1f}B  median ${statistics.median(v):,.0f}"
        )
    if orig:
        v = sorted(float(r["est_original_cost_usd"]) for r in orig)
        # No total here on purpose: these are each in their own year's dollars,
        # so adding them across 1913-2024 produces a number that means nothing.
        print(f"  original (each in its OWN year's dollars)  median ${statistics.median(v):,.0f}")
        ratios = sorted(float(r["cost_ratio_today_to_original"]) for r in orig)
        print(
            f"  today/original ratio  p25 {ratios[len(ratios)//4]:.1f}x  "
            f"median {statistics.median(ratios):.1f}x  p75 {ratios[3*len(ratios)//4]:.1f}x"
        )

    # Sanity check against the estimates we did not calibrate on.
    check = [
        (float(r["est_rebuild_today_usd"]), int(r["improvement_cost_k"]) * 1000)
        for r in rows
        if r["est_rebuild_today_usd"]
        and r.get("improvement_cost_k")
        and r.get("replacement_proposed") == "1"
    ]
    if check:
        rr = sorted(actual / est for est, actual in check if est > 0)
        print(
            f"\n  IN-SAMPLE fit vs ARDOT's own replacement estimates (n={len(rr)}): "
            f"actual/estimated p25 {rr[len(rr)//4]:.2f}  median {statistics.median(rr):.2f}  "
            f"p75 {rr[3*len(rr)//4]:.2f}"
        )
        print(
            "  (these rows ARE the calibration set -- this measures fit, not "
            "predictive accuracy on unseen bridges)"
        )
    print(
        f"\nWARNING: data/cost_index.csv is UNVERIFIED (see costs.py docstring). "
        f"It sets the scale of every est_original_cost_usd value. "
        f"Replace it with a primary-source ENR CCI series before publishing."
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="inp", default=str(IN_CSV))
    p.add_argument("--out", dest="out", default=str(OUT_CSV))
    p.add_argument("--index-file", dest="index_file", default=str(INDEX_CSV))
    p.add_argument("--calib-year", type=int, default=None,
                   help="Vintage of the NBI cost estimates (default: the file's nbi_year)")
    a = p.parse_args()

    with open(a.inp, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        base_fields, rows = list(reader.fieldnames or []), list(reader)
    if not rows:
        raise SystemExit(f"ABORT: {a.inp} is empty.")

    calib_year = a.calib_year
    if calib_year is None:
        yrs = [int(r["nbi_year"]) for r in rows if (r.get("nbi_year") or "").isdigit()]
        calib_year = max(yrs) if yrs else TODAY_YEAR

    index, projected = load_index(Path(a.index_file))
    knots = calibrate(rows)
    rows = cost_rows(rows, knots, index, projected, calib_year)

    fields = base_fields + [f for f in NEW_FIELDS if f not in base_fields]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    report(rows, knots, calib_year)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
