"""Build a review workbook from the parsed data.

`python export_review.py` -> data/review_workbook.xlsx

Four sheets:

  Summary       counts, computed by formula so they follow the data
  Records       every parsed record, filterable
  Review queue  every flagged row with its source sentence
  Hand-code 15  the validation gate from CLAUDE.md, set up to fill in

The hand-code sheet is the point of this file. CLAUDE.md requires 15 documents
hand-coded against records.csv at >=90% field agreement before anyone trusts a
number, and that is tedious to set up by hand every time the sample changes.
Yellow cells are yours to fill; agreement is computed live as you go.
"""

import csv
import random
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

ROOT = Path(__file__).resolve().parent
RECORDS = ROOT / "data" / "records.csv"
REVIEW = ROOT / "data" / "review_queue.csv"
OUT = ROOT / "data" / "review_workbook.xlsx"

FONT = "Arial"
HEAD_FILL = PatternFill("solid", fgColor="1F3864")
FILL_ME = PatternFill("solid", fgColor="FFFF00")
NOTE_FILL = PatternFill("solid", fgColor="FFF2CC")

# Fields a human can check against the PDF in under a minute each.
HAND_FIELDS = [
    "doc_year",
    "county",
    "ce_tier",
    "bats_listed",
    "any_bat_LAA",
    "acres_cleared",
    "mitigation_usd",
]
SAMPLE_N = 15
SEED = 20260914  # fixed so the same 15 documents come back on a re-run

# Written as numbers rather than text so the sheet sorts, filters and
# aggregates correctly -- MIN/MAX over text-formatted years returns 0.
NUMERIC = {
    "doc_year",
    "ce_tier",
    "project_length_mi",
    "n_bats_listed",
    "any_bat_LAA",
    "acres_cleared",
    "mitigation_usd",
    "project_cost_usd",
    "row_cost_usd",
    "n_dollar_figures",
}
CURRENCY = {"mitigation_usd", "project_cost_usd", "row_cost_usd"}


def typed(field: str, value: str):
    if field not in NUMERIC or value in ("", None):
        return value
    try:
        return float(value) if "." in value else int(value)
    except (TypeError, ValueError):
        return value  # leave anything unparseable visible rather than dropping it


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def style_header(ws, row: int = 1) -> None:
    for cell in ws[row]:
        if cell.value is not None:
            cell.font = Font(name=FONT, bold=True, color="FFFFFF")
            cell.fill = HEAD_FILL
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def autosize(ws, limit: int = 52) -> None:
    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        longest = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[letter].width = min(max(longest + 2, 9), limit)


def sheet_records(wb, records: list[dict]) -> None:
    ws = wb.create_sheet("Records")
    if not records:
        ws["A1"] = "no records yet -- run the pipeline first"
        return
    cols = list(records[0].keys())
    ws.append(cols)
    for r in records:
        ws.append([typed(c, r.get(c, "")) for c in cols])
    money_cols = {i for i, c in enumerate(cols, start=1) if c in CURRENCY}
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name=FONT)
            if cell.column in money_cols and isinstance(cell.value, (int, float)):
                cell.number_format = "$#,##0"
    style_header(ws)
    autosize(ws)
    ref = f"A1:{get_column_letter(len(cols))}{len(records) + 1}"
    table = Table(displayName="Records", ref=ref)
    table.tableStyleInfo = TableStyleInfo(name="TableStyleLight9", showRowStripes=True)
    ws.add_table(table)


def sheet_queue(wb, queue: list[dict]) -> None:
    ws = wb.create_sheet("Review queue")
    ws.append(["job_id", "reason", "sentence"])
    for q in queue:
        ws.append([q.get("job_id", ""), q.get("reason", ""), q.get("sentence", "")])
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name=FONT)
        row[2].alignment = Alignment(wrap_text=True, vertical="top")
    style_header(ws)
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 34
    ws.column_dimensions["C"].width = 110


def sheet_handcode(wb, records: list[dict]) -> None:
    """The validation gate, pre-built. Yellow cells are for the reviewer."""
    ws = wb.create_sheet("Hand-code 15")

    ws["A1"] = "Validation gate -- CLAUDE.md requires this before any number is trusted"
    ws["A1"].font = Font(name=FONT, bold=True, size=13)
    ws["A2"] = (
        "Open each source_url, read the PDF, and type what it actually says into the "
        "YELLOW cells. Leave a cell blank if you did not check that field -- blanks are "
        "excluded from the score rather than counted as disagreement. Agreement updates live."
    )
    ws["A2"].font = Font(name=FONT, italic=True)
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("A2:R2")
    ws.row_dimensions[2].height = 30

    head = 4
    cols = ["job_id", "source_url"]
    cols += [f"parsed: {f}" for f in HAND_FIELDS]
    cols += [f"ACTUAL: {f}" for f in HAND_FIELDS]
    cols += ["fields checked", "fields agreeing"]
    ws.append([])  # row 3 spacer
    for i, name in enumerate(cols, start=1):
        ws.cell(row=head, column=i, value=name)

    n_parsed = len(HAND_FIELDS)
    first_parsed = 3
    first_actual = first_parsed + n_parsed
    checked_col = first_actual + n_parsed
    agree_col = checked_col + 1

    rng = random.Random(SEED)
    pool = [r for r in records if r.get("job_id")]
    sample = rng.sample(pool, min(SAMPLE_N, len(pool)))
    sample.sort(key=lambda r: r["job_id"])

    # One example row, so the expected format is unambiguous. Excluded from scoring.
    ex = head + 1
    ws.cell(row=ex, column=1, value="EXAMPLE")
    ws.cell(row=ex, column=2, value="(not scored -- delete or ignore)")
    example_vals = ["2022", "Izard", "3", "GRBA|IBAT", "1", "0.47", "3909"]
    for i, v in enumerate(example_vals):
        ws.cell(row=ex, column=first_parsed + i, value=v)
        ws.cell(row=ex, column=first_actual + i, value=v)
    for c in range(1, agree_col + 1):
        ws.cell(row=ex, column=c).font = Font(name=FONT, italic=True, color="808080")
        ws.cell(row=ex, column=c).fill = NOTE_FILL

    start = ex + 1
    for n, rec in enumerate(sample):
        r = start + n
        ws.cell(row=r, column=1, value=rec["job_id"])
        ws.cell(row=r, column=2, value=rec.get("source_url", ""))
        for i, f in enumerate(HAND_FIELDS):
            ws.cell(row=r, column=first_parsed + i, value=rec.get(f, ""))
            blank = ws.cell(row=r, column=first_actual + i)
            blank.fill = FILL_ME
        a1 = f"{get_column_letter(first_actual)}{r}"
        a2 = f"{get_column_letter(first_actual + n_parsed - 1)}{r}"
        p1 = f"{get_column_letter(first_parsed)}{r}"
        p2 = f"{get_column_letter(first_parsed + n_parsed - 1)}{r}"
        # Compare as text on both sides. Excel stores a typed "2022" as the
        # number 2022, and 2022="2022" is FALSE -- without this, a reviewer who
        # types the right answer gets scored as disagreeing.
        ws.cell(row=r, column=checked_col, value=f'=SUMPRODUCT(--({a1}:{a2}<>""))')
        ws.cell(
            row=r,
            column=agree_col,
            value=(
                f'=SUMPRODUCT(--({a1}:{a2}<>""),'
                f'--(TRIM({a1}:{a2}&"")=TRIM({p1}:{p2}&"")))'
            ),
        )
        for c in range(1, agree_col + 1):
            cell = ws.cell(row=r, column=c)
            if not cell.font.b:
                cell.font = Font(name=FONT)

    last = start + len(sample) - 1
    tot = last + 2
    ck = get_column_letter(checked_col)
    ag = get_column_letter(agree_col)
    ws.cell(row=tot, column=1, value="TOTAL")
    ws.cell(row=tot, column=checked_col, value=f"=SUM({ck}{start}:{ck}{last})")
    ws.cell(row=tot, column=agree_col, value=f"=SUM({ag}{start}:{ag}{last})")

    ws.cell(row=tot + 1, column=1, value="Field agreement")
    ws.cell(
        row=tot + 1,
        column=agree_col,
        value=f"=IFERROR({ag}{tot}/{ck}{tot},\"\")",
    )
    ws.cell(row=tot + 1, column=agree_col).number_format = "0.0%"

    ws.cell(row=tot + 2, column=1, value="Gate (>=90%)")
    ws.cell(
        row=tot + 2,
        column=agree_col,
        value=f'=IF({ck}{tot}=0,"not started",IF({ag}{tot}/{ck}{tot}>=0.9,"PASS","FAIL"))',
    )
    for r in (tot, tot + 1, tot + 2):
        for c in (1, checked_col, agree_col):
            ws.cell(row=r, column=c).font = Font(name=FONT, bold=True)

    style_header(ws, row=head)
    autosize(ws, limit=30)
    ws.column_dimensions["B"].width = 46
    ws.freeze_panes = ws.cell(row=head + 1, column=3)


def sheet_financials(wb, records: list[dict]) -> None:
    """Money and demand signals, with the traps stated before the numbers."""
    ws = wb.create_sheet("Financials")
    cols = list(records[0].keys()) if records else []
    last = len(records) + 1

    def col(name: str) -> str:
        return get_column_letter(cols.index(name) + 1) if name in cols else None

    ws["A1"] = "Financial and demand signals"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)

    ws["A2"] = (
        "mitigation_usd is an in-lieu fee paid to a conservation fund. No consultant "
        "earns it. It is an intensity signal, never market size, and must not be summed "
        "into a revenue figure. project_cost_usd is the construction project's own cost, "
        "not survey spend. The revenue-relevant column is survey_status."
    )
    ws["A2"].font = Font(name=FONT, italic=True, color="9C0006")
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("A2:F2")
    ws.row_dimensions[2].height = 46

    r = 4
    ws.cell(row=r, column=1, value="Coverage — how much of the corpus states each field")
    ws.cell(row=r, column=1).font = Font(name=FONT, bold=True)
    r += 1
    ws.cell(row=r, column=2, value="populated").font = Font(name=FONT, bold=True)
    ws.cell(row=r, column=3, value="of records").font = Font(name=FONT, bold=True)
    ws.cell(row=r, column=4, value="sum (see caveat)").font = Font(name=FONT, bold=True)
    r += 1

    for field_name, summable in [
        ("project_cost_usd", True),
        ("row_cost_usd", True),
        ("mitigation_usd", False),
        ("mitigation_ratio", False),
        ("acres_cleared", True),
        ("n_dollar_figures", False),
    ]:
        c = col(field_name)
        ws.cell(row=r, column=1, value=field_name).font = Font(name=FONT)
        if c:
            rng = f"Records!{c}2:{c}{last}"
            ws.cell(row=r, column=2, value=f'=COUNTIF({rng},"<>")')
            ws.cell(row=r, column=3, value=f"=COUNTA(Records!A2:A{last})")
            if summable:
                cell = ws.cell(row=r, column=4, value=f"=SUM({rng})")
                cell.number_format = "$#,##0" if field_name.endswith("usd") else "0.00"
            else:
                ws.cell(row=r, column=4, value="— do not sum")
                ws.cell(row=r, column=4).font = Font(name=FONT, italic=True, color="9C0006")
        for cc in range(2, 5):
            if not ws.cell(row=r, column=cc).font.i:
                ws.cell(row=r, column=cc).font = Font(name=FONT)
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="Survey demand — the revenue-relevant signal")
    ws.cell(row=r, column=1).font = Font(name=FONT, bold=True)
    r += 1
    sc = col("survey_status")
    for label, value in [
        ("surveys conducted", "conducted"),
        ("surveys required", "required"),
        ("survey mentioned only", "mentioned"),
        ("no survey signal", ""),
    ]:
        ws.cell(row=r, column=1, value=label).font = Font(name=FONT)
        if sc:
            rng = f"Records!{sc}2:{sc}{last}"
            formula = f'=COUNTIF({rng},"{value}")' if value else f'=COUNTIF({rng},"")'
            ws.cell(row=r, column=2, value=formula).font = Font(name=FONT)
        r += 1

    ws.cell(row=r, column=1, value=(
        "FWS boilerplate advising that a project 'may require a presence/absence "
        "survey' is excluded — it is a rule for every applicant, not demand from "
        "this job. Counting it would turn the whole corpus into apparent revenue."
    )).font = Font(name=FONT, italic=True)
    ws.cell(row=r, column=1).alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
    ws.row_dimensions[r].height = 40

    ws.column_dimensions["A"].width = 46
    for c in "BCDEF":
        ws.column_dimensions[c].width = 17


def sheet_summary(wb, records: list[dict], queue: list[dict]) -> None:
    ws = wb.create_sheet("Summary", 0)
    ws["A1"] = "ARDOT bat determinations -- parse summary"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)

    n = len(records)
    last = n + 1  # Records sheet data rows are 2..n+1
    cols = list(records[0].keys()) if records else []

    def col(name: str) -> str:
        return get_column_letter(cols.index(name) + 1) if name in cols else "A"

    rows = [
        ("Records parsed", f"=COUNTA(Records!{col('job_id')}2:{col('job_id')}{last})"),
        ("Review queue rows", len(queue)),
        (None, None),
        ("Resolved a verdict",
         f'=COUNTIF(Records!{col("det_source")}2:{col("det_source")}{last},"direct")'
         f'+COUNTIF(Records!{col("det_source")}2:{col("det_source")}{last},"inferred")'
         f'+COUNTIF(Records!{col("det_source")}2:{col("det_source")}{last},"mixed")'),
        ("  of which direct only",
         f'=COUNTIF(Records!{col("det_source")}2:{col("det_source")}{last},"direct")'),
        ("  of which inferred only",
         f'=COUNTIF(Records!{col("det_source")}2:{col("det_source")}{last},"inferred")'),
        ("  of which mixed",
         f'=COUNTIF(Records!{col("det_source")}2:{col("det_source")}{last},"mixed")'),
        ("No verdict resolved",
         f'=COUNTIF(Records!{col("det_source")}2:{col("det_source")}{last},"")'),
        (None, None),
        ("any_bat_LAA = 1",
         f'=COUNTIF(Records!{col("any_bat_LAA")}2:{col("any_bat_LAA")}{last},1)'),
        ("parse_status = ok",
         f'=COUNTIF(Records!{col("parse_status")}2:{col("parse_status")}{last},"ok")'),
        ("parse_status = review",
         f'=COUNTIF(Records!{col("parse_status")}2:{col("parse_status")}{last},"review")'),
        (None, None),
        ("Earliest doc_year",
         f'=MIN(Records!{col("doc_year")}2:{col("doc_year")}{last})'),
        ("Latest doc_year",
         f'=MAX(Records!{col("doc_year")}2:{col("doc_year")}{last})'),
    ]
    r = 3
    for label, value in rows:
        if label is None:
            r += 1
            continue
        ws.cell(row=r, column=1, value=label).font = Font(
            name=FONT, bold=not label.startswith("  ")
        )
        ws.cell(row=r, column=2, value=value).font = Font(name=FONT)
        r += 1

    warn = r + 1
    ws.cell(row=warn, column=1, value="Read this before quoting any figure above")
    ws.cell(row=warn, column=1).font = Font(name=FONT, bold=True, color="9C0006")
    for i, line in enumerate(
        [
            "any_bat_LAA = 0 does NOT mean no adverse effect was found. Where det_source "
            "is blank, extraction resolved nothing and the true value is unknown.",
            "det_source = inferred means the verdict sentence named no species and was "
            "attributed to a species named up to 3 sentences earlier. A lead, not a "
            "finding. Hand-check before use.",
            "index_year is the posting year, not the document date. Use doc_year for any "
            "annual series.",
            "mitigation_usd is an in-lieu fee paid to a conservation fund. It is not "
            "consultant revenue and must not be summed as market size.",
        ],
        start=1,
    ):
        c = ws.cell(row=warn + i, column=1, value=f"{i}. {line}")
        c.font = Font(name=FONT)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=warn + i, start_column=1, end_row=warn + i, end_column=6)
        ws.row_dimensions[warn + i].height = 28

    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 16


def main() -> None:
    records, queue = read(RECORDS), read(REVIEW)
    wb = Workbook()
    wb.remove(wb.active)
    sheet_records(wb, records)
    sheet_queue(wb, queue)
    if records:
        sheet_financials(wb, records)
        sheet_handcode(wb, records)
    sheet_summary(wb, records, queue)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(records)} records, {len(queue)} queue rows)")


if __name__ == "__main__":
    main()
