"""Stage 1 -- harvest.

Scrape the ARDOT environmental-documents index into data/manifest.csv.

The index groups PDF links under year headings (2026, 2025, ...). Those headings
are POSTING years, not document dates, and the same job appears under more than
one year. We record the heading as `index_year` for provenance only. The
authoritative date comes from inside the PDF (see parse.py).

Filenames are not constructible from job IDs -- observed real examples include
110751_env.pdf, A10031env.pdf, 020738env-1.pdf, 020628nepa.pdf and
A000065env.pdf (an ARDOT typo for job A00065). Always use the href.
"""

import csv
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

INDEX_URL = (
    "https://ardot.gov/divisions/program-management/construction-contract-development/"
    "construction-contractors/additional-project-information-2/environmental-documents/"
)
USER_AGENT = "OzarkBioacoustics-research/0.1 (contact: michael@seismicagency.com)"
ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "data" / "manifest.csv"

YEAR_RE = re.compile(r"^(19|20)\d{2}$")
JOB_RE = re.compile(r"^[A-Z0-9]{6}$")


def stem_from_url(url: str) -> str:
    """Filename without extension, for mismatch checking."""
    return url.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def looks_mismatched(job_id: str, url: str) -> bool:
    """Flag anchors whose href does not contain the job ID.

    Real cases in the live index: job 012542 -> 012550env.pdf,
    job A70020 -> A70022env.pdf, job 012428 -> 012430env.pdf.
    ARDOT typos like A00065 -> A000065env.pdf also land here; those are
    harmless but worth a human glance.
    """
    return job_id.lower() not in stem_from_url(url).lower()


def harvest(index_url: str = INDEX_URL) -> list[dict]:
    resp = requests.get(index_url, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # The document list lives after the "Environmental Documents" heading.
    # Walk the document in order, tracking the most recent year heading.
    rows, current_year = [], None
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "p", "strong", "b", "a"]):
        text = el.get_text(strip=True)
        if YEAR_RE.match(text):
            current_year = int(text)
            continue
        if el.name != "a":
            continue
        href = el.get("href", "")
        if not href.lower().endswith(".pdf"):
            continue
        if "/ardot/" not in href:
            continue
        if current_year is None:
            continue
        job_id = text.split()[0].upper() if text else ""
        if not JOB_RE.match(job_id):
            # Labels like "070435 rev 2/5/2021" still start with a job ID.
            continue
        rows.append(
            {
                "job_id": job_id,
                "index_year": current_year,
                "source_url": href,
                "filename": href.rsplit("/", 1)[-1],
                "id_url_mismatch": int(looks_mismatched(job_id, href)),
            }
        )

    # Dedupe on (job_id, source_url). A job legitimately appears under several
    # index years pointing at the same file; keep the earliest heading.
    seen, deduped = {}, []
    for r in rows:
        key = (r["job_id"], r["source_url"])
        if key in seen:
            seen[key]["index_year"] = min(seen[key]["index_year"], r["index_year"])
            continue
        seen[key] = r
        deduped.append(r)
    return deduped


def merge_into_manifest(rows: list[dict]) -> tuple[int, int]:
    """Additive merge. Never drops a row we have seen before."""
    today = time.strftime("%Y-%m-%d")
    existing = {}
    if MANIFEST.exists():
        with MANIFEST.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                existing[(r["job_id"], r["source_url"])] = r

    added = 0
    for r in rows:
        key = (r["job_id"], r["source_url"])
        if key in existing:
            existing[key]["index_year"] = r["index_year"]
            continue
        r.update(
            {
                "first_seen": today,
                "last_fetched": "",
                "sha256": "",
                "http_status": "",
                "fetch_note": "",
            }
        )
        existing[key] = r
        added += 1

    fields = [
        "job_id",
        "index_year",
        "source_url",
        "filename",
        "id_url_mismatch",
        "first_seen",
        "last_fetched",
        "sha256",
        "http_status",
        "fetch_note",
    ]
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in sorted(existing.values(), key=lambda x: (x["index_year"], x["job_id"])):
            w.writerow(r)
    return added, len(existing)


if __name__ == "__main__":
    rows = harvest()
    if len(rows) < 300:
        print(
            f"ABORT: only {len(rows)} links parsed. The index markup likely changed. "
            "Inspect the page before trusting this run.",
            file=sys.stderr,
        )
        sys.exit(1)
    added, total = merge_into_manifest(rows)
    mismatches = sum(r["id_url_mismatch"] for r in rows)
    print(f"parsed={len(rows)} new={added} total={total} id_url_mismatch={mismatches}")
