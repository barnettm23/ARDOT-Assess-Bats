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
Individual PDFs live on `media.ark.org/ardot/`. Roughly 65–155 documents per
year, 2021–2026, so ~700–800 total.

## Pipeline

```bash
python harvest.py        # index HTML -> data/manifest.csv
python fetch.py 10       # manifest -> cache/pdf/   (LIMIT ARG — use it first)
python parse.py          # PDFs -> data/records.csv + data/review_queue.csv
```

Flat layout: scripts at repo root, `ROOT = Path(__file__).resolve().parent`.
Only `.github/workflows/refresh.yml` is nested (GitHub requires it).

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
   `id_url_mismatch`. Do not silently trust the job label.

3. **Index years are POSTING years, not document dates — this is the big one.**
   Job `110751` sits under the 2026 heading but is dated 2024-02-06. Jobs repeat
   across headings: `012494` under 2024 and 2025; `A50025` under 2023, 2024 and
   2025; `030530` under 2023 and 2024. **Any annual series keyed off
   `index_year` is wrong.** Use `doc_year`, extracted from inside the PDF.
   Records dedupe on PDF `sha256`.

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
| `any_bat_LAA` | 1 if any bat reached likely-to-adversely-affect |
| `acres_cleared` | acres of suitable habitat cleared |
| `mitigation_usd` | in-lieu fee contribution |
| `pup_season_restriction` | seasonal tree-clearing window |
| `sha256` | dedup key and change detector |
| `parse_status`, `parse_notes` | `ok` or `review`, with reasons |

Species codes: `GRBA` gray, `IBAT` Indiana, `NLEB` northern long-eared,
`OBEB` Ozark big-eared, `TCB` tricolored, `LBB` little brown.

## State of the code — READ THIS

**The scripts have never been run against the live site.** They were written in
a sandbox with no network access to ardot.gov. They compile; that is all that is
proven. The regexes were built from **two** documents read in full:

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

Two documents is a thin basis for patterns that must hold across 700. **Expect
the first run to be a debugging session, not a harvest.**

## Debug order

1. **`harvest.py` first.** It aborts if it parses fewer than 300 links — a loud
   failure beats a silent partial parse. If it aborts, the year-heading walk is
   wrong; the headings may not be the tags assumed. Dump the first 30 anchors and
   adjust.
2. **Check `doc_year` vs `index_year`** on the first ten rows. Disagreement is
   correct and expected. Many blank `doc_year` values mean the date regex is too
   narrow — ARDOT uses at least two formats ("February 6, 2024" memo style,
   "July 2022" Tier 3 cover style) and only the first is currently caught.
3. **Check `bats_listed` against the `det_*` columns.** This gap is the real
   work. The determination logic reads sentence-by-sentence and keeps the worst
   verdict per species. **Known weakness:** a single sentence listing several
   species with *different* verdicts will be mis-assigned, and it will not
   self-flag — only species with *no* verdict at all reach
   `review_queue.csv`. Hand-check for this specifically.

## Validation gate before anyone trusts a number

- Hand-code **15 documents** against `records.csv`. Target ≥90% field agreement.
  Log disagreements.
- Work `review_queue.csv` to zero, or document what was left and why.
- **Verify every `any_bat_LAA == 1` row by hand.** Those are the ones that cost
  money and there will not be many.

## Suggested next build: LLM pass on the review queue only

Do **not** route all 700 documents through a model. Regex output is auditable;
LLM output needs spot-checking. Run the model only on `review_queue.csv` rows:
feed the trimmed body, demand strict JSON mapping species → verdict, log model
output beside the source sentence. At a few hundred rows this costs pennies.
Keep the prompt in the repo as a file so it is reviewable.

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
