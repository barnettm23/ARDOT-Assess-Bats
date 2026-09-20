"""Stages 3-6 -- extract, trim, parse, validate.

Turns cached PDFs into data/records.csv plus data/review_queue.csv.

Design note: roughly 90% of every ARDOT environmental document is identical
Nationwide Permit boilerplate. We truncate at the first boilerplate marker,
which cuts the parsing surface by about an order of magnitude and makes the
optional LLM pass cheap.

Determinations are the one field regex handles badly, because the sentence
structure varies. Regex takes a first pass; anything it cannot resolve
confidently is written to review_queue.csv with its source sentence, for either
a human or an LLM call (see llm_determinations.py, not included -- wire it to
whatever model you prefer and keep the prompt in the repo).
"""

import csv
import re
import subprocess
from dataclasses import dataclass, asdict, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PDF_DIR = ROOT / "cache" / "pdf"
TXT_DIR = ROOT / "cache" / "txt"
MANIFEST = ROOT / "data" / "manifest.csv"
RECORDS = ROOT / "data" / "records.csv"
REVIEW = ROOT / "data" / "review_queue.csv"

BOILERPLATE_MARKERS = [
    "Nationwide Permit No.",
    "Nationwide Permit General Conditions",
    "NATIONWIDE PERMIT NO.",
]

BATS = {
    "gray bat": "GRBA",
    "indiana bat": "IBAT",
    "northern long-eared bat": "NLEB",
    "northern long-earned bat": "NLEB",  # real typo seen in the wild
    "ozark big-eared bat": "OBEB",
    "tricolored bat": "TCB",
    "little brown bat": "LBB",
}

MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)

RE_JOB = re.compile(r"\b(?:ARDOT\s+)?JOB\s*#?\s*([A-Z0-9]{6})\b", re.I)
RE_FAP = re.compile(r"\bFAP\s+([A-Z0-9\-()\s]{6,30}?)(?:\s{2,}|\n)", re.I)
# The old pattern took the FIRST "X County" match and trusted it. That put
# "Job Name" in the county column on 89 of 712 records (from a form header
# laying out "Job Name | County | Route" as columns), truncated "St. Francis"
# to "Francis" on 4, and accepted "Coahoma" -- a Mississippi county mentioned
# across the state line. 102 of 712 rows carried something that is not an
# Arkansas county.
#
# Now: find every candidate, keep the first that is really an Arkansas county.
# The optional "St." prefix is what rescues St. Francis; \s+ rather than \s
# absorbs the newlines pdftotext leaves inside a name ("Little\nRiver").
RE_COUNTY = re.compile(
    r"\b((?:St\.?\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+Count(?:y|ies)\b"
)

# Names only. FIPS codes are joined from data/ar_county_fips.csv, which
# fetch_fips.py pulls from Census -- see the reasoning there for why they are
# not written out here.
ARKANSAS_COUNTIES = {
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
    "Sebastian", "Sevier", "Sharp", "Stone", "Union", "Van Buren", "Washington",
    "White", "Woodruff", "Yell",
}

# Location detail short of coordinates. ARDOT names projects by route and by
# the feature crossed -- "Little Piney Creek Str. & Apprs., Hwy 56" -- which
# with a county is enough to place a bridge project precisely by hand.
RE_ROUTE = re.compile(r"\b(?:Hwy|Highway|Rte|Route)\.?\s*(\d{1,3}[A-Za-z]?)\b", re.I)
RE_WATERWAY = re.compile(
    r"\b((?:[A-Z][A-Za-z'\-]+\s+){1,3}"
    r"(?:Creek|River|Bayou|Branch|Slough|Ditch|Fork|Lake))\b"
)

# Do these documents carry coordinates at all? Asserted "no" from two documents
# read months ago; these patterns settle it across the whole corpus instead.
# Bounds are Arkansas: roughly 33.0-36.5 N, 89.6-94.6 W.
RE_DECIMAL_DEG = re.compile(r"\b(3[3-6]\.\d{3,})\s*[,\s]\s*(-?9[0-4]\.\d{3,})\b")
RE_DMS = re.compile(
    r"\b(\d{1,3})\s*[°d]\s*(\d{1,2})\s*['m]\s*([\d.]+)\s*[\"s]?\s*([NSEW])\b", re.I
)
RE_UTM = re.compile(r"\bUTM\b[^.]{0,80}?\b(\d{6})\b[\s,]+\b(\d{7})\b", re.I)
RE_DATE = re.compile(rf"\b({MONTHS})\s+(\d{{1,2}}),\s+(\d{{4}})\b")
# Tier 3 cover pages carry a month and year with no day ("July 2022", job
# 050475). Fallback only: the full date above wins wherever a document has one.
RE_DATE_MY = re.compile(rf"\b({MONTHS})\s+(\d{{4}})\b")
RE_TIER = re.compile(r"Tier\s+([123])\s+Categorical\s+Exclusion", re.I)
RE_ACRES = re.compile(
    r"(?:suitable\s+(?:\w+\s+)?habitat\s+clearing[^.]{0,80}?totals?|"
    r"will\s+remove)\s+([\d.]+)\s*acres?",
    re.I,
)
RE_MITIG = re.compile(
    r"(?:deduct|contribute)\s+\$([\d,]+)(?:\.\d\d)?\s+(?:from|to)", re.I
)
RE_PUPSEASON = re.compile(
    r"pup\s+season[,\s]+(\w+\s+\d{1,2})\s*[-\u2013]\s*(\w+\s+\d{1,2})", re.I
)
RE_LENGTH = re.compile(r"project\s+length\s+is\s+([\d.]+)\s+mile", re.I)

# --- Financial and demand signals -------------------------------------------
#
# What an ARDOT environmental CE actually states about money is not yet
# established -- these documents are written for NEPA compliance, not costing,
# so a total project cost may or may not appear. RE_DOLLAR_CONTEXT is the
# honest answer to that: every dollar figure in the trimmed body is captured
# with its surrounding words and queued, so one full run reports what phrasing
# exists instead of us guessing. Refine the targeted patterns from that
# evidence, then re-parse -- re-parsing costs ARDOT nothing once the PDFs are
# cached.
RE_PROJECT_COST = re.compile(
    r"(?:total|estimated|construction|project)\s+(?:\w+\s+){0,2}?costs?"
    r"[^.$]{0,80}?\$\s?([\d,]+(?:\.\d{2})?)",
    re.I,
)
RE_COST_TRAILING = re.compile(
    r"\$\s?([\d,]+(?:\.\d{2})?)[^.$]{0,40}?\b(?:total|estimated)\s+(?:project\s+)?costs?\b",
    re.I,
)
RE_ROW_COST = re.compile(
    r"right[-\s]of[-\s]way[^.$]{0,80}?\$\s?([\d,]+(?:\.\d{2})?)", re.I
)
RE_MITIG_RATIO = re.compile(
    r"mitigation\s+ratios?[^.]{0,40}?([\d.]+)(?:\s*(?:and|,|&)\s*([\d.]+))?", re.I
)
# Two patterns, not one: a context window cannot overlap itself, so findall on
# the wide pattern silently absorbs a second amount that falls inside the first
# window and undercounts. Count with the narrow one, quote with the wide one.
RE_DOLLAR_AMOUNT = re.compile(r"\$\s?[\d,]+(?:\.\d{2})?")
RE_DOLLAR_CONTEXT = re.compile(r".{0,70}\$\s?[\d,]+(?:\.\d{2})?.{0,50}")

# The demand signal that matters most for a bat-survey firm. A determination
# says whether bats were affected; THIS says whether someone was paid to go
# look. Detected only in non-boilerplate sentences: the FWS letter's "may
# require a presence/absence and/or habitat survey" would otherwise make every
# document in the corpus look like billable work.
SURVEY_TYPES = {
    "presence/absence": r"presence\s*/?\s*(?:and\s*/\s*or\s+)?absence\s+survey",
    "mist-net": r"mist[-\s]?net(?:ting)?(?:\s+surveys?)?",
    "acoustic": r"acoustic(?:\s+\w+){0,2}?\s+surve(?:y|ys|illance)",
    "emergence": r"emergence\s+survey",
    "habitat assessment": r"habitat\s+assessment",
}
SURVEY_TYPE_RE = {k: re.compile(v, re.I) for k, v in SURVEY_TYPES.items()}
RE_SURVEY_DONE = re.compile(
    r"\b(?:were|was|have\s+been|has\s+been)\s+(?:conducted|performed|completed)\b"
    r"|\bsurveys?\s+conducted\b",
    re.I,
)
RE_SURVEY_NEEDED = re.compile(
    r"\b(?:will\s+be\s+(?:required|conducted|performed)|is\s+required|are\s+required"
    r"|must\s+be\s+(?:conducted|performed))\b",
    re.I,
)

# Determination sentences, longest/most specific first. The order is load
# bearing: "likely to adversely affect" is a substring of "not likely to
# adversely affect", and determinations() takes the FIRST pattern that matches.
# With LAA ahead of NLAA every not-likely sentence resolved to LAA, inverting
# the determination that decides any_bat_LAA. Keep NLAA first.
DET_PATTERNS = [
    ("NLAA", re.compile(r"not\s+likely\s+to\s+adversely\s+affect", re.I)),
    ("LAA", re.compile(r"likely\s+to\s+adversely\s+affect", re.I)),
    ("NE", re.compile(r"\bno\s+effect\b", re.I)),
]

RANK = {"NE": 0, "NLAA": 1, "LAA": 2}

# The FWS/IPaC cover letter survives trim() -- it is not Nationwide Permit
# boilerplate -- and it is written in the same vocabulary as a real finding:
#   "If you determine that this project will have no effect on listed species
#    and their habitat in any way, then you have completed Section 7
#    consultation with the Service and may use this letter in your project file"
# That is a rule, not a finding about this project, and it produced the largest
# single category in the review queue (72 of 143 rows on the 75-document run).
#
# Filtered by sentence rather than by trimming the block: the letter appears
# before the narrative in some documents and after it in others, so cutting at
# a marker would silently discard real determinations in the first case.
#
# Deliberately narrow. Each pattern matches instructional or hypothetical
# phrasing that a determination about a specific project does not use. A real
# finding reads "ARDOT has determined..." or "the project will have no effect
# on the gray bat" -- neither is conditional, and neither addresses "you".
BOILERPLATE_SENTENCE_PATTERNS = [
    re.compile(r"\bif\s+you\s+(?:determine|have|are|would|wish)\b", re.I),
    re.compile(r"\bif\s+your\s+species\s+list\b", re.I),
    re.compile(r"\bshould\s+you\s+(?:determine|require|need|have)\b", re.I),
    re.compile(r"\byou\s+have\s+completed\s+section\s+7\b", re.I),
    re.compile(r"\buse\s+this\s+letter\s+in\s+your\s+project\s+file\b", re.I),
    re.compile(r"\bmay\s+require\s+(?:a\s+)?(?:presence\s*/\s*absence|habitat)\b", re.I),
    re.compile(r"\bthis\s+(?:species\s+)?list\s+(?:is|does\s+not)\b", re.I),
]


def is_boilerplate(sentence: str) -> bool:
    """True for FWS letter rules and hypotheticals, not findings about a project."""
    return any(p.search(sentence) for p in BOILERPLATE_SENTENCE_PATTERNS)

# How far back to look for the species a bare verdict sentence refers to.
# Deliberately short: ARDOT states the list and the finding as adjacent
# sentences, and a wider window starts sweeping in unrelated species.
LOOKBACK_SENTENCES = 3


@dataclass
class Record:
    job_id: str = ""
    doc_date: str = ""
    doc_year: str = ""
    county: str = ""
    county_fips: str = ""   # Census code, joined from data/ar_county_fips.csv
    routes: str = ""        # pipe-delimited highway numbers named in the document
    waterways: str = ""     # pipe-delimited named features crossed
    coordinates: str = ""   # only if the document actually states them
    coord_format: str = ""  # decimal_degrees / degrees_minutes_seconds / utm
    fap: str = ""
    ce_tier: str = ""
    project_length_mi: str = ""
    bats_listed: str = ""
    n_bats_listed: str = ""
    det_GRBA: str = ""
    det_IBAT: str = ""
    det_NLEB: str = ""
    det_OBEB: str = ""
    det_TCB: str = ""
    det_LBB: str = ""
    any_bat_LAA: str = ""
    det_source: str = ""  # direct / inferred / mixed -- how the verdicts were reached
    acres_cleared: str = ""
    mitigation_usd: str = ""
    mitigation_ratio: str = ""
    project_cost_usd: str = ""
    row_cost_usd: str = ""
    n_dollar_figures: str = ""  # how many $ amounts the document contains at all
    survey_types: str = ""      # pipe-delimited, boilerplate mentions excluded
    survey_status: str = ""     # conducted / required / mentioned
    pup_season_restriction: str = ""
    source_url: str = ""
    sha256: str = ""
    index_year: str = ""
    parse_status: str = ""
    parse_notes: str = field(default="")


def pdf_to_text(pdf: Path) -> str:
    txt = TXT_DIR / (pdf.stem + ".txt")
    if txt.exists():
        return txt.read_text(encoding="utf-8", errors="replace")
    TXT_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["pdftotext", "-layout", str(pdf), str(txt)],
        check=True,
        capture_output=True,
    )
    return txt.read_text(encoding="utf-8", errors="replace")


def trim(text: str) -> str:
    cut = len(text)
    for marker in BOILERPLATE_MARKERS:
        i = text.find(marker)
        if i != -1:
            cut = min(cut, i)
    return text[:cut]


def sentences(body: str) -> list[str]:
    flat = re.sub(r"\s+", " ", body)
    return re.split(r"(?<=[.;])\s+", flat)


@dataclass
class Determination:
    """Verdicts plus the evidence for each, so nothing is asserted unsourced."""

    verdicts: dict = field(default_factory=dict)   # code -> NE/NLAA/LAA
    source: dict = field(default_factory=dict)     # code -> direct/inferred
    used: list = field(default_factory=list)       # sentences giving direct verdicts
    mentions: dict = field(default_factory=dict)   # code -> sentences naming it
    inferred_from: dict = field(default_factory=dict)  # code -> bridging sentence
    orphans: list = field(default_factory=list)    # verdicts naming no species
    boilerplate_skipped: int = 0                   # FWS letter rules ignored


def _apply(det: Determination, code: str, verdict: str, how: str) -> None:
    """Record a verdict, preferring direct evidence and then the worse verdict."""
    prior = det.source.get(code)
    if prior == "direct" and how == "inferred":
        return  # never let a guess overwrite a sourced determination
    if prior == how and RANK[verdict] <= RANK[det.verdicts[code]]:
        return  # LAA found anywhere outranks a weaker verdict of the same kind
    det.verdicts[code] = verdict
    det.source[code] = how


def load_county_fips() -> dict:
    """county name -> FIPS, from the Census-derived file. Empty if absent."""
    path = ROOT / "data" / "ar_county_fips.csv"
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {r["county"]: r["county_fips"] for r in csv.DictReader(fh)}


COUNTY_FIPS = load_county_fips()


def county_of(body: str) -> tuple[str, list[str]]:
    """First named county that is actually in Arkansas, plus what was rejected.

    Returns ("", rejects) when nothing matches. A blank county is a visible
    gap; "Job Name" in a county column is an invisible error that travels.
    """
    rejected = []
    for m in RE_COUNTY.finditer(body):
        name = " ".join(m.group(1).split())          # collapse embedded newlines
        if name.startswith("St ") or name.startswith("St. "):
            name = "St. " + name.split(None, 1)[1]   # normalise "St Francis"
        if name in ARKANSAS_COUNTIES:
            return name, rejected
        if name not in rejected:
            rejected.append(name)
    return "", rejected


def coordinates_of(body: str) -> tuple[str, str]:
    """Any coordinates in the document, and which notation they were in."""
    if m := RE_DECIMAL_DEG.search(body):
        lon = m.group(2)
        lon = lon if lon.startswith("-") else f"-{lon}"
        return f"{m.group(1)},{lon}", "decimal_degrees"
    if m := RE_DMS.search(body):
        return m.group(0).strip(), "degrees_minutes_seconds"
    if m := RE_UTM.search(body):
        return f"{m.group(1)},{m.group(2)}", "utm"
    return "", ""


def survey_signals(body: str) -> tuple[list[str], str, list[str]]:
    """Survey types actually discussed for this project, and whether they happened.

    Returns (types, status, evidence sentences). Boilerplate sentences are
    excluded first: the FWS letter tells every applicant their project "may
    require a presence/absence and/or habitat survey", which is a rule about
    the program, not a fact about this job. Counting those would turn the whole
    corpus into apparent demand.

    Status is the strongest signal found -- "conducted" outranks "required",
    which outranks a bare mention -- because a completed survey is evidence
    someone was paid and a required one is only evidence someone will be.
    """
    found, evidence, status = {}, [], ""
    for sent in sentences(body):
        if is_boilerplate(sent):
            continue
        hits = [name for name, pat in SURVEY_TYPE_RE.items() if pat.search(sent)]
        if not hits:
            continue
        for name in hits:
            found[name] = True
        evidence.append(sent.strip())
        if RE_SURVEY_DONE.search(sent):
            status = "conducted"
        elif RE_SURVEY_NEEDED.search(sent) and status != "conducted":
            status = "required"
        elif not status:
            status = "mentioned"
    return sorted(found), status, evidence


def determinations(body: str) -> Determination:
    """Map each bat code to NE / NLAA / LAA, with the evidence for each.

    Two attribution paths, kept distinguishable because their reliability
    differs and the caller must be able to tell them apart:

    "direct" -- the species and the verdict appear in the same sentence.

    "inferred" -- the verdict sentence names no species, but a sentence just
    before it does. ARDOT routinely writes the species list and the finding as
    consecutive sentences ("...identified the Indiana Bat, northern long-eared
    bat... ARDOT has determined the project will have no effect on these
    species."), so requiring co-occurrence in one sentence resolved almost
    nothing: 1 of 19 records in the first live sample. Every inferred verdict is
    flagged and queued for review -- it is a lead, not a finding.

    Verdicts matching nothing at all land in `orphans`, which is the diagnostic
    for whatever structure this rule still fails to capture.
    """
    det = Determination()
    sents = sentences(body)
    species_at = [
        [code for name, code in BATS.items() if name in s.lower()] for s in sents
    ]

    for i, sent in enumerate(sents):
        # FWS letter rules carry determination vocabulary but assert nothing
        # about this project. Skipped before they can set a verdict, seed an
        # inferred attribution, or pad the orphan queue. Counted so the effect
        # of this filter stays visible rather than becoming invisible cleanup.
        if is_boilerplate(sent):
            det.boilerplate_skipped += 1
            continue
        clean = sent.strip()
        for code in species_at[i]:
            det.mentions.setdefault(code, []).append(clean)

        verdict = next((v for v, pat in DET_PATTERNS if pat.search(sent)), None)
        if not verdict:
            continue

        if species_at[i]:
            det.used.append(clean)
            for code in species_at[i]:
                _apply(det, code, verdict, "direct")
            continue

        back = [c for j in range(max(0, i - LOOKBACK_SENTENCES), i) for c in species_at[j]]
        if not back:
            det.orphans.append(clean)
            continue
        for code in dict.fromkeys(back):
            _apply(det, code, verdict, "inferred")
            det.inferred_from.setdefault(code, clean)
    return det


def parse_one(pdf: Path, meta: dict) -> tuple[Record, list[dict]]:
    raw = pdf_to_text(pdf)
    body = trim(raw)
    rec = Record(
        job_id=meta["job_id"],
        source_url=meta["source_url"],
        sha256=meta.get("sha256", ""),
        index_year=meta.get("index_year", ""),
    )
    notes = []

    if len(body) < 500:
        rec.parse_status = "empty-or-scanned"
        rec.parse_notes = f"body chars={len(body)}"
        return rec, [{"job_id": rec.job_id, "reason": "empty", "sentence": ""}]

    if m := RE_DATE.search(body):
        rec.doc_date = f"{m.group(1)} {m.group(2)}, {m.group(3)}"
        rec.doc_year = m.group(3)
    elif m := RE_DATE_MY.search(body):
        # doc_year is what the annual series needs, and it is unambiguous here.
        # doc_date carries the lower precision plainly rather than inventing a day.
        rec.doc_date = f"{m.group(1)} {m.group(2)}"
        rec.doc_year = m.group(2)
    else:
        notes.append("no-date")

    rec.county, county_rejects = county_of(body)
    if rec.county:
        rec.county_fips = COUNTY_FIPS.get(rec.county, "")
        if not rec.county_fips:
            notes.append("no-county-fips")
    else:
        notes.append("no-county")
        if county_rejects:
            notes.append(f"county-rejected:{','.join(county_rejects[:3])}")

    rec.routes = "|".join(dict.fromkeys(RE_ROUTE.findall(body)))
    rec.waterways = "|".join(
        dict.fromkeys(" ".join(w.split()) for w in RE_WATERWAY.findall(body))
    )[:200]
    rec.coordinates, rec.coord_format = coordinates_of(body)

    if m := RE_FAP.search(body):
        rec.fap = " ".join(m.group(1).split())
    if m := RE_TIER.search(body):
        rec.ce_tier = m.group(1)
    if m := RE_LENGTH.search(body):
        rec.project_length_mi = m.group(1)
    if m := RE_ACRES.search(body):
        rec.acres_cleared = m.group(1)
    if m := RE_MITIG.search(body):
        rec.mitigation_usd = m.group(1).replace(",", "")
    if m := RE_PUPSEASON.search(body):
        rec.pup_season_restriction = f"{m.group(1)}-{m.group(2)}"

    # Financial fields. Whether ARDOT states a project cost in a NEPA document
    # at all is exactly what the first full run answers -- n_dollar_figures and
    # the queued dollar-context rows report what is really there.
    if m := (RE_PROJECT_COST.search(body) or RE_COST_TRAILING.search(body)):
        rec.project_cost_usd = m.group(1).replace(",", "")
    if m := RE_ROW_COST.search(body):
        rec.row_cost_usd = m.group(1).replace(",", "")
    if m := RE_MITIG_RATIO.search(body):
        rec.mitigation_ratio = "|".join(g for g in m.groups() if g)
    rec.n_dollar_figures = str(len(RE_DOLLAR_AMOUNT.findall(body)))
    dollars = RE_DOLLAR_CONTEXT.findall(body)

    survey_types, survey_status, survey_evidence = survey_signals(body)
    rec.survey_types = "|".join(survey_types)
    rec.survey_status = survey_status

    low = body.lower()
    listed = sorted({code for name, code in BATS.items() if name in low})
    rec.bats_listed = "|".join(listed)
    rec.n_bats_listed = str(len(listed))

    det = determinations(body)
    dets = det.verdicts
    for code, verdict in dets.items():
        setattr(rec, f"det_{code}", verdict)
    rec.any_bat_LAA = str(int("LAA" in dets.values()))
    kinds = set(det.source.values())
    rec.det_source = "mixed" if len(kinds) > 1 else (kinds.pop() if kinds else "")

    queue = []
    undetermined = [c for c in listed if c not in dets]
    if undetermined:
        notes.append(f"undetermined:{','.join(undetermined)}")
        for code in undetermined:
            # Hand over the sentences naming THIS species, whichever way they
            # fell. Falling back to `used` would be empty by construction here,
            # and an empty sentence column defeats the point of the queue.
            src = det.mentions.get(code)
            queue.append(
                {
                    "job_id": rec.job_id,
                    "reason": f"no-determination-{code}",
                    "sentence": (
                        " | ".join(src)[:1500]
                        if src
                        else "species named in document but in no parsed sentence"
                    ),
                }
            )

    # The validation gate requires every any_bat_LAA row to be checked by hand.
    # That is impossible from the CSV alone if the sentence that produced the
    # finding is nowhere in the output, so carry it. LAA rows are rare by
    # design, so this does not bloat the queue.
    if rec.any_bat_LAA == "1":
        laa = sorted(c for c, v in dets.items() if v == "LAA")
        queue.append(
            {
                "job_id": rec.job_id,
                "reason": f"VERIFY-LAA-{','.join(laa)}-{rec.det_source}",
                "sentence": (
                    " | ".join(det.used)[:1500]
                    if det.used
                    else "no direct sentence -- every LAA here was inferred, see above"
                ),
            }
        )

    # Survey evidence is the demand signal the business case rests on, so it is
    # queued with its sentences rather than asserted as a bare column value.
    if survey_evidence:
        queue.append(
            {
                "job_id": rec.job_id,
                "reason": f"survey-{survey_status}-{'|'.join(survey_types)}",
                "sentence": " | ".join(survey_evidence)[:1500],
            }
        )

    # Every dollar figure with its surrounding words. This is a diagnostic, not
    # a finding: it exists so one full run establishes what ARDOT actually
    # writes about money, instead of us inferring it from two documents.
    if dollars:
        queue.append(
            {
                "job_id": rec.job_id,
                "reason": f"dollar-context-{len(dollars)}",
                "sentence": " || ".join(d.strip() for d in dollars[:6])[:1500],
            }
        )

    if det.boilerplate_skipped:
        notes.append(f"fws-boilerplate-skipped:{det.boilerplate_skipped}")

    # An inferred verdict is a lead, not a finding. Surface every one.
    guessed = sorted(c for c, how in det.source.items() if how == "inferred")
    if guessed:
        notes.append(f"inferred:{','.join(guessed)}")
        for code in guessed:
            queue.append(
                {
                    "job_id": rec.job_id,
                    "reason": f"inferred-determination-{code}-{dets[code]}",
                    "sentence": det.inferred_from.get(code, "")[:1500],
                }
            )

    # Verdicts that named no species and had none nearby. These are the shapes
    # the proximity rule still cannot reach -- the diagnostic for what is next.
    if det.orphans:
        notes.append(f"orphan-verdicts:{len(det.orphans)}")
        queue.append(
            {
                "job_id": rec.job_id,
                "reason": "verdict-without-species",
                "sentence": " | ".join(det.orphans)[:1500],
            }
        )
    if rec.any_bat_LAA == "1" and not rec.acres_cleared:
        notes.append("LAA-without-acres")
        queue.append(
            {"job_id": rec.job_id, "reason": "LAA-without-acres", "sentence": ""}
        )

    rec.parse_notes = ";".join(notes)
    rec.parse_status = "review" if notes else "ok"
    return rec, queue


def main() -> None:
    with MANIFEST.open(newline="", encoding="utf-8") as fh:
        manifest = list(csv.DictReader(fh))

    records, queue = [], []
    for meta in manifest:
        import hashlib

        tag = hashlib.sha256(meta["source_url"].encode()).hexdigest()[:8]
        pdf = PDF_DIR / f"{meta['job_id']}__{tag}.pdf"
        if not pdf.exists():
            continue
        rec, q = parse_one(pdf, meta)
        records.append(asdict(rec))
        queue.extend(q)

    # Dedupe on (job_id, sha256). The same document posted under two index
    # years is one project, and that still collapses here.
    #
    # What must NOT collapse is two DIFFERENT jobs served identical content --
    # ARDOT does this: 012380 and 012381 have distinct, correct-looking hrefs
    # and id_url_mismatch=0 but byte-identical PDFs. Keying on sha256 alone
    # dropped 012381 with no row and no flag. The countable unit here is the
    # bat-triggering project-year, so a silent drop undercounts the headline
    # number. Keep both, flag the pair, and let a human decide whether it is a
    # shared document or a bad link.
    by_sha: dict[str, set[str]] = {}
    for r in records:
        if r["sha256"]:
            by_sha.setdefault(r["sha256"], set()).add(r["job_id"])

    seen, unique = set(), []
    for r in records:
        key = (r["job_id"], r["sha256"] or r["source_url"])
        if key in seen:
            continue
        seen.add(key)
        shared = sorted(by_sha.get(r["sha256"], set()) - {r["job_id"]})
        if shared:
            r["parse_notes"] = ";".join(
                filter(None, [r["parse_notes"], f"shared-pdf:{'|'.join(shared)}"])
            )
            r["parse_status"] = "review"
            queue.append(
                {
                    "job_id": r["job_id"],
                    "reason": "shared-pdf",
                    "sentence": (
                        f"byte-identical PDF also served for job(s) "
                        f"{', '.join(shared)} at a different URL -- confirm whether "
                        f"these are separate projects or a bad index link"
                    ),
                }
            )
        unique.append(r)

    RECORDS.parent.mkdir(parents=True, exist_ok=True)
    with RECORDS.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(Record().__dict__.keys()))
        w.writeheader()
        w.writerows(unique)

    with REVIEW.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["job_id", "reason", "sentence"])
        w.writeheader()
        w.writerows(queue)

    ok = sum(1 for r in unique if r["parse_status"] == "ok")
    laa = sum(1 for r in unique if r["any_bat_LAA"] == "1")
    print(
        f"parsed={len(records)} unique={len(unique)} clean={ok} "
        f"review={len(unique) - ok} any_bat_LAA={laa} queue_rows={len(queue)}"
    )


if __name__ == "__main__":
    main()
