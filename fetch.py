"""Stage 2 -- fetch.

Download every PDF in the manifest into cache/pdf/, skipping anything already
held. Records sha256 so a silently replaced document re-enters the pipeline.

Be polite: one request per second, identify yourself, retry once on 5xx.
ARDOT is a small state agency, not a CDN.
"""

import csv
import hashlib
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "data" / "manifest.csv"
PDF_DIR = ROOT / "cache" / "pdf"
USER_AGENT = "OzarkBioacoustics-research/0.1 (contact: you@example.com)"
DELAY_SECONDS = 1.0


def local_path(row: dict) -> Path:
    """Name cached files by job_id + a URL hash, so two different files for the
    same job (re-evaluations, corrections) never collide."""
    tag = hashlib.sha256(row["source_url"].encode()).hexdigest()[:8]
    return PDF_DIR / f"{row['job_id']}__{tag}.pdf"


def fetch_one(row: dict) -> dict:
    dest = local_path(row)
    if dest.exists() and row.get("sha256"):
        return row  # already held and hashed
    for attempt in (1, 2):
        try:
            resp = requests.get(
                row["source_url"], headers={"User-Agent": USER_AGENT}, timeout=120
            )
        except requests.RequestException as exc:
            row["fetch_note"] = f"error: {type(exc).__name__}"
            time.sleep(3)
            continue
        row["http_status"] = resp.status_code
        if resp.status_code == 200 and resp.content[:4] == b"%PDF":
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            row["sha256"] = hashlib.sha256(resp.content).hexdigest()
            row["last_fetched"] = time.strftime("%Y-%m-%d")
            row["fetch_note"] = ""
            return row
        if resp.status_code == 200:
            row["fetch_note"] = "not-a-pdf"
            return row
        if resp.status_code < 500 or attempt == 2:
            row["fetch_note"] = f"http-{resp.status_code}"
            return row
        time.sleep(5)
    return row


def main(limit: int | None = None) -> None:
    with MANIFEST.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields, rows = reader.fieldnames, list(reader)

    todo = [r for r in rows if not (local_path(r).exists() and r.get("sha256"))]
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} to fetch of {len(rows)} in manifest")

    for i, row in enumerate(todo, 1):
        fetch_one(row)
        note = row.get("fetch_note") or "ok"
        print(f"[{i}/{len(todo)}] {row['job_id']} {note}", flush=True)
        time.sleep(DELAY_SECONDS)

    with MANIFEST.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    failed = [r for r in rows if r.get("fetch_note")]
    print(f"done. {len(failed)} rows carry a fetch_note -- review those.")


if __name__ == "__main__":
    main(limit=int(sys.argv[1]) if len(sys.argv) > 1 else None)
