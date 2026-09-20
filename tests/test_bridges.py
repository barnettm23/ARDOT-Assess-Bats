"""Offline checks for bridges.py against a synthetic NBI delimited file.

Run:  python -m pytest tests/  (or python tests/test_bridges.py)
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bridges  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "nbi_sample.txt"


def rows():
    return {r["structure_number"]: r for r in bridges.parse_file(FIXTURE, nbi_year=2024)}


def test_county_fips_lookup():
    r = rows()
    assert r["000000000005612"]["county"] == "Izard"
    assert r["000000000010231"]["county"] == "Washington"
    assert r["000000000020044"]["county"] == "Pulaski"
    assert r["000000000040099"]["county"] == "Conway"
    assert r["000000000030077"]["county"] == ""
    assert "unknown-county-fips:999" in r["000000000030077"]["parse_notes"]


def test_packed_dms_coordinates():
    r = rows()["000000000005612"]
    # 36 05 12.34 N, 091 58 22.11 W
    assert abs(float(r["lat"]) - (36 + 5 / 60 + 12.34 / 3600)) < 1e-6
    assert abs(float(r["lon"]) - -(91 + 58 / 60 + 22.11 / 3600)) < 1e-6
    assert r["coord_note"] == ""


def test_missing_coordinates_flagged_not_dropped():
    r = rows()["000000000030077"]
    assert r["lat"] == "" and r["lon"] == ""
    assert r["coord_note"] == "missing"


def test_condition_schema_lowest_component_wins():
    r = rows()
    assert (r["000000000005612"]["condition"], r["000000000005612"]["lowest_rating"]) == ("Poor", 4)
    assert (r["000000000010231"]["condition"], r["000000000010231"]["lowest_rating"]) == ("Good", 7)
    assert (r["000000000020044"]["condition"], r["000000000020044"]["lowest_rating"]) == ("Fair", 5)
    # Culvert: 58/59/60 are N, culvert rating 3 -> Poor
    assert (r["000000000030077"]["condition"], r["000000000030077"]["lowest_rating"]) == ("Poor", 3)


def test_fhwa_disagreement_is_flagged():
    r = rows()["000000000040099"]
    assert r["condition"] == "Fair" and r["fhwa_condition"] == "Good"
    assert "condition-disagrees" in r["parse_notes"]


def test_colors_follow_condition():
    for r in rows().values():
        assert r["condition_color"] == bridges.CONDITION_COLOR[r["condition"]]


def test_pipeline_fields():
    r = rows()
    p = r["000000000005612"]
    assert p["work_proposed_code"] == 31 and p["replacement_proposed"] == 1
    assert p["year_of_improvement"] == 2024 and p["improvement_cost_k"] == 1250
    assert p["over_water"] == 1 and p["owner"] == "State Highway Agency"
    assert p["age_yrs"] == 72
    assert r["000000000020044"]["replacement_proposed"] == 0  # deck rehab, code 36
    assert r["000000000020044"]["over_water"] == 0  # railroad under
    assert r["000000000010231"]["year_reconstructed"] == 2015


def test_summary_counts():
    s = {row["county"]: row for row in bridges.summarize(list(rows().values()))}
    assert s["Izard"]["poor"] == 1 and s["Izard"]["poor_over_water"] == 1
    assert s["Izard"]["replacement_proposed"] == 1
    assert s["UNKNOWN"]["bridges"] == 1
    assert s["Pulaski"]["work_proposed"] == 1 and s["Pulaski"]["replacement_proposed"] == 0


def test_limit():
    assert len(bridges.parse_file(FIXTURE, limit=2)) == 2


def test_header_resolution_by_item_number():
    col = bridges.item_map(["LAT_016", "SOME_RENAMED_THING_017", "DECK_COND_058", "BRIDGE_CONDITION"])
    assert col["016"] == "LAT_016" and col["017"] == "SOME_RENAMED_THING_017"
    assert col["BRIDGE_CONDITION"] == "BRIDGE_CONDITION"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
