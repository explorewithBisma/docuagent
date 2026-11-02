"""
reconcile/output.py
--------------------
The result as ONE Excel workbook with the sheets Summary, Matched, Maybe, Only in A, Only in B and Mismatches. Matched / Maybe /
Mismatches put the two files' rows side by side with the reason; every bucket has its own colour. The user's own files are never
touched - this is a new workbook.
"""

import io
from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from quality import is_blank
from reconcile.report import BUCKET_TITLES, totals

COLORS = {"exact": "C6E0B4", "close": "E2EFDA", "maybe": "FFE699", "mismatch": "FFC7CE", "only_a": "F8CBAD", "only_b": "BDD7EE", "confirmed": "C6E0B4"}
HEADER_A, HEADER_B, HEADER_PLAIN = "D9E1F2", "FCE4D6", "EDEDED"
MAX_ROWS = 200_000
_EDGE = Side(style="thin", color="D0D0D0")
THIN = Border(left=_EDGE, right=_EDGE, top=_EDGE, bottom=_EDGE)


def _value(v):
    if not isinstance(v, str) and is_blank(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v.item() if hasattr(v, "item") else v


_FILLS = {}


def _fill(color):
    """One shared PatternFill per colour (creating one per cell is what makes big sheets slow)."""
    if color not in _FILLS:
        _FILLS[color] = PatternFill("solid", start_color=color, end_color=color)
    return _FILLS[color]


def _table_sheet(ws, headers, rows, colors, header_colors, notes, title):
    for number, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=number, value=header)
        cell.font = Font(bold=True)
        cell.fill = _fill(header_colors[number - 1])
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    shown = rows[:MAX_ROWS]
    for r, (values, color) in enumerate(zip(shown, colors), start=2):
        fill = _fill(color) if color else None
        for c, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=c, value=_value(value))
            if fill is not None:
                cell.fill = fill
    ws.freeze_panes = "A2"
    if shown:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(shown) + 1}"
    for number, header in enumerate(headers, start=1):
        sample = [len(str(_value(row[number - 1]) or "")) for row in shown[:300]]
        ws.column_dimensions[get_column_letter(number)].width = min(max(max(sample + [len(header)]) + 2, 9), 48)
    if len(rows) > MAX_ROWS:
        notes.append(f"The '{title}' sheet shows the first {MAX_ROWS:,} of {len(rows):,} rows.")


def build_workbook(name_a, name_b, table_a, table_b, A, B, map_a, map_b, result, report, accepted=frozenset()) -> bytes:
    """`accepted` = {(a, b)} maybes the user confirmed: they are listed under Matched (reason 'confirmed by you')."""
    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    notes = []
    cols_a, cols_b = [str(c) for c in table_a.columns], [str(c) for c in table_b.columns]
    values_a, values_b = table_a.astype(object).values.tolist(), table_b.astype(object).values.tolist()          # once, not row by row
    amount_difference = "Amount difference (B - A)"

    def side_by_side(pairs, bucket_of, reason_of):
        headers = ["Result", "Why", "Row in A"] + [f"A: {c}" for c in cols_a] + ["Row in B"] + [f"B: {c}" for c in cols_b] + [amount_difference]
        header_colors = [HEADER_PLAIN] * 3 + [HEADER_A] * len(cols_a) + [HEADER_PLAIN] + [HEADER_B] * len(cols_b) + [HEADER_PLAIN]
        rows, colors = [], []
        for p in pairs:
            rows.append([bucket_of(p), reason_of(p), A.row_numbers[p.a]] + values_a[p.a] + [B.row_numbers[p.b]] + values_b[p.b] + [round(p.difference, 2)])
            colors.append(COLORS["confirmed"] if (p.a, p.b) in accepted else COLORS[p.bucket])
        return headers, rows, colors, header_colors

    def one_side(rows_idx, side, values, cols, label):
        headers = [f"Row in {label}"] + [f"{label}: {c}" for c in cols]
        header_colors = [HEADER_PLAIN] + [HEADER_A if label == "A" else HEADER_B] * len(cols)
        rows = [[side.row_numbers[i]] + values[i] for i in rows_idx]
        return headers, rows, [COLORS["only_" + label.lower()]] * len(rows), header_colors

    confirmed = [p for p in result.bucket("maybe") if (p.a, p.b) in accepted]
    matched = [p for p in result.pairs if p.bucket in ("exact", "close")] + confirmed
    matched.sort(key=lambda p: (p.a, p.b))
    still_maybe = [p for p in result.bucket("maybe") if (p.a, p.b) not in accepted]

    def matched_result(p):
        return "Confirmed by you" if (p.a, p.b) in accepted else BUCKET_TITLES[p.bucket]

    def matched_reason(p):
        return ("Confirmed by you: " + p.reason) if (p.a, p.b) in accepted else p.reason

    for title, spec in (
        ("Matched", side_by_side(matched, matched_result, matched_reason)),
        ("Maybe", side_by_side(still_maybe, lambda p: "Maybe - please confirm", lambda p: p.reason)),
        ("Only in A", one_side(result.only_a, A, values_a, cols_a, "A")),
        ("Only in B", one_side(result.only_b, B, values_b, cols_b, "B")),
        ("Mismatches", side_by_side(result.mismatches, lambda p: BUCKET_TITLES["mismatch"], lambda p: p.reason)),
    ):
        headers, rows, colors, header_colors = spec
        _table_sheet(wb.create_sheet(title), headers, rows, colors, header_colors, notes, title)

    _summary(summary, name_a, name_b, map_a, map_b, A, B, result, report, accepted, notes)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _summary(ws, name_a, name_b, map_a, map_b, A, B, result, report, accepted, notes):
    row = [1]

    def put(values, bold=False, color=None):
        for c, value in enumerate(values, start=1):
            cell = ws.cell(row=row[0], column=c, value=value)
            cell.font = Font(bold=bold)
            if color:
                cell.fill = _fill(color)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        row[0] += 1

    put(["Match two files - summary"], bold=True)
    put(["Made", datetime.now().strftime("%Y-%m-%d %H:%M")])
    put(["File A", name_a, f"{A.n:,} rows"])
    put(["File B", name_b, f"{B.n:,} rows"])
    put(["Your own files were not changed - this is a new workbook."])
    row[0] += 1
    t = totals(A, B, result)
    put(["Result", "Rows", "Money in A", "Money in B", "Colour on the sheet"], bold=True, color=HEADER_PLAIN)
    confirmed = sum(1 for p in result.bucket("maybe") if (p.a, p.b) in accepted)
    confirmed_a = sum(float(A.amounts[p.a]) for p in result.bucket("maybe") if (p.a, p.b) in accepted)
    confirmed_b = sum(float(B.amounts[p.b]) for p in result.bucket("maybe") if (p.a, p.b) in accepted)
    put([BUCKET_TITLES["exact"], t["exact"]["count"], round(t["exact"]["a"], 2), round(t["exact"]["b"], 2), "green"], color=COLORS["exact"])
    put([BUCKET_TITLES["close"], t["close"]["count"], round(t["close"]["a"], 2), round(t["close"]["b"], 2), "light green"], color=COLORS["close"])
    if confirmed:
        put(["Maybe, confirmed by you", confirmed, round(confirmed_a, 2), round(confirmed_b, 2), "green"], color=COLORS["confirmed"])
    put([BUCKET_TITLES["maybe"] + " (to confirm)", t["maybe"]["count"] - confirmed, round(t["maybe"]["a"] - confirmed_a, 2), round(t["maybe"]["b"] - confirmed_b, 2), "yellow"], color=COLORS["maybe"])
    put([BUCKET_TITLES["only_a"], t["only_a"]["count"], round(t["only_a"]["a"], 2), 0, "orange"], color=COLORS["only_a"])
    put([BUCKET_TITLES["only_b"], t["only_b"]["count"], 0, round(t["only_b"]["b"], 2), "blue"], color=COLORS["only_b"])
    put([BUCKET_TITLES["mismatch"], t["mismatch"]["count"], round(t["mismatch"]["a"], 2), round(t["mismatch"]["b"], 2), "red"], color=COLORS["mismatch"])
    put(["Net amount difference in mismatches (B - A)", "", "", round(t["mismatch"]["difference"], 2)])
    row[0] += 1
    put(["How the columns were read"], bold=True, color=HEADER_PLAIN)
    for fact in report.facts:
        put([fact.label, fact.value, fact.source, fact.confidence])
    row[0] += 1
    put(["Settings"], bold=True, color=HEADER_PLAIN)
    put(["Days apart allowed", result.options.day_window])
    put(["Amount tolerance for 'maybe'", f"{result.options.tolerance:.2%}"])
    put(["Rows matched at most once", "yes - a row is never matched to two rows; leftovers are listed"])
    if report.notes or notes:
        row[0] += 1
        put(["Notes"], bold=True, color=HEADER_PLAIN)
        for note in list(report.notes) + notes:
            put([note])
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 44
    ws.column_dimensions["C"].width = 44
    ws.column_dimensions["D"].width = 18
    ws.column_dimensions["E"].width = 18
