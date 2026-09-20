"""Offline checks for costs.py.

Run:  python tests/test_costs.py
"""

import csv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import costs  # noqa: E402

INDEX = ROOT / "data" / "cost_index.csv"


def test_index_interpolates_geometrically():
    idx, projected = costs.load_index(INDEX)
    assert idx[1913] == 100
    # Every anchor year is reproduced exactly.
    assert idx[1970] == 1381 and idx[2000] == 6221 and idx[2024] == 13906
    # Interpolated years sit strictly between their anchors and rise monotonically.
    assert idx[1970] < idx[1972] < idx[1975]
    # Real declines are preserved, not smoothed away: construction costs fell
    # after WWI and again through the Depression.
    assert idx[1925] < idx[1920]
    assert idx[1935] < idx[1930]
    # From 1935 the series rises without interruption.
    years = sorted(y for y in idx if y >= 1935)
    assert all(idx[a] <= idx[b] for a, b in zip(years, years[1:]))
    # Geometric, not linear: the midpoint is below the arithmetic mean.
    assert idx[1972] < (idx[1970] + idx[1975]) / 2 * 1.02
    assert 2025 in projected and 2026 in projected
    assert 2024 not in projected


def _row(**kw):
    base = {
        "structure_len_m": "30.0", "deck_width_m": "9.0", "year_built": "1960",
        "year_reconstructed": "", "replacement_proposed": "0",
        "improvement_cost_k": "", "nbi_year": "2024",
    }
    base.update(kw)
    return base


def _calibration_rows():
    """Enough costed replacements to fill two bins."""
    rows = []
    for _ in range(costs.MIN_BIN_N + 5):
        # ~700 sq ft at $300/sq ft -> $210k
        rows.append(_row(structure_len_m="13.0", deck_width_m="5.0",
                         replacement_proposed="1", improvement_cost_k="210"))
        # ~15,000 sq ft at $90/sq ft -> $1,350k
        rows.append(_row(structure_len_m="62.0", deck_width_m="22.5",
                         replacement_proposed="1", improvement_cost_k="1350"))
    return rows


def test_deck_area_and_tiny_structures_rejected():
    assert costs.deck_area_sqft({"structure_len_m": "10", "deck_width_m": "10"}) is not None
    assert costs.deck_area_sqft({"structure_len_m": "0", "deck_width_m": "9"}) is None
    assert costs.deck_area_sqft({"structure_len_m": "", "deck_width_m": ""}) is None
    assert costs.deck_area_sqft({"structure_len_m": "1", "deck_width_m": "1"}) is None


def test_rate_falls_with_size_and_clamps():
    knots = costs.calibrate(_calibration_rows())
    assert len(knots) >= 2
    small, large = costs.rate_for(700, knots), costs.rate_for(15000, knots)
    assert small > large, "cost per sq ft must fall as deck area rises"
    # Clamped outside the end knots rather than extrapolated to absurdity.
    assert costs.rate_for(10, knots) == knots[0][1]
    assert costs.rate_for(10**7, knots) == knots[-1][1]


def test_original_is_deflated_below_rebuild():
    idx, proj = costs.load_index(INDEX)
    knots = costs.calibrate(_calibration_rows())
    r = _row(year_built="1960")
    costs.cost_rows([r], knots, idx, proj, 2024)
    orig = float(r["est_original_cost_usd"])
    today = float(r["est_rebuild_today_usd"])
    assert 0 < orig < today, "a 1960 build must cost less in 1960 dollars"
    # The ratio is exactly the index ratio.
    assert abs(today / orig - idx[2026] / idx[1960]) < 0.01
    assert r["cost_index_built"] == "824"


def test_recent_build_has_ratio_near_one():
    idx, proj = costs.load_index(INDEX)
    knots = costs.calibrate(_calibration_rows())
    r = _row(year_built="2024")
    costs.cost_rows([r], knots, idx, proj, 2024)
    assert abs(float(r["cost_ratio_today_to_original"]) - idx[2026] / idx[2024]) < 0.1


def test_uncoverable_years_flagged_not_guessed():
    idx, proj = costs.load_index(INDEX)
    knots = costs.calibrate(_calibration_rows())
    old, blank = _row(year_built="1880"), _row(year_built="")
    costs.cost_rows([old, blank], knots, idx, proj, 2024)
    # Rebuild still estimated; only the historical figure is withheld.
    assert old["est_rebuild_today_usd"] and old["est_original_cost_usd"] == ""
    assert "before-index-coverage" in old["cost_basis_note"]
    assert blank["est_original_cost_usd"] == ""
    assert "no-year-built" in blank["cost_basis_note"]


def test_missing_deck_area_yields_no_estimate():
    idx, proj = costs.load_index(INDEX)
    knots = costs.calibrate(_calibration_rows())
    r = _row(structure_len_m="", deck_width_m="")
    costs.cost_rows([r], knots, idx, proj, 2024)
    assert r["est_rebuild_today_usd"] == "" and r["est_original_cost_usd"] == ""
    assert r["cost_basis_note"] == "no-deck-area"


def test_culverts_get_their_own_reason():
    """NBI codes deck width 0 for culverts; the gap must say so, not read as
    generic missing data."""
    idx, proj = costs.load_index(INDEX)
    knots = costs.calibrate(_calibration_rows())
    r = _row(deck_width_m="0", culvert_cond="7")
    costs.cost_rows([r], knots, idx, proj, 2024)
    assert r["cost_basis_note"] == "culvert-no-deck-width"
    assert r["est_rebuild_today_usd"] == ""


def test_reconstructed_rows_are_flagged():
    idx, proj = costs.load_index(INDEX)
    knots = costs.calibrate(_calibration_rows())
    r = _row(year_built="1955", year_reconstructed="1998")
    costs.cost_rows([r], knots, idx, proj, 2024)
    assert "reconstructed-original-cost-is-first-build" in r["cost_basis_note"]


def test_thin_calibration_aborts_rather_than_inventing_a_curve():
    try:
        costs.calibrate([_row(structure_len_m="13.0", deck_width_m="5.0",
                              replacement_proposed="1", improvement_cost_k="210")])
    except SystemExit as e:
        assert "calibration" in str(e)
    else:
        raise AssertionError("expected SystemExit on a thin calibration set")


def test_columns_are_adjacent_and_ordered_before_then_after():
    f = costs.NEW_FIELDS
    assert f.index("est_rebuild_today_usd") - f.index("est_original_cost_usd") == 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
