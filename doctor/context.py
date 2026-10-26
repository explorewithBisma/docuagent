"""
doctor/context.py
------------------
What every check shares: the workbook, the evaluator, the transaction lists found in it, the options, the list of
issues found so far, and which cells already have a finding (so one problem is not reported twice).
"""

from collections import Counter
from dataclasses import dataclass, field

from budget import importer, names
from doctor import formulas as fm
from doctor.evaluator import Evaluator, EvalError, ExcelError, Unsupported, is_number
from toolkit.sheets import a1


@dataclass
class Options:
    tolerance: float = 0.001        # figures that differ by less than this share (0.1%) are treated as equal
    include_low: bool = True        # show the low-importance findings too


@dataclass
class TxTable:
    """A transaction list: a header row and rows of dated, described, priced items (like a daily expense log)."""
    sheet: str
    header_row: int
    first_row: int
    last_row: int
    columns: dict                   # role -> 1-based column
    rows: list                      # Excel row numbers of the data rows
    amount_total: float = 0.0
    by_category: dict = field(default_factory=dict)      # canonical name -> [display name, total, count]
    by_date: dict = field(default_factory=dict)

    def column_letter(self, role):
        from openpyxl.utils import get_column_letter
        return get_column_letter(self.columns[role])


TOTAL_LABEL = importer.TOTAL_WORDS
TOTAL_TOKENS = {"total", "totals", "sum", "subtotal"}       # ("grand" alone is not one: "Grand barsha" is a shop)


def label_tokens(text) -> set:
    return set(names.canonical(text).split())


class Context:
    def __init__(self, workbook, options: Options = None):
        self.wb = workbook
        self.options = options or Options()
        self.ev = Evaluator(workbook)
        self.issues = []
        self.reported = set()               # {(sheet, "B5")}: cells that already have a finding
        self.tx_tables = []
        self._trees = {}
        self.stats = Counter()
        self.functions_seen = Counter()

    # --- values -------------------------------------------------------------------------------------------------
    def val(self, sheet, row, col):
        """A cell's value (a formula's stored result, or its calculated one); None when unknown."""
        try:
            return self.ev.cell_value(sheet, row, col)
        except (Unsupported, EvalError):
            return None

    def number(self, sheet, row, col):
        value = self.val(sheet, row, col)
        # a date is a number to Excel, but a "figure" on a sheet is never a date (a "Loan Start Date" is not an amount)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, (bool, ExcelError)) else None

    def tree(self, cell):
        """The parsed formula of a cell, or None when it cannot be read (counted)."""
        key = (cell.sheet, cell.row, cell.col)
        if key not in self._trees:
            try:
                self._trees[key] = fm.parse(cell.formula)
            except fm.FormulaSyntax:
                self._trees[key] = None
                self.stats["unreadable_formulas"] += 1
        return self._trees[key]

    # --- reporting ------------------------------------------------------------------------------------------------
    def add(self, issue):
        self.issues.append(issue)
        for sheet, coord in issue.locations:
            self.reported.add((sheet, coord))
        return issue

    def is_reported(self, cell) -> bool:
        return (cell.sheet, cell.coord) in self.reported

    def close(self, a, b) -> bool:
        """Equal, allowing for rounding and the tolerance option."""
        return abs(a - b) <= max(0.005, self.options.tolerance * max(abs(a), abs(b)))


def money(value) -> str:
    """11755 -> '11,755'; 8266.67 -> '8,266.67'."""
    value = round(float(value), 2)
    return f"{value:,.0f}" if value == int(value) else f"{value:,.2f}"


def is_total_label(text) -> bool:
    """'TOTAL', '🏁 GRAND TOTAL', '✅ TOTAL INCOME', '(-) Total Expenses This Month', 'Subtotal': a label that starts with total / sum.
    Emojis and symbols in front are ignored."""
    words = names.canonical(text).split()
    return bool(words) and (words[0] in ("total", "totals", "subtotal", "sum") or words[:2] == ["grand", "total"])


def row_has_total_label(sheet, row) -> bool:
    return any(isinstance(cell.value, str) and not cell.is_formula and is_total_label(cell.value)
               for (r, _), cell in sheet.cells.items() if r == row)


def labelled_figures(ctx, sheet):
    """[(label text, label cell, number cell, number)] for every text label with a number in the next three cells of its row."""
    found = []
    for (row, col), cell in sheet.cells.items():
        if cell.is_formula or not isinstance(cell.value, str) or not cell.value.strip():
            continue
        for next_col in range(col + 1, col + 4):
            other = sheet.get(row, next_col)
            if other is None:
                continue
            number = ctx.number(sheet.name, row, next_col)
            if number is not None:
                found.append((cell.value.strip(), cell, other, number))
            break
    return found


def _is_sum_of_rows_above(ctx, sheet, row, col, first_row) -> bool:
    """True when the amount in this row is a total of the rows above it even though the row has no 'Total' label: a SUM formula over
    them, or (for a typed total) an amount, with no description, that equals the sum of the numbers above."""
    cell = sheet.get(row, col)
    if cell is None:
        return False
    if cell.is_formula:
        tree = ctx.tree(cell)
        return tree is not None and any(
            isinstance(arg, fm.Ref) and arg.kind in ("range", "col") and (arg.sheet in (None, sheet.name)) and arg.c1 <= col <= arg.c2
            and arg.r1 <= first_row + 1 and (arg.r2 is None or arg.r2 >= row - 1)
            for call in fm.calls(tree) if call.name in ("SUM", "SUBTOTAL") for arg in call.args)
    amount = ctx.number(sheet.name, row, col)
    if amount is None or amount <= 0:
        return False
    described = any(sheet.get(row, c) is not None and isinstance(sheet.get(row, c).value, str) and sheet.get(row, c).value.strip()
                    for c in range(1, sheet.max_col + 1))
    if described:
        return False
    above = [ctx.number(sheet.name, r, col) for r in range(first_row, row)]
    above = [a for a in above if a is not None]
    return len(above) >= 3 and abs(sum(above) - amount) <= max(0.005, 1e-6 * amount)


def find_tx_tables(ctx) -> list:
    """Transaction lists: a header row with date/description and amount columns, and at least 5 numeric amounts below it."""
    tables = []
    for sheet in ctx.wb.sheets.values():
        if sheet.state != "visible" or sheet.max_row < 6:
            continue
        layout = importer.detect_layout(sheet.to_frame())
        if not layout or "amount" not in layout["columns"]:
            continue
        columns = {role: index + 1 for role, index in layout["columns"].items()}
        header_row = layout["header_row"] + 1
        rows = []
        amount_col = columns["amount"]
        for row in range(header_row + 1, sheet.max_row + 1):
            if row_has_total_label(sheet, row) or _is_sum_of_rows_above(ctx, sheet, row, amount_col, header_row + 1):
                break
            description = sheet.get(row, columns["description"]) if "description" in columns else None
            if ctx.number(sheet.name, row, columns["amount"]) is not None or (description is not None and description.value not in (None, "")) \
                    or sheet.get(row, columns["amount"]) is not None:
                rows.append(row)
        amounts = [ctx.number(sheet.name, r, columns["amount"]) for r in rows]
        numeric = [(r, a) for r, a in zip(rows, amounts) if a is not None]
        if len(numeric) < 5:
            continue
        table = TxTable(sheet.name, header_row, rows[0], rows[-1], columns, rows, amount_total=round(sum(a for _, a in numeric), 6))
        for row, amount in numeric:
            if "category" in columns:
                category = sheet.value(row, columns["category"])
                if isinstance(category, str) and category.strip():
                    entry = table.by_category.setdefault(names.canonical(category), [category.strip(), 0.0, 0])
                    entry[1] += amount
                    entry[2] += 1
        tables.append(table)
    return tables
