"""Build the share-ready package for an outside researcher.

`python export_usgs.py` -> data/usgs/

  ardot_bat_determinations.csv   the data, UTF-8, RFC 4180
  data_dictionary.csv            one row per column: definition, type, domain
  README.txt                     provenance, method, and the limits that matter

CSV rather than the review workbook on purpose. The workbook holds formulas, a
half-finished validation sheet and cells meant for a particular reader; that is
a working instrument, not a deliverable. USGS practice is also to avoid
proprietary formats for anything meant to be preserved or reused.

This is NOT a USGS data release. A formal release needs FGDC CSDGM metadata
validated with the `mp` parser and Fundamental Science Practices review. What
this produces is the tier below: a clean, documented, citable-by-URL table that
a collaborator can actually use, and the substrate FGDC metadata would be
written from.
"""

import csv
import hashlib
import shutil
from datetime import date
from pathlib import Path

import parse

ROOT = Path(__file__).resolve().parent
RECORDS = ROOT / "data" / "records.csv"
REVIEW = ROOT / "data" / "review_queue.csv"
OUT = ROOT / "data" / "usgs"

# Columns to publish, in order, with definition / type / domain. Anything not
# listed is deliberately withheld -- internal parse plumbing is not something a
# researcher should have to interpret.
SCHEMA = [
    ("job_id", "text", "ARDOT job number, as printed in the source index", ""),
    ("doc_date", "text", "Date printed inside the PDF. May be month-and-year only where the document carries no day", ""),
    ("doc_year", "integer", "Year from doc_date. AUTHORITATIVE for any annual series", "2013-2026"),
    ("index_year", "integer", "Year heading the document was posted under. PROVENANCE ONLY -- posting year, not document date", "2021-2026"),
    ("county", "text", "Arkansas county, validated against the 75 county names. Blank where none could be confirmed", "75 Arkansas counties"),
    ("county_fips", "text", "5-digit Census FIPS code for county. Blank where county is blank", "05001-05149"),
    ("routes", "text", "Highway numbers named in the document, pipe-delimited", ""),
    ("waterways", "text", "Named water features crossed, pipe-delimited", ""),
    ("coordinates", "text", "Coordinates as stated in the document. USE ONLY WITH coord_confidence -- see README", ""),
    ("coord_format", "text", "Notation of the coordinates column", "decimal_degrees | degrees_minutes_seconds | utm"),
    ("coord_confidence", "text", "'document-specific' = the value does not repeat across documents (necessary, NOT sufficient, evidence it is the project location). 'repeated-across-documents' = the same value appears in unrelated PDFs and CANNOT be a project location -- do not map these", "document-specific | repeated-across-documents | (blank)"),
    ("fap", "text", "Federal Aid Project number, where stated. Links the project to federal funding records", ""),
    ("ce_tier", "integer", "Categorical Exclusion tier", "1 | 2 | 3"),
    ("project_length_mi", "decimal", "Project length in miles, where stated", ""),
    ("bats_listed", "text", "Listed bat species on the IPaC species list, pipe-delimited species codes", "GRBA|IBAT|NLEB|OBEB|TCB|LBB"),
    ("n_bats_listed", "integer", "Count of bats_listed", "0-6"),
    ("det_GRBA", "text", "Effect determination, gray bat (Myotis grisescens)", "NE | NLAA | LAA"),
    ("det_IBAT", "text", "Effect determination, Indiana bat (Myotis sodalis)", "NE | NLAA | LAA"),
    ("det_NLEB", "text", "Effect determination, northern long-eared bat (Myotis septentrionalis)", "NE | NLAA | LAA"),
    ("det_OBEB", "text", "Effect determination, Ozark big-eared bat (Corynorhinus townsendii ingens)", "NE | NLAA | LAA"),
    ("det_TCB", "text", "Effect determination, tricolored bat (Perimyotis subflavus)", "NE | NLAA | LAA"),
    ("det_LBB", "text", "Effect determination, little brown bat (Myotis lucifugus)", "NE | NLAA | LAA"),
    ("det_source", "text", "HOW the det_* values were obtained. READ THE README BEFORE USING det_* COLUMNS", "direct | inferred | mixed | (blank)"),
    ("any_bat_LAA", "integer", "1 if any bat reached likely-to-adversely-affect. 0 means NOT ESTABLISHED where det_source is blank, not 'no effect'", "0 | 1"),
    ("survey_status", "text", "Whether a bat survey was conducted or required for this project. Generic regulatory advisories are excluded", "conducted | required | mentioned | (blank)"),
    ("survey_types", "text", "Survey methods named, pipe-delimited", "presence/absence | mist-net | acoustic | emergence | habitat assessment"),
    ("acres_cleared", "decimal", "Acres of suitable habitat cleared, where stated", ""),
    ("mitigation_usd", "decimal", "In-lieu fee contribution in US dollars. NOT consultant revenue -- see README", ""),
    ("mitigation_ratio", "text", "Mitigation ratios applied, pipe-delimited", ""),
    ("project_cost_usd", "decimal", "Total project cost in US dollars WHERE STATED. Very sparse -- these are NEPA compliance documents, not cost estimates; see README", ""),
    ("row_cost_usd", "decimal", "Right-of-way cost in US dollars where stated. Very sparse", ""),
    ("pup_season_restriction", "text", "Seasonal tree-clearing prohibition window, where stated", ""),
    ("source_url", "text", "Permanent URL of the source PDF. Every row is independently verifiable", ""),
    ("sha256", "text", "SHA-256 of the source PDF as fetched. Detects silent replacement upstream", ""),
    ("parse_status", "text", "Extraction outcome for this record", "ok | review | empty-or-scanned"),
    ("parse_notes", "text", "Semicolon-delimited extraction flags", ""),
]

README = """ARDOT LISTED-BAT DETERMINATIONS
Derived dataset, {today}

WHAT THIS IS
Effect determinations for federally listed bat species, extracted from
Arkansas Department of Transportation environmental documents (Categorical
Exclusions) published at:
  https://ardot.gov/divisions/program-management/construction-contract-development/
  construction-contractors/additional-project-information-2/environmental-documents/

{n_records} records covering documents posted under index years 2021-2026.
Source PDFs are public records hosted on media.ark.org.

WHAT THIS IS NOT
Not observation data. These are regulatory conclusions -- what an agency
determined -- not detections, captures, or acoustic records. It is not a
NABat contribution and does not follow the NABat schema.

Not a USGS data release. No FGDC CSDGM metadata, no Fundamental Science
Practices review, no DOI. Cite it as a derived dataset with the URL below.

HOW IT WAS BUILT
  1. Scrape the ARDOT index for PDF links (never construct URLs; ARDOT
     filenames are not derivable from job numbers).
  2. Fetch each PDF at 1 request/second, recording SHA-256.
  3. Extract text with pdftotext -layout, truncate at the Nationwide Permit
     boilerplate marker, then apply pattern extraction.

Code, including every extraction pattern: {repo}

THE FOUR THINGS MOST LIKELY TO BE MISREAD
1. det_source. "direct" means the species name and the effect determination
   appeared in the SAME sentence. "inferred" means the determination sentence
   named no species ("...no effect on these species") and was attributed to
   species named in the preceding three sentences. Inferred values are
   positional guesses, not read determinations. {n_inferred} of {n_resolved}
   resolved records rest on inference. Filter to det_source='direct' for any
   analysis that needs to be defensible.

2. any_bat_LAA = 0 does not mean "no adverse effect". {n_unresolved} of
   {n_records} records resolved no determination at all; there the value is
   0 because nothing was found, not because nothing was determined. Treat
   det_source='' rows as UNKNOWN. The honest denominator for any rate is
   records where det_source is non-blank.

3. doc_year, not index_year. index_year is the year ARDOT POSTED the
   document. Documents posted from 2021 onward carry document dates back to
   2013. Any annual series keyed on index_year is wrong.

4. mitigation_usd is an in-lieu fee paid to a conservation fund. No
   consultant or contractor earns it. It is an intensity signal only and must
   not be summed as market size or program cost.

COST FIELDS ARE NEARLY EMPTY, AND THAT IS THE FINDING
ARDOT environmental documents are NEPA compliance records, not cost
estimates. project_cost_usd is populated on {n_cost} of {n_records} records
and row_cost_usd on {n_row}. The columns are included because the values that
are there are real, not because the coverage supports analysis. Project cost
for these jobs has to come from a different source -- ARDOT bid tabulations or
the STIP, both public.

LOCATION -- READ BEFORE MAPPING ANYTHING
County is the reliable location field. It is validated against the 75
Arkansas county names and carries a Census FIPS code; where no Arkansas
county could be confirmed the field is blank rather than guessed.

  records with a validated county:  {n_county} of {n_records}
  records with county_fips:         {n_fips} of {n_records}

COORDINATES ARE PARTLY CONTAMINATED. {n_coords} records carry a
coordinate-looking value, but only {n_coord_distinct} DISTINCT values exist
among them, and {n_coord_bad} of the {n_coords} share a value with an
unrelated source PDF. Those cannot be project locations -- the most common
one sits at the geographic centre of Arkansas and appears on projects in two
non-adjacent counties. It is a locator map or a default map centre.

Use coord_confidence:
  document-specific          {n_coord_ok} records -- value unique to its
                             source document. This is a NECESSARY condition,
                             not a verified one. It has NOT been confirmed to
                             be the project's location. Spot-check before use.
  repeated-across-documents  {n_coord_bad} records -- DO NOT MAP.

routes and waterways are the honest locational detail: ARDOT names projects
by route and the feature crossed ("Little Piney Creek Str. & Apprs., Hwy 56"),
which with a county places a bridge project precisely by hand or against
ARDOT's own GIS.

  records with routes:    {n_routes} of {n_records}
  records with waterways: {n_waterways} of {n_records}

County centroids were deliberately NOT synthesized. They would look like
project locations and are not.

KNOWN DEFECTS
- Some PDFs embed subsetted fonts with no ToUnicode map. pdftotext returns
  text shifted 29 ASCII positions, which matches nothing; those passages are
  counted as "resolved nothing" when the truth is "not read".
- One PDF can serve several job numbers (largest group observed: 13 jobs, one
  document). Each job appears as its own row, flagged shared-pdf in
  parse_notes. Whether those are separate project-years is undecided and
  affects any count materially.
- Local-agency and State Aid projects are not in this index.

VALIDATION STATUS
One document (job 050475) was checked field-by-field against an
independently-recorded ground truth and matched on 10 of 10 fields. A broader
hand-coding exercise (15 documents, >=90% field agreement) is defined but NOT
yet complete. Treat this dataset as unvalidated at corpus scale.

CONTACT
{contact}
"""


def main() -> None:
    with RECORDS.open(newline="", encoding="utf-8") as fh:
        records = list(csv.DictReader(fh))
    if not records:
        raise SystemExit("no records -- run the pipeline first")

    # Same function parse.py uses, so the published flag and the repository's
    # own flag can never disagree. Applied here too because the check is
    # cross-record and records.csv may predate the parser change.
    for r in records:
        r.setdefault("coord_confidence", "")
    parse.flag_coordinate_confidence(records)

    OUT.mkdir(parents=True, exist_ok=True)
    fields = [c[0] for c in SCHEMA]
    missing = [f for f in fields if f not in records[0]]
    if missing:
        raise SystemExit(f"records.csv is missing columns: {missing}")

    data_path = OUT / "ardot_bat_determinations.csv"
    with data_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow({f: r.get(f, "") for f in fields})

    dict_path = OUT / "data_dictionary.csv"
    with dict_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["column", "type", "definition", "domain"])
        for name, typ, definition, domain in SCHEMA:
            w.writerow([name, typ, definition, domain])

    resolved = [r for r in records if r.get("det_source")]
    inferred = [r for r in records if r.get("det_source") == "inferred"]
    stats = {
        "today": date.today().isoformat(),
        "n_records": len(records),
        "n_resolved": len(resolved),
        "n_inferred": len(inferred),
        "n_unresolved": len(records) - len(resolved),
        "n_coords": sum(1 for r in records if r.get("coordinates")),
        "n_county": sum(1 for r in records if r.get("county")),
        "n_cost": sum(1 for r in records if r.get("project_cost_usd")),
        "n_row": sum(1 for r in records if r.get("row_cost_usd")),
        "n_fips": sum(1 for r in records if r.get("county_fips")),
        "n_routes": sum(1 for r in records if r.get("routes")),
        "n_waterways": sum(1 for r in records if r.get("waterways")),
        "n_coord_distinct": len({r["coordinates"] for r in records if r.get("coordinates")}),
        "n_coord_ok": sum(1 for r in records if r.get("coord_confidence") == "document-specific"),
        "n_coord_bad": sum(1 for r in records if r.get("coord_confidence") == "repeated-across-documents"),
        "repo": "https://github.com/barnettm23/ARDOT-Assess-Bats",
        "contact": "michael@seismicagency.com",
    }
    (OUT / "README.txt").write_text(README.format(**stats), encoding="utf-8")

    if REVIEW.exists():
        shutil.copyfile(REVIEW, OUT / "review_queue.csv")

    digest = hashlib.sha256(data_path.read_bytes()).hexdigest()
    print(f"wrote {OUT.relative_to(ROOT)}/")
    print(f"  ardot_bat_determinations.csv  {len(records)} rows, {len(fields)} columns")
    print(f"  data_dictionary.csv           {len(SCHEMA)} column definitions")
    print(f"  README.txt                    provenance and limitations")
    print(f"  sha256(data) = {digest}")
    print(f"\n  coordinates stated: {stats['n_coords']}/{stats['n_records']}")
    print(f"  county validated:   {stats['n_county']}/{stats['n_records']}")
    print(f"  direct verdicts:    {len(resolved) - len(inferred)}/{stats['n_records']}")


if __name__ == "__main__":
    main()
