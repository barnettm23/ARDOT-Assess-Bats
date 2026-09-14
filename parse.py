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
RE_COUNTY = re.compile(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+County\b")
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


@dataclass
class Record:
    job_id: str = ""
    doc_date: str = ""
    doc_year: str = ""
    county: str = ""
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
    acres_cleared: str = ""
    mitigation_usd: str = ""
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


def determinations(body: str) -> tuple[dict, list[str], dict]:
    """Map each bat code to NE / NLAA / LAA.

    Returns the verdicts, the sentences that produced them, and every sentence
    mentioning each species whether or not a verdict matched. That third value
    is what a reviewer needs: a species reaches the queue precisely because no
    verdict was resolved, so the sentences that did match are the empty set.
    """
    out, used, mentions = {}, [], {}
    for sent in sentences(body):
        low = sent.lower()
        present = [code for name, code in BATS.items() if name in low]
        if not present:
            continue
        clean = sent.strip()
        for code in present:
            mentions.setdefault(code, []).append(clean)
        verdict = next((v for v, pat in DET_PATTERNS if pat.search(sent)), None)
        if not verdict:
            continue
        used.append(clean)
        for code in present:
            # LAA wins over a weaker verdict found elsewhere in the document.
            rank = {"NE": 0, "NLAA": 1, "LAA": 2}
            if code not in out or rank[verdict] > rank[out[code]]:
                out[code] = verdict
    return out, used, mentions


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

    if m := RE_COUNTY.search(body):
        rec.county = m.group(1)
    else:
        notes.append("no-county")

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

    low = body.lower()
    listed = sorted({code for name, code in BATS.items() if name in low})
    rec.bats_listed = "|".join(listed)
    rec.n_bats_listed = str(len(listed))

    dets, used, mentions = determinations(body)
    for code, verdict in dets.items():
        setattr(rec, f"det_{code}", verdict)
    rec.any_bat_LAA = str(int("LAA" in dets.values()))

    queue = []
    undetermined = [c for c in listed if c not in dets]
    if undetermined:
        notes.append(f"undetermined:{','.join(undetermined)}")
        for code in undetermined:
            # Hand over the sentences naming THIS species, whichever way they
            # fell. Falling back to `used` would be empty by construction here,
            # and an empty sentence column defeats the point of the queue.
            src = mentions.get(code)
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

    # Dedupe on sha256: the same document posted under two index years is one
    # project, not two. This is the single biggest source of double-counting.
    seen, unique = set(), []
    for r in records:
        key = r["sha256"] or (r["job_id"], r["source_url"])
        if key in seen:
            continue
        seen.add(key)
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
