"""
doctor/analyze.py
------------------
Excel Doctor: read a workbook, run every check, and build the Report (the "What I found" facts, the "Please check" issues in
order of importance, and notes about what could not be checked). The workbook is only read, never changed.
"""

from collections import Counter

from budget import names
from doctor import checks, rootcause
from doctor.context import Context, Options, find_tx_tables, money
from doctor.evaluator import SUPPORTED_FUNCTIONS
from toolkit.ranking import rank
from toolkit.report import Fact, Report
from toolkit.sheets import read_workbook


MAX_CELLS = 300_000


def describe_sheet(ctx, sheet, table):
    """(purpose sentence, evidence, confidence) for one sheet."""
    formulas = sheet.formula_cells()
    if not sheet.cells:
        return "empty", "no cells have anything in them", "high"
    if sheet.state != "visible":
        return "a hidden sheet", f"{len(sheet.cells)} cells; the sheet is hidden", "high"
    if table is not None:
        roles = ", ".join(sorted(table.columns, key=lambda r: table.columns[r]))
        return (f"a transaction list: {len(table.rows)} rows ({roles})",
                f"header row {table.header_row}; amounts add up to {money(table.amount_total)}", "high" if len(table.columns) >= 3 else "medium")
    other = Counter()
    for cell in formulas:
        tree = ctx.tree(cell)
        if tree is not None:
            for ref in (r for r in checks.fm.refs(tree) if r.sheet and r.sheet != sheet.name):
                other[ref.sheet] += 1
    share = len(formulas) / len(sheet.cells)
    if formulas and other and share >= 0.25:
        sources = ", ".join(f"'{n}'" for n, _ in other.most_common(3))
        return (f"a summary that calculates from {sources}", f"{len(formulas)} of {len(sheet.cells)} cells are formulas, "
                f"{sum(other.values())} references to other sheets", "high" if share >= 0.4 else "medium")
    if share >= 0.25:
        return ("a calculation sheet", f"{len(formulas)} of {len(sheet.cells)} cells are formulas", "medium")
    labelled = len(checks.labelled_figures(ctx, sheet))
    if labelled >= 2:
        return ("a list of labelled figures or settings", f"{labelled} label + number pairs", "medium")
    return ("a sheet of typed values and labels", f"{len(sheet.cells)} cells, {len(formulas)} formulas", "low")


def analyze(file_bytes: bytes, options: Options = None):
    """-> (Report, Context). Raises ValueError (readable) for a file that cannot be read."""
    workbook = read_workbook(file_bytes)
    cells = sum(len(sheet.cells) for sheet in workbook.sheets.values())
    if cells > MAX_CELLS:
        raise ValueError(f"This workbook is very large ({cells:,} filled cells). The limit for this check is {MAX_CELLS:,}; "
                         "try it with fewer sheets or rows.")
    ctx = Context(workbook, options)
    ctx.tx_tables = find_tx_tables(ctx)

    # the order matters: findings on a cell stop the later, more general checks from repeating it
    checks.check_errors(ctx)
    checks.check_criteria(ctx)
    checks.check_blank_criteria(ctx)
    checks.check_figures(ctx)
    checks.check_typed_totals(ctx)
    checks.check_ranges(ctx)
    checks.check_typed(ctx)
    checks.check_stale(ctx)
    checks.check_small(ctx)
    rootcause.merge_same_root_cause(ctx)            # last: joins findings that share one cause

    issues = ctx.issues if ctx.options.include_low else [i for i in ctx.issues if i.severity != "low"]
    tables = {t.sheet: t for t in ctx.tx_tables}
    facts = [Fact("This file", f"{len(workbook.sheets)} sheets, {workbook.total_formulas()} formulas"
                  + (f", {workbook.charts} chart(s)" if workbook.charts else "")
                  + (", macros" if workbook.has_macros else ""), "read directly from the workbook", "high")]
    for sheet in workbook.sheets.values():
        purpose, evidence, confidence = describe_sheet(ctx, sheet, tables.get(sheet.name))
        facts.append(Fact(sheet.name, purpose, evidence, confidence))

    notes = []
    other_functions = Counter()
    for sheet in workbook.sheets.values():
        for cell in sheet.formula_cells():
            tree = ctx.tree(cell)
            if tree is not None:
                for call in checks.fm.calls(tree):
                    if call.name not in SUPPORTED_FUNCTIONS:
                        other_functions[call.name] += 1
    if other_functions:
        listing = ", ".join(f"{name} ×{count}" for name, count in other_functions.most_common(6))
        notes.append(f"Formulas using {listing} were checked for errors, references and typed-over cells, but not recalculated "
                     "(only SUM, SUMIF, SUMIFS, COUNTIF and COUNTIFS are).")
    if ctx.stats["unreadable_formulas"]:
        notes.append(f"{ctx.stats['unreadable_formulas']} formula(s) could not be read and were not checked.")
    if ctx.stats["criteria_without_match_or_lookalike"]:
        notes.append(f"{ctx.stats['criteria_without_match_or_lookalike']} SUMIF/COUNTIF criteria match nothing and have no look-alike value "
                     "(probably a category with no spending yet) - not reported.")
    if workbook.charts:
        notes.append("This workbook has charts; they are kept exactly as they are in the highlighted copy.")
    if not ctx.tx_tables:
        notes.append("No transaction list was found, so totals could not be compared with the rows behind them.")

    stats = {"sheets": len(workbook.sheets), "formulas": workbook.total_formulas(), "charts": workbook.charts,
             "issues": len(issues), "recalculated": ctx.stats["recalculated"], "criteria_checked": ctx.stats["criteria_checked"]}
    report = Report("Excel Doctor", facts=facts, issues=rank(issues, workbook.order), stats=stats, notes=notes)
    return report, ctx
