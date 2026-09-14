# ardot-bat

A reproducible, monthly-refreshed dataset of listed-bat determinations in
Arkansas Department of Transportation environmental documents, 2021-present.

Built to size the bat-compliance market in Arkansas from public records rather
than from national averages.

## Why this exists

Habitat Conservation Plans are not a countable market in Arkansas -- there is
essentially one (Nimbus Wind, Carroll County). The countable unit is the
*bat-triggering project-year*, and ARDOT publishes every one of them.

## Source

ARDOT Environmental Documents index:
https://ardot.gov/divisions/program-management/construction-contract-development/construction-contractors/additional-project-information-2/environmental-documents/

Roughly 65-155 documents per year, 2021-2026. Individual PDFs live on
media.ark.org.

## Pipeline

```
python harvest.py     # index -> data/manifest.csv
python fetch.py 10    # manifest -> cache/pdf/  (1 req/sec, cached; limit arg)
python parse.py       # PDFs -> data/records.csv + data/review_queue.csv
```

Everything is idempotent. Re-running fetches only what is new.

## Four traps this handles

1. **URLs are not constructible.** Observed filenames include `110751_env.pdf`,
   `A10031env.pdf`, `020738env-1.pdf`, `020628nepa.pdf`, and `A000065env.pdf`
   (an ARDOT typo for job A00065). Always scrape the href.
2. **The index contains broken links.** Job 012542 points at `012550env.pdf`;
   A70020 points at `A70022env.pdf`. `id_url_mismatch` flags these.
3. **Index years are posting years, not document dates.** Job 110751 sits under
   2026 but is dated 2024-02-06. Jobs repeat across years (012494 under 2024 and
   2025; A50025 under 2023, 2024 and 2025). Use `doc_year`, never `index_year`,
   for any annual series. Records are deduped on PDF sha256.
4. **~90% of each PDF is Nationwide Permit boilerplate.** Truncated at the first
   marker before parsing.

## Output schema (data/records.csv)

| column | meaning |
|---|---|
| `job_id` | ARDOT job number |
| `doc_date`, `doc_year` | date from inside the PDF -- authoritative |
| `county` | Arkansas county |
| `ce_tier` | Categorical Exclusion tier 1/2/3 |
| `bats_listed`, `n_bats_listed` | IPaC bats on the species list, pipe-delimited |
| `det_GRBA` ... `det_LBB` | NE / NLAA / LAA per species |
| `any_bat_LAA` | 1 if any bat reached likely-to-adversely-affect |
| `acres_cleared` | acres of suitable habitat cleared |
| `mitigation_usd` | in-lieu fee contribution |
| `pup_season_restriction` | seasonal tree-clearing window |
| `sha256` | dedup key and change detector |
| `parse_status`, `parse_notes` | `ok` or `review`, with reasons |

Species codes: GRBA gray, IBAT Indiana, NLEB northern long-eared,
OBEB Ozark big-eared, TCB tricolored, LBB little brown.

## Validation before you trust it

- Hand-code 15 documents against `records.csv`. Target >=90% field agreement.
- Work `review_queue.csv` to zero, or document what you left.
- `any_bat_LAA` rows are the ones that cost money -- verify all of them by hand;
  there will not be many.

## Known limits

- Local-agency and State Aid projects are not in this index.
- In-lieu fee dollars are mitigation payments, not consultant fees. They are not
  a revenue proxy.
- The ESA "harm" definition was rescinded effective 2026-09-12. Pre- and
  post-2026 years are not strictly comparable for private-land analogues.

## Licence / etiquette

Source documents are public records. Keep the 1 req/sec delay and a real
contact address in `USER_AGENT`.
