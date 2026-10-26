"""
doctor/checks.py
-----------------
The checks of Excel Doctor. Each one reads the workbook (never changes it) and adds Issues to the context:

  check_figures      figures that disagree with the rows they come from, or with another sheet
                     (typed totals, a sheet's total vs the transaction list, budgets that differ)
  check_errors       #REF!, #DIV/0!, ... shown in cells, references to sheets that do not exist, links to other files
  check_criteria     SUMIF / COUNTIF criteria that match nothing, with the closest value that does exist
  check_ranges       formulas whose range stops short of the data, siblings that differ, ranges that include a subtotal
  check_typed        a number typed over a formula in a column of formulas
  check_stale        a stored result that differs from what the formula gives now
  check_small        numbers or dates stored as text, hidden data

Every finding says what it saw, where, and why it matters. Nothing is guessed: what cannot be calculated is counted and
reported as "not checked".
"""

import difflib
import re
from collections import Counter, defaultdict

from budget import names
from doctor import formulas as fm
from doctor.context import TOTAL_TOKENS, labelled_figures, label_tokens, money, row_has_total_label
from doctor.evaluator import EvalError, ExcelError, Unsupported, as_number, is_number, make_matcher, numeric_text
from quality import is_blank, parse_number
from toolkit.report import Issue
from toolkit.sheets import ERROR_VALUES, a1

SUMMING = ("SUM", "SUMIF", "SUMIFS", "COUNTIF", "COUNTIFS", "AVERAGE", "MAX", "MIN", "COUNT")
EXPENSE_WORDS = {"expense", "expenses", "spent", "spending"}
NOT_AN_EXPENSE_TOTAL = {"budget", "income", "loan", "loans", "payment", "payments", "balance", "savings", "remaining", "left", "over",
                        "average", "avg", "daily", "category", "target", "limit"}
ERROR_EXPLANATIONS = {
    "#REF!": "a formula points at a cell, row or sheet that was deleted",
    "#DIV/0!": "a formula divides by zero or by an empty cell",
    "#N/A": "a lookup did not find what it was looking for",
    "#NAME?": "a formula uses a name or function Excel does not know",
    "#VALUE!": "a formula is doing arithmetic with text",
    "#NUM!": "a formula has an impossible number in it",
    "#NULL!": "a formula refers to two areas that do not overlap",
}


# ---------------------------------------------------------------------------------------------------------------
# 1. ERRORS
# ---------------------------------------------------------------------------------------------------------------
def check_errors(ctx):
    groups = defaultdict(list)                       # (sheet, code) -> cells
    missing_sheet = defaultdict(list)                # missing sheet name -> cells
    external = []
    for sheet in ctx.wb.sheets.values():
        for cell in sheet.formula_cells():
            code = cell.value if cell.is_error else next((e for e in ERROR_VALUES if e in cell.formula), None)
            if code:
                groups[(sheet.name, code)].append(cell)
            tree = ctx.tree(cell)
            if tree is not None:
                for ref in fm.refs(tree):
                    if ref.sheet is not None and ref.sheet not in ctx.wb.sheets and not code:
                        missing_sheet[ref.sheet].append(cell)
            if re.search(r"\[\d+\]", cell.formula):
                external.append(cell)
    for (sheet, code), cells in groups.items():
        first = cells[0]
        ctx.add(Issue(
            "formula_error", "high", f"{code} in {len(cells)} cell(s) on '{sheet}'" if len(cells) > 1 else f"{code} in {sheet}!{first.coord}",
            f"Excel shows {code} here: {ERROR_EXPLANATIONS.get(code, 'a formula cannot be calculated')}. Anything that adds up this "
            "cell will show an error or a wrong number.",
            evidence=f"{first.coord}: ={first.formula}", locations=[(sheet, c.coord) for c in cells],
            fix="Open the cell, look at the formula and point it at the right cell again."))
    for name, cells in missing_sheet.items():
        ctx.add(Issue("missing_sheet", "high", f"A formula refers to a sheet called '{name}' that is not in this file",
                      "The sheet was deleted or renamed, so these formulas cannot work.", evidence=f"{cells[0].coord}: ={cells[0].formula}",
                      locations=[(c.sheet, c.coord) for c in cells], fix="Restore the sheet or change the formula to use the right one."))
    if external:
        ctx.add(Issue("external_link", "medium", f"{len(external)} formula(s) read from another file",
                      "These figures come from a different workbook. If that file is moved or renamed they stop updating.",
                      evidence=f"{external[0].coord}: ={external[0].formula}", locations=[(c.sheet, c.coord) for c in external],
                      fix="Copy the figures into this file, or keep both files together."))


# ---------------------------------------------------------------------------------------------------------------
# 2. CRITERIA THAT MATCH NOTHING
# ---------------------------------------------------------------------------------------------------------------
def _closest(criterion: str, values) -> tuple:
    """(best value, how) among the distinct texts of a range, or (None, '')."""
    wanted = names.canonical(criterion)
    wanted_tokens = set(wanted.split())
    best = (0.0, None)
    for value in values:
        canon = names.canonical(value)
        if not canon:
            continue
        tokens = set(canon.split())
        if canon == wanted:
            score = 1.0
        elif wanted_tokens and (wanted_tokens <= tokens or tokens <= wanted_tokens):
            score = 0.9
        else:
            score = difflib.SequenceMatcher(None, wanted, canon).ratio()
        if score > best[0]:
            best = (score, value)
    return (best[1], "") if best[0] >= 0.6 else (None, "")


def check_criteria(ctx):
    groups = {}
    for sheet in ctx.wb.sheets.values():
        for cell in sheet.formula_cells():
            tree = ctx.tree(cell)
            if tree is None:
                continue
            for call in fm.calls(tree):
                if call.name not in ("SUMIF", "SUMIFS", "COUNTIF", "COUNTIFS"):
                    continue
                if call.name in ("SUMIF", "COUNTIF"):
                    pairs = [(call.args[0], call.args[1])] if len(call.args) >= 2 else []
                    sum_arg = call.args[2] if call.name == "SUMIF" and len(call.args) == 3 else (call.args[0] if call.name == "SUMIF" else None)
                else:
                    pairs = [(call.args[i], call.args[i + 1]) for i in range(1 if call.name == "SUMIFS" else 0, len(call.args) - 1, 2)]
                    sum_arg = call.args[0] if call.name == "SUMIFS" else None
                for range_node, criterion_node in pairs:
                    if not isinstance(range_node, fm.Ref) or range_node.kind not in ("range", "col"):
                        continue
                    try:
                        values = ctx.ev.range_values(range_node, sheet.name)
                        criterion = ctx.ev.criterion_value(criterion_node, sheet.name)
                    except (Unsupported, EvalError):
                        ctx.stats["criteria_not_checked"] += 1
                        continue
                    ctx.stats["criteria_checked"] += 1
                    flat = [v for row in values for v in row]
                    texts = [v for v in flat if isinstance(v, str) and v.strip()]
                    if not isinstance(criterion, str) or not texts:
                        continue
                    matcher = make_matcher(criterion)
                    if any(matcher(v) for v in flat):
                        continue
                    candidate, _ = _closest(criterion, dict.fromkeys(texts))
                    if candidate is None:
                        ctx.stats["criteria_without_match_or_lookalike"] += 1
                        continue
                    key = (sheet.name, range_node.text, str(criterion), candidate)
                    group = groups.setdefault(key, {"cells": [], "range": range_node, "sum": sum_arg, "sheet": sheet.name,
                                                    "criterion": criterion, "candidate": candidate, "values": values, "stored_zero": True})
                    group["cells"].append(cell)
                    stored = cell.value
                    if is_number(stored) and not isinstance(stored, ExcelError) and as_number(stored) != 0:
                        group["stored_zero"] = False
    for group in groups.values():
        cells, candidate, criterion = group["cells"], group["candidate"], group["criterion"]
        rows = [i for i, row in enumerate(group["values"]) if row and row[0] == candidate or (row and isinstance(row[0], str) and row[0] == candidate)]
        total = 0.0
        if isinstance(group["sum"], fm.Ref):
            try:
                sums = ctx.ev.range_values(group["sum"], group["sheet"])
                total = sum(as_number(sums[i][0]) for i in rows if i < len(sums) and sums[i] and is_number(sums[i][0]))
            except (Unsupported, EvalError):
                total = 0.0
        if str(criterion).strip().casefold() == str(candidate).strip().casefold():
            how = "they differ only by extra spaces, which Excel does not ignore"
        elif names.canonical(criterion) == names.canonical(candidate):
            how = "they differ only by symbols, emojis or punctuation, which Excel does not ignore"
        else:
            how = "they are written differently"
        ctx.add(Issue(
            "criteria_no_match", "high" if group["stored_zero"] else "medium",
            f"The formula looks for \"{criterion}\" but no cell in {group['range'].text.split('!')[-1]} says that",
            f"The criterion \"{criterion}\" matches nothing, so the formula adds up nothing. The closest value that exists is "
            f"\"{candidate}\" ({len(rows)} row(s){', ' + money(total) + ' in total' if total else ''}); {how}.",
            evidence=f"{cells[0].coord}: ={cells[0].formula}", locations=[(c.sheet, c.coord) for c in cells], impact=total,
            fix=f"Make the criterion say \"{candidate}\" exactly, or rename the values in the list to match."))


# ---------------------------------------------------------------------------------------------------------------
# 3. RANGES
# ---------------------------------------------------------------------------------------------------------------
def _continuation(ctx, sheet_name, row_after, col, own_cell):
    """Rows directly below a range that still hold numbers: [(row, value)]."""
    sheet = ctx.wb.sheets[sheet_name]
    found = []
    row = row_after
    while row <= sheet.max_row:
        cell = sheet.get(row, col)
        if cell is None or (own_cell is not None and cell.sheet == own_cell.sheet and (cell.row, cell.col) == (own_cell.row, own_cell.col)):
            break
        number = ctx.number(sheet_name, row, col)
        if number is None or row_has_total_label(sheet, row):
            break
        found.append((row, number))
        row += 1
    return found


def check_ranges(ctx):
    for sheet in ctx.wb.sheets.values():
        formula_cells = sheet.formula_cells()
        for cell in formula_cells:
            tree = ctx.tree(cell)
            if tree is None:
                continue
            for call in fm.calls(tree):
                ctx.functions_seen[call.name] += 1
                if call.name not in SUMMING:
                    continue
                for arg in call.args:
                    if not isinstance(arg, fm.Ref) or arg.kind != "range" or arg.c1 != arg.c2:
                        continue
                    target = arg.sheet or sheet.name
                    if target not in ctx.wb.sheets:
                        continue
                    after = _continuation(ctx, target, arg.r2 + 1, arg.c1, cell)
                    if after:
                        first, last, hidden = after[0][0], after[-1][0], sum(v for _, v in after)
                        where = f"{target}!{a1(first, arg.c1)}" + (f":{a1(last, arg.c1)}" if last != first else "")
                        ctx.add(Issue(
                            "range_short", "medium", f"{cell.sheet}!{cell.coord}: the range stops before the data does",
                            f"=" f"{call.name}(...{arg.text.split('!')[-1]}) ends at row {arg.r2}, but {len(after)} more row(s) of numbers follow "
                            f"directly below ({where}), worth {money(hidden)}. They are not included.",
                            evidence=f"{cell.coord}: ={cell.formula}", locations=[(cell.sheet, cell.coord)], impact=abs(hidden),
                            fix=f"Extend the range to row {last}."))
                        break
                    if arg.r1 > 1:
                        above = ctx.number(target, arg.r1 - 1, arg.c1)
                        above_cell = ctx.wb.sheets[target].get(arg.r1 - 1, arg.c1)
                        if above is not None and above_cell is not None and not above_cell.is_formula and \
                                not row_has_total_label(ctx.wb.sheets[target], arg.r1 - 1):
                            ctx.add(Issue(
                                "range_short", "medium", f"{cell.sheet}!{cell.coord}: the range starts below the first number",
                                f"The range starts at row {arg.r1}, but the number directly above it ({target}!{a1(arg.r1 - 1, arg.c1)} = "
                                f"{money(above)}) is left out.", evidence=f"{cell.coord}: ={cell.formula}",
                                locations=[(cell.sheet, cell.coord)], impact=abs(above), fix="Start the range one row higher."))
                            break
        _check_siblings(ctx, sheet)
        _check_double_count(ctx, sheet)


def _check_siblings(ctx, sheet):
    """In a column of filled-down formulas, the one that does something different from the rest."""
    by_column = defaultdict(list)
    for cell in sheet.formula_cells():
        by_column[cell.col].append(cell)
    for col, cells in by_column.items():
        cells.sort(key=lambda c: c.row)
        run = []
        for cell in cells + [None]:
            if cell is not None and (not run or cell.row == run[-1].row + 1):
                run.append(cell)
                continue
            if len(run) >= 4:
                patterns = [fm.to_relative(c.formula, c.row, c.col) for c in run]
                common, count = Counter(patterns).most_common(1)[0]
                if count / len(run) >= 0.7 and count < len(run):
                    odd = [c for c, p in zip(run, patterns) if p != common and not ctx.is_reported(c)]
                    if 0 < len(odd) <= 2:
                        ctx.add(Issue(
                            "inconsistent_formula", "medium", f"{sheet.name}!{odd[0].coord} does something different from the formulas around it",
                            f"{count} of the {len(run)} formulas in this column work the same way; this one does not.",
                            evidence=f"{odd[0].coord}: ={odd[0].formula}   (the others look like ={run[patterns.index(common)].formula})",
                            locations=[(sheet.name, c.coord) for c in odd], fix="Copy the formula from the cell above if the difference is a mistake."))
            run = [cell] if cell is not None else []


def _check_double_count(ctx, sheet):
    for cell in sheet.formula_cells():
        tree = ctx.tree(cell)
        if tree is None:
            continue
        for call in fm.calls(tree):
            if call.name != "SUM":
                continue
            for arg in call.args:
                if not isinstance(arg, fm.Ref) or arg.kind != "range" or (arg.sheet or sheet.name) != sheet.name or arg.c1 != arg.c2:
                    continue
                for row in range(arg.r1, arg.r2 + 1):
                    inner = sheet.get(row, arg.c1)
                    if inner is None or not inner.is_formula or (inner.row, inner.col) == (cell.row, cell.col):
                        continue
                    inner_tree = ctx.tree(inner)
                    if inner_tree is not None and any(c.name == "SUM" for c in fm.calls(inner_tree)):
                        ctx.add(Issue("double_count", "medium", f"{sheet.name}!{cell.coord} may count a subtotal twice",
                                      f"The range includes {inner.coord}, which is itself a sum. Adding the rows and their subtotal counts the "
                                      "same money twice.", evidence=f"{cell.coord}: ={cell.formula}   ({inner.coord}: ={inner.formula})",
                                      locations=[(sheet.name, cell.coord), (sheet.name, inner.coord)],
                                      impact=abs(ctx.number(sheet.name, inner.row, inner.col) or 0), fix="Leave the subtotal rows out of the range."))
                        break


# ---------------------------------------------------------------------------------------------------------------
# 4. A NUMBER TYPED OVER A FORMULA
# ---------------------------------------------------------------------------------------------------------------
def check_typed(ctx):
    for sheet in ctx.wb.sheets.values():
        found = []
        for (row, col), cell in sheet.cells.items():
            if cell.is_formula or not is_number(cell.value) or ctx.is_reported(cell):
                continue
            above, below = sheet.get(row - 1, col), sheet.get(row + 1, col)
            if above is not None and below is not None and above.is_formula and below.is_formula \
                    and ctx.number(sheet.name, row - 1, col) is not None and ctx.number(sheet.name, row + 1, col) is not None:
                found.append(cell)
        for cell in sorted(found, key=lambda c: (c.col, c.row)):
            ctx.add(Issue("typed_among_formulas", "medium", f"{sheet.name}!{cell.coord} is a typed number between formulas",
                          f"The cells directly above and below are formulas, but this one is a number someone typed ({money(as_number(cell.value))}). "
                          "It will not change when the data changes.", evidence=f"{cell.coord}: {money(as_number(cell.value))}",
                          locations=[(sheet.name, cell.coord)], impact=abs(as_number(cell.value)),
                          fix="If it should be calculated, copy the formula from the cell above."))


# ---------------------------------------------------------------------------------------------------------------
# 5. FIGURES THAT DISAGREE
# ---------------------------------------------------------------------------------------------------------------
def _figure_tables(ctx, sheet, spent_word_ok=True):
    """Tables with a header row (Category/Date | ... Spent ...) and a total row below: [(header_row, label_col, value_col, total_cell)]."""
    tables = []
    for (row, col), cell in sheet.cells.items():
        if not isinstance(cell.value, str) or cell.is_formula:
            continue
        tokens = label_tokens(cell.value)
        if not (tokens & {"spent", "expenses", "expense", "spending"}):
            continue
        # the column of numbers under this header, until a blank cell or a total row
        row_end, total_cell = row, None
        for r in range(row + 1, sheet.max_row + 2):
            value_cell = sheet.get(r, col)
            if value_cell is None:
                break
            if row_has_total_label(sheet, r):
                total_cell = value_cell
                break
            row_end = r
        if total_cell is not None and row_end > row and ctx.number(sheet.name, total_cell.row, total_cell.col) is not None:
            tables.append((row, col, total_cell))
    return tables


def _budget_tables(ctx):
    """Every 'Category | ... Budget ...' table: [(sheet, {canonical category: (display, budget, cell)})]."""
    found = []
    for sheet in ctx.wb.sheets.values():
        for (row, col), cell in sheet.cells.items():
            if not isinstance(cell.value, str) or cell.is_formula or names.canonical(cell.value) != "category":
                continue
            budget_cell = next((c for (r, cc), c in sheet.cells.items() if r == row and isinstance(c.value, str) and not c.is_formula
                                and "budget" in names.canonical(c.value)), None)
            if budget_cell is None:
                continue
            rows = {}
            for r in range(row + 1, sheet.max_row + 1):
                label, value_cell = sheet.get(r, col), sheet.get(r, budget_cell.col)
                if label is None or not isinstance(label.value, str):
                    break
                if row_has_total_label(sheet, r):
                    break
                number = ctx.number(sheet.name, r, budget_cell.col)
                if number is not None:
                    rows[names.canonical(label.value)] = (names.display_name(label.value), number, value_cell)
            if rows:
                found.append((sheet.name, rows))
            break
    return found


def check_figures(ctx):
    tx_sheets = {t.sheet for t in ctx.tx_tables}
    main = max(ctx.tx_tables, key=lambda t: len(t.rows)) if ctx.tx_tables else None

    # (a) a labelled "total expenses" that does not match what the transaction list adds up to
    if main is not None:
        for sheet in ctx.wb.sheets.values():
            if sheet.name in tx_sheets:
                continue
            for text, label_cell, value_cell, number in labelled_figures(ctx, sheet):
                tokens = label_tokens(text)
                if not (tokens & EXPENSE_WORDS) or not (tokens & TOTAL_TOKENS) or tokens & NOT_AN_EXPENSE_TOTAL:
                    continue
                if ctx.close(number, main.amount_total):
                    continue
                typed = not value_cell.is_formula
                ctx.add(Issue(
                    "cross_total", "high", f"'{text}' says {money(number)}, but the rows add up to {money(main.amount_total)}",
                    f"{sheet.name}!{value_cell.coord} " + ("is a number someone typed, not a formula. " if typed else "is a formula. ")
                    + f"The list on '{main.sheet}' ({len(main.rows)} rows) adds up to {money(main.amount_total)}, a difference of "
                    f"{money(abs(number - main.amount_total))}.",
                    evidence=f"{sheet.name}!{value_cell.coord} = {money(number)};  {main.sheet}!{main.column_letter('amount')}"
                             f"{main.first_row}:{main.column_letter('amount')}{main.last_row} = {money(main.amount_total)}",
                    locations=[(sheet.name, value_cell.coord)], impact=abs(number - main.amount_total),
                    fix="Make this cell a formula that reads the total from the list." if typed else "Check what this formula adds up."))

    # (b) a table of 'spent' figures whose TOTAL differs from the transaction list
    if main is not None:
        for sheet in ctx.wb.sheets.values():
            if sheet.name in tx_sheets:
                continue
            for header_row, col, total_cell in _figure_tables(ctx, sheet):
                number = ctx.number(sheet.name, total_cell.row, total_cell.col)
                if ctx.is_reported(total_cell) or ctx.close(number, main.amount_total):
                    continue
                header = sheet.get(header_row, col).value
                ctx.add(Issue(
                    "cross_total", "high" if abs(number - main.amount_total) > 0.01 * main.amount_total else "medium",
                    f"{sheet.name}: the '{header}' column totals {money(number)}, but the rows on '{main.sheet}' add up to {money(main.amount_total)}",
                    f"The total at {sheet.name}!{total_cell.coord} differs from the transaction list by {money(abs(number - main.amount_total))}. "
                    "Some spending is missing from this summary (for example because a category or date is written differently in the list).",
                    evidence=f"{sheet.name}!{total_cell.coord} = {money(number)};  {main.sheet} = {money(main.amount_total)}",
                    locations=[(sheet.name, total_cell.coord)], impact=abs(number - main.amount_total),
                    fix="Check the formulas above the total, and the spelling of the categories / dates in the list."))

    # (c) the same label with different numbers on different sheets
    seen = defaultdict(list)
    for sheet in ctx.wb.sheets.values():
        for text, label_cell, value_cell, number in labelled_figures(ctx, sheet):
            canon = names.canonical(text)
            if len(canon.split()) >= 2:
                seen[canon].append((sheet.name, text, value_cell, number))
    for canon, entries in seen.items():
        sheets_involved = {e[0] for e in entries}
        values = [e[3] for e in entries]
        if len(sheets_involved) < 2 or all(ctx.close(values[0], v) for v in values[1:]) or any(ctx.is_reported(e[2]) for e in entries):
            continue
        listing = "; ".join(f"{money(e[3])} on '{e[0]}'" for e in entries)
        ctx.add(Issue("label_conflict", "medium", f"'{entries[0][1]}' has different numbers on different sheets",
                      f"The same figure is {listing}.", evidence=listing, locations=[(e[0], e[2].coord) for e in entries],
                      impact=max(values) - min(values), fix="Decide which one is right and make the others read from it."))

    # (d) budgets that differ between two category tables
    tables = _budget_tables(ctx)
    for i in range(len(tables)):
        for j in range(i + 1, len(tables)):
            (sheet_a, rows_a), (sheet_b, rows_b) = tables[i], tables[j]
            diffs = [(name, rows_a[name], rows_b[name]) for name in rows_a if name in rows_b and not ctx.close(rows_a[name][1], rows_b[name][1])]
            if not diffs:
                continue
            listing = "; ".join(f"{d[1][0]}: {money(d[1][1])} on '{sheet_a}' vs {money(d[2][1])} on '{sheet_b}'" for d in diffs)
            ctx.add(Issue("budget_conflict", "medium", f"Budgets for {len(diffs)} categor{'y' if len(diffs) == 1 else 'ies'} differ between '{sheet_a}' and '{sheet_b}'",
                          f"The same category has two different budgets: {listing}.", evidence=listing,
                          locations=[(sheet_a, d[1][2].coord) for d in diffs] + [(sheet_b, d[2][2].coord) for d in diffs],
                          impact=sum(abs(d[1][1] - d[2][1]) for d in diffs), fix="Keep the budget in one place and let the other sheet read it."))


def check_typed_totals(ctx):
    """A labelled total that is a typed number: compared with the numbers directly above it."""
    for sheet in ctx.wb.sheets.values():
        for (row, col), label in list(sheet.cells.items()):
            if label.is_formula or not isinstance(label.value, str) or not (label_tokens(label.value) & TOTAL_TOKENS):
                continue
            for next_col in range(col + 1, col + 7):
                cell = sheet.get(row, next_col)
                if cell is None or cell.is_formula or not is_number(cell.value) or ctx.is_reported(cell):
                    continue
                block = []
                r = row - 1
                while r >= 1 and not row_has_total_label(sheet, r):
                    number = ctx.number(sheet.name, r, next_col)
                    if number is None:
                        break
                    block.append(number)
                    r -= 1
                if len(block) < 3:
                    continue
                expected, typed = sum(block), as_number(cell.value)
                if ctx.close(typed, expected):
                    ctx.add(Issue("typed_total", "medium", f"{sheet.name}!{cell.coord} is a typed total",
                                  f"'{label.value.strip()}' is a number someone typed ({money(typed)}). It matches the {len(block)} rows above today, "
                                  "but it will not change when they do.", evidence=f"{cell.coord}: {money(typed)} = sum of {len(block)} rows above",
                                  locations=[(sheet.name, cell.coord)], impact=abs(typed), fix=f"Replace it with =SUM(...) of the rows above."))
                else:
                    ctx.add(Issue("typed_total", "high", f"{sheet.name}!{cell.coord}: the typed total {money(typed)} does not match its rows",
                                  f"'{label.value.strip()}' was typed in by hand, and the {len(block)} rows above it add up to {money(expected)}, "
                                  f"a difference of {money(abs(typed - expected))}.", evidence=f"typed {money(typed)} vs rows {money(expected)}",
                                  locations=[(sheet.name, cell.coord)], impact=abs(typed - expected), fix="Replace it with =SUM(...) of the rows above."))


# ---------------------------------------------------------------------------------------------------------------
# 6. STALE RESULTS
# ---------------------------------------------------------------------------------------------------------------
def check_stale(ctx):
    for sheet in ctx.wb.sheets.values():
        for cell in sheet.formula_cells():
            if ctx.is_reported(cell) or cell.value is None or cell.is_error or not is_number(cell.value):
                continue
            try:
                fresh = ctx.ev.evaluate(cell.formula, sheet.name)
            except (Unsupported, EvalError):
                continue
            if isinstance(fresh, ExcelError) or not is_number(fresh):
                continue
            ctx.stats["recalculated"] += 1
            if not ctx.close(as_number(cell.value), as_number(fresh)):
                ctx.add(Issue("stale_result", "low", f"{sheet.name}!{cell.coord} shows {money(as_number(cell.value))} but the formula gives {money(as_number(fresh))}",
                              "The number saved in the file is not what the formula calculates now. The file may not have been recalculated before it was saved.",
                              evidence=f"{cell.coord}: ={cell.formula}", locations=[(sheet.name, cell.coord)],
                              impact=abs(as_number(cell.value) - as_number(fresh)), fix="Open the file in Excel and press F9 (or save it again)."))


# ---------------------------------------------------------------------------------------------------------------
# 7. SMALL THINGS
# ---------------------------------------------------------------------------------------------------------------
_DATE_TEXT = re.compile(r"^\s*\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}\s*$")


def check_small(ctx):
    for sheet in ctx.wb.sheets.values():
        columns = defaultdict(list)
        for (row, col), cell in sheet.cells.items():
            columns[col].append(cell)
        for col, cells in columns.items():
            cells = [c for c in cells if not c.is_formula]
            numeric = [c for c in cells if is_number(c.value) and not hasattr(c.value, "year")]
            dated = [c for c in cells if hasattr(c.value, "year")]
            texty_numbers = [c for c in cells if isinstance(c.value, str) and parse_number(c.value.strip()) is not None]
            if len(numeric) >= 5 and texty_numbers and len(texty_numbers) <= 0.3 * len(numeric):
                header = next((sheet.get(r, col).value for r in range(1, min(sheet.max_row, 8) + 1)
                               if sheet.get(r, col) is not None and isinstance(sheet.get(r, col).value, str)), "")
                if re.search(r"\b(id|code|phone|mobile|zip|postal|account|number|no)\b", str(header).casefold()):
                    continue
                ctx.add(Issue("number_as_text", "low", f"{len(texty_numbers)} number(s) in column {a1(1, col)[:-1]} of '{sheet.name}' are stored as text",
                              "These look like numbers but Excel treats them as text, so SUM and other formulas skip them.",
                              evidence=f"{texty_numbers[0].coord}: \"{texty_numbers[0].value}\"", locations=[(sheet.name, c.coord) for c in texty_numbers],
                              impact=sum(float(c.value.strip().replace(",", "")) for c in texty_numbers), fix="Select them and convert to numbers."))
            texty_dates = [c for c in cells if isinstance(c.value, str) and _DATE_TEXT.match(c.value)]
            if len(dated) >= 5 and texty_dates and len(texty_dates) <= 0.3 * len(dated):
                ctx.add(Issue("date_as_text", "low", f"{len(texty_dates)} date(s) in column {a1(1, col)[:-1]} of '{sheet.name}' are stored as text",
                              "These look like dates but Excel treats them as text, so sorting, filtering and date formulas skip them.",
                              evidence=f"{texty_dates[0].coord}: \"{texty_dates[0].value}\"", locations=[(sheet.name, c.coord) for c in texty_dates],
                              fix="Re-enter them as real dates."))
        if sheet.state != "visible" and sheet.cells:
            ctx.add(Issue("hidden_sheet", "low", f"The sheet '{sheet.name}' is hidden", f"It holds {len(sheet.cells)} cells that are easy to forget about.",
                          locations=[(sheet.name, a1(*min(sheet.cells)))], fix="Right-click any sheet tab, choose Unhide, and check what is there."))
        hidden_cells = [c for (r, cc), c in sheet.cells.items() if r in sheet.hidden_rows or cc in sheet.hidden_cols]
        if hidden_cells and sheet.state == "visible":
            ctx.add(Issue("hidden_data", "low", f"{len(hidden_cells)} cell(s) with data are in hidden rows or columns on '{sheet.name}'",
                          "Hidden data is still counted by formulas but nobody sees it.", locations=[(sheet.name, c.coord) for c in hidden_cells[:50]],
                          fix="Unhide the rows or columns and check them."))
