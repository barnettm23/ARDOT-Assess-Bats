# CLAUDE.md — ARDOT-Assess-Bats

## What this is

A scraper and dataset that counts **listed-bat determinations in Arkansas
Department of Transportation environmental documents**, 2021–present, so we can
size the bat-compliance market in Arkansas from public records instead of from
national averages.

Business context: this supports a JBU MBA New Ventures business case for **Ozark
Bioacoustics**, a proposed drone-based bat-survey firm in Northwest Arkansas.
The deliverable is a defensible five-year annual series a professor can
interrogate line by line — not a big number.

Repo: `https://github.com/barnettm23/ARDOT-Assess-Bats` (public)

## Why ARDOT and not HCPs

Habitat Conservation Plans are **not a countable market in Arkansas**. There is
essentially one — Scout Clean Energy's Nimbus Wind in Carroll County, whose
draft HCP and EA are still unpublished as of Sept 2026. You cannot build an
annual series from N=1.

The countable unit is the **bat-triggering project-year**, and ARDOT publishes
every one of them in a consistent, machine-parseable format going back years.

## Source

Index page (scrape this — do not construct URLs):
```
https://ardot.gov/divisions/program-management/construction-contract-development/construction-contractors/additional-project-information-2/environmental-documents/
```
Individual PDFs live on `media.ark.org/ardot/`.

**Measured 2026-09-14, not estimated:** the index carries **1,198 documents
across 2017–2026** — four years further back than this file previously assumed.
Of those, **713 fall under index years 2021+**, which matches the old ~700–800
estimate almost exactly; the extra 485 are genuinely pre-study-period.

```
2017:  68    2018: 137    2019:  97    2020: 183    2021: 105
2022: 137    2023: 162    2024: 134    2025:  94    2026:  81
```

## Pipeline

```bash
python harvest.py        # index HTML -> data/manifest.csv
python fetch.py 10       # manifest -> cache/pdf/   (LIMIT ARG — use it first)
python parse.py          # PDFs -> data/records.csv + data/review_queue.csv
```

`fetch.py` honours `MIN_INDEX_YEAR` (default **2021**). Without it, the manifest
sorts by `index_year` and `fetch.py 10` samples the *oldest* documents on the
site — the first real sample came back entirely from 2017, two of them dated
2006 and 2010. This is a fetch filter only and does not weaken trap 3: a
document cannot be posted before it is written, so `index_year >= Y` is a
superset of `doc_year >= Y` and excludes nothing in scope.

The GitHub Actions workflow exposes this as the `min_year` dispatch input, and
`limit` (`0` = full harvest). A scheduled run with no inputs is capped at 25 —
`github.event.inputs` is null on `schedule`, and an unguarded interpolation
there means an unbounded ~1,200-document fetch.

Flat layout: scripts at repo root, `ROOT = Path(__file__).resolve().parent`.
Only `.github/workflows/*.yml` is nested (GitHub requires it).

### Stage 4 — bridges (`bridges.py`, workflow `bridges`)

```bash
python bridges.py 100                          # NBI Arkansas file -> data/bridges.csv + bridges_summary.csv
python bridges.py --file tests/fixtures/nbi_sample.txt   # offline parse, for tests
python tests/test_bridges.py                   # 10 offline checks against the fixture
```

Source is the **FHWA National Bridge Inventory**, Arkansas state file
(`fhwa.dot.gov/bridge/nbi/<year>/delimited/AR<yy>.txt`, ~12,700 rows, every
public-road bridge over 20 ft, state and local). Independent of ARDOT's index;
one file per year. `NBI_YEAR` defaults to **2024**, the last legacy-format
release — 2025+ submittals use the SNBI specification with different item
codes and this parser will abort loudly on them.

Why it is here: "Str. & Apprs." jobs are the bulk of the bat-triggering
projects and bridges over water are gray-bat roosts. NBI gives each a
coordinate, a **condition** and the owner's own declared plan.

Condition columns follow FHWA's official Good/Fair/Poor schema: the lowest of
deck/superstructure/substructure (items 58/59/60, or culvert 62) — **7–9 Good,
5–6 Fair, ≤4 Poor**. `condition` is computed from the components;
`fhwa_condition` is FHWA's own field, and any disagreement is written to
`parse_notes`. `condition_color` is a hex for the map (green/amber/red, grey
unrated). `over_water` is item 42B ∈ {waterway codes}.

**The pipeline columns are the point.** `work_proposed` (item 75A) and
`year_of_improvement` (item 97) are the owner's stated intent; codes 31–33 set
`replacement_proposed=1`. `bridges_summary.csv` counts per county: poor,
poor-over-water, state-owned-poor, work-proposed, replacement-proposed. A Poor
state-owned bridge over water in an Ozark county is the archetype of a future
bat determination.

#### Measured 2026-09-20 against the live NBI 2024 Arkansas file

Full run, all rows, workflow run 2. **12,974 bridges across all 75 counties.**

```
Good 5,940    Fair 6,330    Poor 704    unrated 0
over water 11,977 (92%)     coordinates missing 4
owners: state 7,346  county 4,275  city 1,107  USFS 132  other 114
year_built 1860-2023
```

Two results that matter for trust:

- **Computed condition matched FHWA's own `BRIDGE_CONDITION` on 12,974 of
  12,974 rows.** Zero disagreements. The Good/Fair/Poor derivation is correct.
- Only **4** rows have missing coordinates and **0** land outside the state
  bounding box. Coordinate decoding is sound.

The pipeline numbers:

| signal | count |
|---|---|
| Poor | 704 |
| Poor **and** over water | 659 |
| Poor and state-owned | 386 |
| replacement proposed (75A ∈ 31/32/33) | 1,532 |
| replacement proposed and over water | 1,459 |

Ozark and NW Arkansas counties (21 of 75) hold **3,410 bridges, 222 Poor, 213
Poor-over-water and 411 replacements proposed** — a quarter of the state's
inventory and a quarter of its declared replacement pipeline.

Highest Poor-over-water counts: Poinsett 31, Washington 31, Polk 28,
Mississippi 26, Madison 23. Highest replacement-proposed: Pulaski 121,
Garland 82, Polk 73, Hot Spring 65.

**Two traps in these columns.**

1. **`work_proposed` is intent recorded at inspection, not a funded programme.**
   Item 75A is what the inspector or owner thinks the structure needs. It is not
   a let schedule and carries no obligation. Treat 1,532 as a ceiling on the
   replacement pipeline, not a forecast.
2. **`year_of_improvement` (item 97) is populated on 36 of 12,974 rows.**
   Effectively empty. **The NBI cannot date the pipeline.** You get the stock of
   candidate projects, never the annual flow. Any per-year series still has to
   come from `records.csv` `doc_year` or from an ARDOT letting schedule; this
   file is a cross-section, not a time series. Do not divide 1,532 by five.

Other caveats: NBI omits spans under 20 ft, so small culvert crossings are
undercounted; `route_number` is blank on 104 rows (mostly county roads with no
route designation); the release lags field inspection by about a year.

### Stage 5 — costs (`costs.py`)

```bash
python costs.py                # data/bridges.csv -> data/bridges_costed.csv
python costs.py --index-file F # swap the deflator
python tests/test_costs.py     # 11 offline checks
```

Adds, adjacent, to every bridge:

| column | meaning |
|---|---|
| `est_original_cost_usd` | **before** — modelled build cost in the year built, in *that year's* dollars |
| `est_rebuild_today_usd` | **after** — modelled cost to rebuild the same structure now |
| `cost_ratio_today_to_original` | the multiple between them |
| `deck_area_sqft`, `rate_today_per_sqft` | the working |
| `cost_index_built`, `cost_index_today` | the deflator values used |
| `cost_basis_note` | why a row is blank or qualified |

**Both columns are models, not records. The NBI has no original-cost field.**
Item 96 is the cost of *proposed work*: 2,050 of the 2,058 rows carrying it
also carry a work code, and a bridge built in 1934 shows $500k there. Original
cost therefore cannot be read; it can only be estimated.

**Rate calibration imports no outside cost assumption.** It comes from ARDOT's
own replacement estimates for its own bridges — the ~1,480 structures with both
a declared replacement and a cost — divided by deck area. Cost per square foot
falls steeply with size, measured live:

```
under 1,000 sq ft  $361     5,000-10,000   $114
1,000-2,500        $202    10,000-25,000    $88
2,500-5,000        $151    25,000+          $70
```

A power-law fit tracks the middle but underestimates the biggest bridges by 23%
($54 against $70), so the module interpolates log-linearly between those
observed medians instead and clamps past the ends. Material is deliberately
unused: once size is controlled the signal is weak and confounded.

Deflation uses `data/cost_index.csv`, an ENR Construction Cost Index table
(1913 = 100) interpolated geometrically between anchors.

#### Measured 2026-09-20, full file

```
rows 12,974   rebuild estimated 10,037   original estimated 10,025
rebuild today: total $7.7B, median $477,742
original: median $108,466 (each in its OWN year's dollars)
today/original ratio: p25 2.4x  median 4.5x  p75 16.7x
```

Uncosted: **2,937 culverts**, every one flagged `culvert-no-deck-width`. The
NBI codes deck width 0 for culverts by convention, so deck area cannot be
formed. Costing them needs item 51 (roadway width) and a culvert-specific rate;
neither exists here yet. 12 rows predate the index and are flagged, not guessed.

#### Four things that will burn you

1. **`data/cost_index.csv` is UNVERIFIED.** Its values were written from
   recollection of the ENR CCI series, not transcribed from an ENR publication,
   and the 2025–26 rows are extrapolated. The index alone sets the scale of
   every `est_original_cost_usd`, and it drives the 70x+ ratios on pre-war
   bridges. Replace it with a primary-source series before any figure leaves
   the repo. `costs.py` prints a warning every run.
2. **Never sum `est_original_cost_usd`.** Each value is in a different year's
   dollars, 1913 through 2024. The total is meaningless. `report()` refuses to
   print one, for the same reason CLAUDE.md refuses to sum `mitigation_usd`.
3. **Per-row error is large.** Actual over predicted on the calibration set runs
   0.73 at the 10th percentile to 1.63 at the 90th. The column is an aggregate
   instrument. Deck area ignores foundation depth, span arrangement and site
   conditions, which are what actually drive bridge cost.
4. **The in-sample fit is not validation.** The 0.92 median actual/estimated is
   measured on the very rows used to calibrate. No held-out test has been run.

Every stage is idempotent and cached. Re-running fetches only what is new.

## Four traps, all confirmed against the live index

These are not hypotheticals. Each was observed directly.

1. **URLs are not constructible.** Real filenames include `110751_env.pdf`,
   `A10031env.pdf`, `020738env-1.pdf`, `020628nepa.pdf`,
   `061754_2022.09.13_aCE-T1-Re-eval.pdf`, and `A000065env.pdf` (an ARDOT typo
   for job A00065). Always use the scraped href.

2. **The index contains broken links.** Job `012542` points at
   `012550env.pdf`; `A70020` points at `A70022env.pdf`; `012428` appears twice,
   once pointing at `012430env.pdf`. `harvest.py` flags these as
   `id_url_mismatch`. Do not silently trust the job label. The live harvest
   flags **5** such rows out of 1,198.

   A second form of this trap does **not** get flagged: jobs `012380` and
   `012381` have distinct, correct-looking hrefs (`012380env.pdf`,
   `012381env.pdf`) and `id_url_mismatch=0`, yet serve **byte-identical PDFs**.
   Same sha256. See the dedup warning under trap 3.

3. **Index years are POSTING years, not document dates — this is the big one.**
   Job `110751` sits under the 2026 heading but is dated 2024-02-06. Jobs repeat
   across headings: `012494` under 2024 and 2025; `A50025` under 2023, 2024 and
   2025; `030530` under 2023 and 2024. **Any annual series keyed off
   `index_year` is wrong.** Use `doc_year`, extracted from inside the PDF.

   Confirmed live and worse than documented: job `012007` sits under 2017 but is
   dated **2006** — an eleven-year gap. Job `020484` under 2017 is dated 2010.
   Five of the nine documents sampled from index year 2021 are dated 2020.

   Records dedupe on PDF `sha256`. **That dedup currently loses projects.** It
   was written for one document posted under two index years; it also fires when
   two *distinct jobs* share content, as `012380`/`012381` do. `012381` is absent
   from `records.csv` entirely — no row, no flag, no `review_queue` entry. Since
   the countable unit is the bat-triggering project-year, a silent drop here
   undercounts the headline number. Fix before trusting any annual series.

4. **~90% of each PDF is identical Nationwide Permit boilerplate**, starting at
   the marker `"Nationwide Permit No."`. `parse.py` truncates there. This cuts
   the parsing surface by an order of magnitude and makes any LLM pass cheap.

## Output schema — data/records.csv

| column | meaning |
|---|---|
| `job_id` | ARDOT job number |
| `doc_date`, `doc_year` | date from inside the PDF — **authoritative** |
| `index_year` | index heading — provenance only, never for analysis |
| `county` | Arkansas county |
| `ce_tier` | Categorical Exclusion tier 1/2/3 |
| `project_length_mi` | project length |
| `bats_listed`, `n_bats_listed` | IPaC bats on the species list, pipe-delimited |
| `det_GRBA` … `det_LBB` | `NE` / `NLAA` / `LAA` per species |
| `det_source` | **how those verdicts were reached** — see below |
| `any_bat_LAA` | 1 if any bat reached likely-to-adversely-affect |
| `acres_cleared` | acres of suitable habitat cleared |
| `mitigation_usd` | in-lieu fee contribution |
| `pup_season_restriction` | seasonal tree-clearing window |
| `sha256` | change detector; dedup is on `(job_id, sha256)` |
| `parse_status`, `parse_notes` | `ok` or `review`, with reasons |

Species codes: `GRBA` gray, `IBAT` Indiana, `NLEB` northern long-eared,
`OBEB` Ozark big-eared, `TCB` tricolored, `LBB` little brown.

### `det_source` — read this before using any `det_*` column

| value | meaning |
|---|---|
| `direct` | species and verdict appeared in the same sentence |
| `inferred` | the verdict sentence named no species; attributed to species named within the preceding 3 sentences |
| `mixed` | both, on different species in the same document |
| *(blank)* | no verdict resolved |

`inferred` exists because ARDOT routinely writes the species list and the
finding as consecutive sentences — "...identified the Indiana Bat, northern
long-eared bat... ARDOT has determined the project will have no effect on these
species." Requiring co-occurrence in a single sentence resolved 1 of 19 records.

**An `inferred` verdict is a lead, not a finding.** Every one is flagged in
`parse_notes` and queued in `review_queue.csv` with the bridging sentence.
Direct evidence always overrides an inferred verdict; an inferred verdict never
overwrites a direct one. **Do not report any figure built on `inferred` rows
without hand-checking them**, and say which is which on anything that leaves the
repo.

Verdict sentences with no species anywhere nearby land in `review_queue.csv` as
`verdict-without-species`. That queue is the diagnostic for whatever document
structure the proximity rule still fails to reach — work it before widening the
lookback window, which sweeps in unrelated species fast.

## State of the code — READ THIS

**Superseded 2026-09-14: the pipeline has now run against the live site.** Two
sample runs on a GitHub Actions runner (the dev container's egress policy blocks
`ardot.gov` and `media.ark.org`, so local runs are not possible; dispatch the
`refresh` workflow instead).

What the live runs established:

- `harvest.py` **works.** It cleared its 300-link guard on the first attempt —
  no year-heading debugging was needed. 1,198 documents, 2017–2026.
- `fetch.py` **works.** 20 PDFs fetched across two runs, zero `fetch_note`
  values, 1 req/sec held.
- `parse.py` **runs, and populates dates, counties, tiers, FAP and species
  lists.** Determination extraction is the weak part: **1 of 19 records resolved
  any verdict at all** — job `012377`, which resolved all four of its listed
  species. Every other record has empty `det_*` columns. See "What the live run
  actually broke" below.
- Still completely untested: `acres_cleared`, `mitigation_usd`,
  `pup_season_restriction`, `project_length_mi`. Every sampled document was a
  Tier 1 CE with no adverse-effect finding, so none of those fields *should*
  populate. They will not be exercised until the sample reaches a Tier 3 with an
  LAA, like job `050475` below.

The regexes were built from **two** documents read in full:

- Job `050475` — Little Piney Creek Str. & Apprs., Hwy 56, Izard County, July
  2022. Tier 3 CE. IPaC listed 10 species. Determinations: "no effect" on three
  birds; NLAA gray bat, scaleshell, snuffbox, Missouri bladderpod; **LAA Indiana
  bat**. 0.47 acre suitable habitat cleared, mitigation ratios 1.25 and 2.0 →
  0.63 acres, **$3,909** to The Conservation Fund. Pup-season clearing
  prohibition May 1 – July 31. Reinitiation if >0.8 acre cleared or >5 IBATs
  taken.
- Job `110751` — Larkin Creek Str. & Apprs., Hwy 121, Lee County, dated
  2024-02-06 but filed under the 2026 index heading. Tier 1 CE. "No effect" on
  NLEB; TCB noted as proposed endangered, no jeopardy.

Two documents proved a thin basis for patterns that must hold across 1,198. The
first run was a debugging session, as predicted — just not of the predicted bugs.

## What the live run actually broke

Fixed, and merged:

- **`DET_PATTERNS` inverted every NLAA into LAA.** `likely to adversely affect`
  is a substring of `not likely to adversely affect`, and the list tested LAA
  first while `determinations()` takes the first match. This silently flipped
  `any_bat_LAA` — the field the revenue model rests on — in the direction that
  overstates it. Latent until extraction started working. **If you reorder
  `DET_PATTERNS`, keep NLAA ahead of LAA.**
- **`review_queue.csv` was blank exactly where it mattered.** A species reaches
  that queue *because* no verdict resolved, which made the set of matched
  sentences empty by construction. Every row arrived with an empty `sentence`.
  It now carries the sentences naming that species.
- **The date regex missed the Tier 3 cover format** (`July 2022`), as predicted
  below. `RE_DATE_MY` is the fallback; the full date still wins where present.

- **Species and verdicts live in different sentences.** The root cause of the
  1-of-19 determination rate, and *not* the failure mode this file predicted.
  Species names appear in IPaC species-list tables (`Mammals NAME STATUS Gray
  Bat (Myotis grisescens) Endangered`), in FWS boilerplate (`If your species
  list includes any mussels, Northern Long-eared Bat, Indiana Bat...`), and in
  `ecos.fws.gov` profile URLs. The determinations sit in narrative prose that
  usually does not repeat the species name.

  Addressed by proximity bridging — see `det_source` in the schema above. A
  verdict sentence naming no species is attributed to species named in the
  preceding 3 sentences and flagged `inferred`. **This is a heuristic and it is
  not validated against hand-coded ground truth.** It is auditable rather than
  correct: every inferred verdict carries its bridging sentence into the review
  queue. The LLM pass below remains the better answer.

- **The sha256 dedup dropped distinct jobs.** Fixed: the key is now
  `(job_id, sha256)`, so one job posted under two index years still collapses
  while two different jobs sharing a PDF both survive, flagged `shared-pdf` and
  queued. Whether `012380`/`012381` is a shared document or a bad index link is
  still an open question for a human.

Open, and the real work:
- **The multi-species risk is real but unproven.** Job `012377` resolved `NE` for
  all four listed bats with `parse_status: ok` and no notes. That may be correct;
  a Tier 3 CE can genuinely find no effect on all four. There is no evidence
  attached either way, which is the point — it does not self-flag.
- **`pdftotext -layout` interleaves page furniture mid-sentence.** From job
  `012375`: `identified the Indiana Bat (Myotis Job Number 012375 Tier 1
  Categorical Exclusion Page 2 of 2 sodalis), northern long-eared bat`. The
  running header splits the binomial across a page break, corrupting sentence
  segmentation. Strip repeating `Job Number`/`Tier N`/`Page N of N` lines before
  `trim()`.
- **`RE_COUNTY` matches form labels.** Job `012359` records `county="Job Name"`,
  picked out of a header laying out `Job Name | County | Route` as columns.
  Arkansas has 75 counties — a closed set, cheap to validate against.
- **The sha256 dedup drops distinct jobs.** See trap 3.

## Debug order

1. **`harvest.py` first.** It aborts if it parses fewer than 300 links — a loud
   failure beats a silent partial parse. If it aborts, the year-heading walk is
   wrong; the headings may not be the tags assumed. Dump the first 30 anchors and
   adjust. *(Passed on first live attempt, 2026-09-14.)*
2. **Check `doc_year` vs `index_year`.** Disagreement is correct and expected —
   confirmed at 2/10 and 5/9 in the two samples. Blank `doc_year` values mean the
   date regex is too narrow; ARDOT uses at least two formats ("February 6, 2024"
   memo style, "July 2022" Tier 3 cover style). *(Both now handled.)*
3. **Check `bats_listed` against the `det_*` columns.** Still the real work, but
   read "What the live run actually broke" first — the gap is a sentence-scoping
   problem, not a pattern problem, and this file previously described it wrong.

## Validation gate before anyone trusts a number

- Hand-code **15 documents** against `records.csv`. Target ≥90% field agreement.
  Log disagreements.
- Work `review_queue.csv` to zero, or document what was left and why.
- **Verify every `any_bat_LAA == 1` row by hand.** Those are the ones that cost
  money and there will not be many.
- **Do not read `any_bat_LAA == 0` as "no adverse effect found."** Across both
  live samples it was 0 on every row, but 18 of 19 records resolved no verdict
  at all. "Determined not to be adversely affected" and "extraction produced
  nothing" are the same value in this column and must not be conflated. Until
  the determination gap closes, treat the 0s as *unknown*, and count
  `n_bats_listed > 0 AND no det_*` as the size of the unresolved pile.
- Sample across the whole period, not the top of the manifest. `MIN_INDEX_YEAR`
  is a floor, not a spread — 10 documents from 2021 are still 10 documents from
  one year.

## Suggested next build: LLM pass on the review queue only

Do **not** route all 1,198 documents through a model. Regex output is auditable;
LLM output needs spot-checking. Run the model only on `review_queue.csv` rows:
feed the trimmed body, demand strict JSON mapping species → verdict, log model
output beside the source sentence. At a few hundred rows this costs pennies.
Keep the prompt in the repo as a file so it is reviewable.

This is now the most valuable single build, and the review queue is finally
carrying what it needs: real per-species sentences rather than an empty column.
The sentence-scoping failure above is exactly the shape of problem a model
handles well and regex does not — the species is in a table on page 2, the
verdict is in prose on page 4, and a human reading both has no trouble.
Feed the model the trimmed body, not the sentence alone, for that reason.

## Etiquette — non-negotiable

ARDOT is a small state agency, not a CDN. Keep the 1 req/sec delay in
`fetch.py`, keep a real contact address in `USER_AGENT`, and never remove the
fetch cache. Use `python fetch.py 10` before any full run.

## Things not to do

- **Do not sum `mitigation_usd` and call it market size.** It is an in-lieu fee
  paid to The Conservation Fund. No consultant earns it. It is an intensity
  signal only. Put a note on any sheet that leaves the repo.
- Do not use `index_year` for the annual series. See trap 3.
- Do not commit PDFs. `.gitignore` excludes `cache/`; keep it that way or the
  repo passes a gigabyte.
- Do not extrapolate ARDOT rates to private development. The ESA "harm"
  definition was rescinded effective **2026-09-12**, which cuts hardest at
  private-land, no-federal-nexus tree clearing. ARDOT has a federal nexus and is
  largely unaffected. Pre- and post-2026 private-land analogues are not
  comparable.

## Known coverage limits

- Local-agency and State Aid projects are **not** in this index.
- ARDOT is one of four demand streams. The others: private development (Arkansas
  DEQ construction stormwater permits by acreage and county), federal-nexus
  non-transport (USACE 404, USFS, NRCS — see below), and energy (MISO
  interconnection queue, Arkansas counties).

## The higher-value parallel action

A FOIA to the **USFWS Arkansas Ecological Services Field Office** (110 South
Amity Road, Suite 300, Conway, AR 72032) for a **TAILS** export of Section 7
consultations FY2021–FY2026, filtered to gray, Indiana, northern long-eared,
Ozark big-eared and tricolored bat, with fields for fiscal year, county, action
agency, action type and effect determination.

TAILS is FWS's internal workload-tracking system with 70+ preset reports. It
covers **all** federal action agencies, not just transportation. It is free, it
is one email, and it has a months-long queue — so it should already be sent. It
complements this repo: TAILS gives breadth, this repo gives acres and dollars
per project.

## Downstream model this data feeds

```
Annual addressable revenue (year t)
  = Σ streams:
      [projects triggering a bat determination in year t]
    × [share reaching LAA or requiring survey]
    × [share Ozark Bioacoustics can perform]
    × [price per engagement]
  + [installed base under monitoring obligation]
    × [annual monitoring contract value]
```

The second term matters most and is usually omitted. Monitoring obligations run
the **life of the permit** — median 28 years across 25 wind-bat HCPs (range
6–43). Model it as an installed base with accretion, not as annual project wins.

Price is the weakest input; there is no public rate card. Fix it with (a) ARDOT
consultant contract awards, which are public, (b) three real quotes from
regional firms, (c) a bottom-up cost model. Not by guessing a share of national
HCP spend.

## Key external reference

Newman, C. & Surrey, K.C. (2025). *The costs of wind energy permitting
compliance actions for regulated bats in the US.* PLOS One 20(5): e0322005.
Median total HCP cost ≈ $4.68M; fatality monitoring ≈56% of that vs
compensatory mitigation ≈40%. Underlying data and all 25 source HCPs:
`https://datadryad.org/dataset/doi:10.5061/dryad.59zw3r2kf`

Caution: that model was trained on FWS Regions 3 and 5 only. **Arkansas is
Region 4.** Extrapolation, and it should be labelled as such.
