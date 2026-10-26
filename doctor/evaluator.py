"""
doctor/evaluator.py
--------------------
A small evaluator for the formulas that matter when checking a workbook: SUM, SUMIF, SUMIFS, COUNTIF, COUNTIFS and plain
references, combined with + - * / ^ & % and comparisons. It reads the values stored in the workbook (and calculates a formula
whose stored result is missing). Any other function raises `Unsupported`: the Doctor then says "not checked" rather than guess.

The criteria rules follow Excel: ignoring capitals, wildcards * ? (and ~ to escape them), comparison prefixes >= <= <> > < =,
numbers matching numeric text, and an empty criterion behaving like 0.
"""

import re
from datetime import date, datetime

from doctor.formulas import Bin, Call, Err, FormulaSyntax, Name, Neg, Num, Pct, Ref, Str, parse
from toolkit.sheets import ERROR_VALUES

_EPOCH = datetime(1899, 12, 30)
SUPPORTED_FUNCTIONS = ("SUM", "SUMIF", "SUMIFS", "COUNTIF", "COUNTIFS")


class Unsupported(Exception):
    """The formula uses something this evaluator does not calculate."""


class EvalError(Exception):
    """The formula's result is an Excel error such as #DIV/0!."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class ExcelError(str):
    """An Excel error value as a result (a str, so it compares with '#REF!')."""


def serial(value) -> float:
    """Excel's number for a date."""
    if isinstance(value, datetime):
        return (value - _EPOCH).total_seconds() / 86400
    return float((datetime(value.year, value.month, value.day) - _EPOCH).days)


def is_number(value) -> bool:
    return isinstance(value, (int, float, datetime, date)) and not isinstance(value, bool)


def as_number(value) -> float:
    """Excel's number for a value (dates become serial numbers)."""
    if isinstance(value, (datetime, date)):
        return serial(value)
    return float(value)


def numeric_text(value):
    """The number a text cell holds ('5', ' 3.5 '), or None."""
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", "")) if value.strip() else None
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------------------------------------------
# CRITERIA (SUMIF / COUNTIF)
# ---------------------------------------------------------------------------------------------------------------
def _wildcard_regex(pattern: str):
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "~" and i + 1 < len(pattern) and pattern[i + 1] in "*?~":
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "*" else "." if ch == "?" else re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE | re.DOTALL)


def make_matcher(criterion):
    """A function(cell value) -> bool that says whether a cell meets a SUMIF/COUNTIF criterion."""
    if isinstance(criterion, ExcelError):
        return lambda value: False
    if criterion is None:
        criterion = 0
    if isinstance(criterion, bool):
        return lambda value: value is criterion
    if is_number(criterion):
        wanted = as_number(criterion)

        def numeric_equal(value):
            if is_number(value):
                return as_number(value) == wanted
            parsed = numeric_text(value)
            return parsed is not None and parsed == wanted
        return numeric_equal

    text = str(criterion)
    operator = next((op for op in (">=", "<=", "<>", ">", "<", "=") if text.startswith(op)), "")
    operand = text[len(operator):]
    number = numeric_text(operand)

    if operator in (">", "<", ">=", "<="):
        compare = {">": lambda a, b: a > b, "<": lambda a, b: a < b, ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b}[operator]
        if number is not None:
            return lambda value: is_number(value) and compare(as_number(value), number)
        return lambda value: isinstance(value, str) and compare(value.casefold(), operand.casefold())

    if operator == "<>":
        if operand == "":
            return lambda value: value is not None and value != ""
        equal = make_matcher(operand if operator == "<>" else text)
        return lambda value: not equal(value)

    # plain equality (with or without "=")
    if operand == "":
        return lambda value: value is None or value == ""
    if number is not None:
        def equal_number(value):
            if is_number(value):
                return as_number(value) == number
            parsed = numeric_text(value)
            return (parsed is not None and parsed == number) or (isinstance(value, str) and value.strip().casefold() == operand.strip().casefold())
        return equal_number
    regex = _wildcard_regex(operand)
    return lambda value: isinstance(value, str) and regex.match(value) is not None


# ---------------------------------------------------------------------------------------------------------------
# THE EVALUATOR
# ---------------------------------------------------------------------------------------------------------------
class Evaluator:
    def __init__(self, workbook):
        self.workbook = workbook
        self._memo = {}
        self._active = set()

    # --- cells ------------------------------------------------------------------------------------------------------
    def sheet(self, name):
        data = self.workbook.sheets.get(name)
        if data is None:
            raise EvalError("#REF!")
        return data

    def cell_value(self, sheet_name, row, col):
        """The value of one cell. A formula whose stored result is missing is calculated (and remembered)."""
        sheet = self.sheet(sheet_name)
        cell = sheet.get(row, col)
        if cell is None:
            return None
        if not cell.is_formula or cell.value is not None:
            return ExcelError(cell.value) if isinstance(cell.value, str) and cell.value in ERROR_VALUES else cell.value
        key = (sheet_name, row, col)
        if key in self._memo:
            return self._memo[key]
        if key in self._active:
            raise Unsupported("circular reference")
        self._active.add(key)
        try:
            try:
                result = self.evaluate(cell.formula, sheet_name)
            except EvalError as error:
                result = ExcelError(error.code)
        finally:
            self._active.discard(key)
        self._memo[key] = result
        return result

    def range_values(self, ref: Ref, current_sheet: str) -> list:
        """The values of a reference as a list of rows (a single cell is a 1x1 range)."""
        name = ref.sheet or current_sheet
        sheet = self.sheet(name)
        if ref.kind == "row":
            raise Unsupported("whole-row reference")
        last_row = ref.r2 if ref.r2 is not None else sheet.max_row
        return [[self.cell_value(name, r, c) for c in range(ref.c1, ref.c2 + 1)] for r in range(ref.r1, last_row + 1)]

    # --- formulas ---------------------------------------------------------------------------------------------------
    def evaluate(self, formula: str, sheet_name: str):
        """The result of a formula text. Raises Unsupported when it uses something not calculated here."""
        try:
            tree = parse(formula)
        except FormulaSyntax as error:
            raise Unsupported(str(error))
        try:
            value = self._eval(tree, sheet_name)
        except EvalError as error:
            return ExcelError(error.code)
        if isinstance(value, Ref):
            raise Unsupported("a range as a result")
        return value

    def _scalar(self, node, sheet):
        value = self._eval(node, sheet)
        if isinstance(value, list):
            if len(value) == 1 and len(value[0]) == 1:
                value = value[0][0]
            else:
                raise Unsupported("a range where one value is needed")
        if isinstance(value, ExcelError):
            raise EvalError(str(value))
        return value

    def _eval(self, node, sheet):
        if isinstance(node, Num):
            return node.value
        if isinstance(node, Str):
            return node.value
        if isinstance(node, Err):
            raise EvalError(node.code)
        if isinstance(node, Name):
            if node.name.upper() in ("TRUE", "FALSE"):
                return node.name.upper() == "TRUE"
            raise Unsupported(f"name {node.name}")
        if isinstance(node, Ref):
            return self.range_values(node, sheet)
        if isinstance(node, Neg):
            return -self._number(self._scalar(node.operand, sheet))
        if isinstance(node, Pct):
            return self._number(self._scalar(node.operand, sheet)) / 100
        if isinstance(node, Bin):
            return self._binary(node, sheet)
        if isinstance(node, Call):
            return self._call(node, sheet)
        raise Unsupported("unknown element")

    @staticmethod
    def _number(value):
        if value is None:
            return 0.0
        if isinstance(value, bool):
            return float(value)
        if is_number(value):
            return as_number(value)
        parsed = numeric_text(value)
        if parsed is None:
            raise EvalError("#VALUE!")
        return parsed

    @staticmethod
    def _text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (datetime, date)):
            value = serial(value)                       # joined into a text, a date is its number: "<"&A1 -> "<46175"
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    def _binary(self, node, sheet):
        left, right = self._scalar(node.left, sheet), self._scalar(node.right, sheet)
        op = node.op
        if op == "&":
            return self._text(left) + self._text(right)
        if op in ("=", "<>", "<", ">", "<=", ">="):
            return self._compare(op, left, right)
        a, b = self._number(left), self._number(right)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0:
                raise EvalError("#DIV/0!")
            return a / b
        if op == "^":
            try:
                return a ** b
            except (ZeroDivisionError, OverflowError, ValueError):
                raise EvalError("#NUM!")
        raise Unsupported(f"operator {op}")

    @staticmethod
    def _compare(op, left, right):
        def key(value):
            if value is None:
                return (0, 0.0)
            if isinstance(value, bool):
                return (2, float(value))
            if is_number(value):
                return (0, as_number(value))
            return (1, str(value).casefold())

        a, b = key(left), key(right)
        if left is None and isinstance(right, str):
            a = (1, "")
        if right is None and isinstance(left, str):
            b = (1, "")
        return {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]

    # --- functions ----------------------------------------------------------------------------------------------------
    def _call(self, node, sheet):
        name = node.name
        if name not in SUPPORTED_FUNCTIONS:
            raise Unsupported(f"function {name}")
        if name == "SUM":
            return self._sum(node.args, sheet)
        if name == "COUNTIF":
            return self._countif(node.args, sheet)
        if name == "SUMIF":
            return self._sumif(node.args, sheet)
        return self._ifs(name, node.args, sheet)

    def _numbers_in(self, values):
        total = 0.0
        for row in values:
            for value in row:
                if isinstance(value, ExcelError):
                    raise EvalError(str(value))
                if is_number(value):
                    total += as_number(value)
        return total

    def _sum(self, args, sheet):
        total = 0.0
        for arg in args:
            if arg is None:
                continue
            value = self._eval(arg, sheet)
            total += self._numbers_in(value) if isinstance(value, list) else self._number(value)
        return total

    def _range_arg(self, arg, sheet):
        if not isinstance(arg, Ref):
            raise Unsupported("a range was expected")
        return self.range_values(arg, sheet)

    def matching_cells(self, values: list, criterion_node, sheet) -> list:
        """[[bool]] shaped like `values`: which cells meet the criterion."""
        criterion = self._scalar(criterion_node, sheet) if criterion_node is not None else None
        matcher = make_matcher(criterion)
        return [[matcher(value) for value in row] for row in values]

    def criterion_value(self, criterion_node, sheet):
        """The value a criterion argument has (a literal, a cell's value, or a built text)."""
        return self._scalar(criterion_node, sheet) if criterion_node is not None else None

    def _countif(self, args, sheet):
        if len(args) != 2:
            raise EvalError("#VALUE!")
        return float(sum(sum(row) for row in self.matching_cells(self._range_arg(args[0], sheet), args[1], sheet)))

    def _sumif(self, args, sheet):
        if len(args) not in (2, 3):
            raise EvalError("#VALUE!")
        criteria_values = self._range_arg(args[0], sheet)
        hits = self.matching_cells(criteria_values, args[1], sheet)
        sum_values = criteria_values if len(args) == 2 or args[2] is None else self._shifted(args[2], sheet, criteria_values)
        return self._sum_where(sum_values, hits)

    def _shifted(self, ref_node, sheet, like):
        """The sum range of a SUMIF: starts at the first cell of its range and has the shape of the criteria range."""
        if not isinstance(ref_node, Ref):
            raise Unsupported("a range was expected")
        rows, cols = len(like), len(like[0]) if like else 0
        shaped = Ref(ref_node.sheet, "range", ref_node.r1, ref_node.c1, ref_node.r1 + rows - 1, ref_node.c1 + cols - 1)
        return self.range_values(shaped, sheet)

    def _sum_where(self, values, hits):
        total = 0.0
        for value_row, hit_row in zip(values, hits):
            for value, hit in zip(value_row, hit_row):
                if hit:
                    if isinstance(value, ExcelError):
                        raise EvalError(str(value))
                    if is_number(value):
                        total += as_number(value)
        return total

    def _ifs(self, name, args, sheet):
        offset = 1 if name == "SUMIFS" else 0
        pairs = args[offset:]
        if not pairs or len(pairs) % 2 or (name == "SUMIFS" and not args):
            raise EvalError("#VALUE!")
        combined = None
        for i in range(0, len(pairs), 2):
            values = self._range_arg(pairs[i], sheet)
            hits = self.matching_cells(values, pairs[i + 1], sheet)
            if combined is not None and (len(hits) != len(combined) or (hits and len(hits[0]) != len(combined[0]))):
                raise EvalError("#VALUE!")
            combined = hits if combined is None else [[a and b for a, b in zip(r1, r2)] for r1, r2 in zip(combined, hits)]
        if name == "COUNTIFS":
            return float(sum(sum(row) for row in combined))
        sum_values = self._range_arg(args[0], sheet)
        if len(sum_values) != len(combined) or (combined and len(sum_values[0]) != len(combined[0])):
            raise EvalError("#VALUE!")
        return self._sum_where(sum_values, combined)
