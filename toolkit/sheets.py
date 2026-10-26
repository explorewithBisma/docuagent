"""
toolkit/sheets.py
------------------
Reads an .xlsx workbook ONCE into plain Python objects, for the tools that look inside a workbook (Excel Doctor, and later
the others): every cell with its formula text and Excel's stored result, number format, hidden state, merged cells, and
which parts of the file exist (charts, images, external links, comments).

Nothing here changes the file. Excel stores the result of every formula next to it; that stored result is what is read
(`value`). A file that was never opened in Excel (for example one written by a script) has no stored results: then
`value` of a formula cell is None, and the evaluator in doctor/evaluator.py can compute it.
"""

import io
import re
import warnings
import zipfile
from dataclasses import dataclass, field

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

ERROR_VALUES = ("#REF!", "#DIV/0!", "#N/A", "#NAME?", "#VALUE!", "#NUM!", "#NULL!")
OLD_EXCEL_HEADER = b"\xd0\xcf\x11\xe0"          # the container of old .xls files AND of password-protected workbooks


def a1(row: int, col: int) -> str:
    return f"{get_column_letter(col)}{row}"


@dataclass
class Cell:
    sheet: str
    row: int
    col: int
    formula: str            # formula text WITHOUT the leading "=", or None for a typed value
    value: object           # the typed value, or Excel's stored result for a formula (None when there is none)
    number_format: str = "General"

    @property
    def is_formula(self) -> bool:
        return self.formula is not None

    @property
    def coord(self) -> str:
        return a1(self.row, self.col)

    @property
    def is_error(self) -> bool:
        return isinstance(self.value, str) and self.value in ERROR_VALUES


@dataclass
class SheetData:
    name: str
    state: str = "visible"
    cells: dict = field(default_factory=dict)           # {(row, col): Cell}
    max_row: int = 0
    max_col: int = 0
    merged: list = field(default_factory=list)
    hidden_rows: set = field(default_factory=set)
    hidden_cols: set = field(default_factory=set)
    has_comments: bool = False

    def get(self, row: int, col: int):
        return self.cells.get((row, col))

    def value(self, row: int, col: int):
        cell = self.cells.get((row, col))
        return None if cell is None else cell.value

    def formula_cells(self) -> list:
        return [c for c in self.cells.values() if c.is_formula]

    def to_frame(self) -> pd.DataFrame:
        """The sheet as a table of its values (formulas as their stored results), row 0 = Excel row 1."""
        frame = pd.DataFrame(index=range(self.max_row), columns=range(self.max_col), dtype=object)
        for (row, col), cell in self.cells.items():
            if cell.value is not None:
                frame.iat[row - 1, col - 1] = cell.value
        return frame


@dataclass
class WorkbookData:
    sheets: dict                                        # {name: SheetData} in workbook order
    parts: list                                         # names of the parts inside the .xlsx package
    defined_names: list = field(default_factory=list)
    charts: int = 0
    images: int = 0
    has_external_links: bool = False
    has_macros: bool = False

    @property
    def order(self) -> list:
        return list(self.sheets)

    def total_formulas(self) -> int:
        return sum(len(s.formula_cells()) for s in self.sheets.values())


def read_workbook(file_bytes: bytes) -> WorkbookData:
    """Read every sheet. Raises ValueError with a readable message for anything that is not a normal .xlsx/.xlsm."""
    if file_bytes[:4] == OLD_EXCEL_HEADER:
        raise ValueError("This looks like an old .xls file or a password-protected workbook. Please save it as a normal "
                         ".xlsx file without a password and upload it again.")
    if not zipfile.is_zipfile(io.BytesIO(file_bytes)):
        raise ValueError("This doesn't look like an Excel (.xlsx) file.")
    parts = zipfile.ZipFile(io.BytesIO(file_bytes)).namelist()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            formulas = load_workbook(io.BytesIO(file_bytes), data_only=False)
            stored = load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as error:                                            # a damaged file: say so, don't crash
        raise ValueError(f"This workbook couldn't be opened ({type(error).__name__}). It may be damaged.")

    sheets = {}
    for ws in formulas.worksheets:
        data = SheetData(name=ws.title, state=ws.sheet_state, max_row=ws.max_row or 0, max_col=ws.max_column or 0,
                         merged=[str(r) for r in ws.merged_cells.ranges])
        data.hidden_rows = {r for r, d in ws.row_dimensions.items() if d.hidden}
        data.hidden_cols = {openpyxl_col_index(k) for k, d in ws.column_dimensions.items() if d.hidden}
        results = stored[ws.title]
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                raw = cell.value
                text = getattr(raw, "text", None)                         # array formulas
                formula = text if text is not None else (raw if isinstance(raw, str) and raw.startswith("=") else None)
                if formula is not None:
                    formula = formula[1:] if formula.startswith("=") else formula
                    value = results.cell(row=cell.row, column=cell.column).value
                else:
                    value = raw
                data.cells[(cell.row, cell.column)] = Cell(ws.title, cell.row, cell.column, formula, value, cell.number_format)
                if cell.comment is not None:
                    data.has_comments = True
        sheets[ws.title] = data

    return WorkbookData(
        sheets=sheets, parts=parts, defined_names=list(formulas.defined_names),
        charts=sum(1 for p in parts if re.match(r"xl/charts/chart\d+\.xml$", p)),
        images=sum(1 for p in parts if p.startswith("xl/media/")),
        has_external_links=any(p.startswith("xl/externalLinks/") for p in parts),
        has_macros="xl/vbaProject.bin" in parts)


def openpyxl_col_index(letters: str) -> int:
    from openpyxl.utils import column_index_from_string
    return column_index_from_string(letters)
