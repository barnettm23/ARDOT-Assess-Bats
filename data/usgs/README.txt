ARDOT LISTED-BAT DETERMINATIONS
Derived dataset, 2026-09-20

WHAT THIS IS
Effect determinations for federally listed bat species, extracted from
Arkansas Department of Transportation environmental documents (Categorical
Exclusions) published at:
  https://ardot.gov/divisions/program-management/construction-contract-development/
  construction-contractors/additional-project-information-2/environmental-documents/

712 records covering documents posted under index years 2021-2026.
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

Code, including every extraction pattern: https://github.com/barnettm23/ARDOT-Assess-Bats

THE FOUR THINGS MOST LIKELY TO BE MISREAD
1. det_source. "direct" means the species name and the effect determination
   appeared in the SAME sentence. "inferred" means the determination sentence
   named no species ("...no effect on these species") and was attributed to
   species named in the preceding three sentences. Inferred values are
   positional guesses, not read determinations. 235 of 395
   resolved records rest on inference. Filter to det_source='direct' for any
   analysis that needs to be defensible.

2. any_bat_LAA = 0 does not mean "no adverse effect". 317 of
   712 records resolved no determination at all; there the value is
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
estimates. project_cost_usd is populated on 13 of 712 records
and row_cost_usd on 1. The columns are included because the values that
are there are real, not because the coverage supports analysis. Project cost
for these jobs has to come from a different source -- ARDOT bid tabulations or
the STIP, both public.

LOCATION -- READ BEFORE MAPPING ANYTHING
County is the reliable location field. It is validated against the 75
Arkansas county names and carries a Census FIPS code; where no Arkansas
county could be confirmed the field is blank rather than guessed.

  records with a validated county:  688 of 712
  records with county_fips:         688 of 712

COORDINATES ARE PARTLY CONTAMINATED. 282 records carry a
coordinate-looking value, but only 76 DISTINCT values exist
among them, and 166 of the 282 share a value with an
unrelated source PDF. Those cannot be project locations -- the most common
one sits at the geographic centre of Arkansas and appears on projects in two
non-adjacent counties. It is a locator map or a default map centre.

Use coord_confidence:
  document-specific          116 records -- value unique to its
                             source document. This is a NECESSARY condition,
                             not a verified one. It has NOT been confirmed to
                             be the project's location. Spot-check before use.
  repeated-across-documents  166 records -- DO NOT MAP.

routes and waterways are the honest locational detail: ARDOT names projects
by route and the feature crossed ("Little Piney Creek Str. & Apprs., Hwy 56"),
which with a county places a bridge project precisely by hand or against
ARDOT's own GIS.

  records with routes:    643 of 712
  records with waterways: 619 of 712

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
michael@seismicagency.com
