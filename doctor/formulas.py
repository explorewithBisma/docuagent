"""
doctor/formulas.py
-------------------
A small, careful reader of Excel formula text: a tokenizer, a parser into a tree, and helpers to list the references and
function calls inside a formula, or to write a formula in "relative" form (R[1]C[0]) so formulas that do the same thing in
different rows compare equal.

It understands: numbers, "texts", cell references (A1, $A$1, A1:B5, A:A, 'My sheet'!A1), the operators + - * / ^ & = <> < >
<= >= and %, parentheses and function calls. Anything else (array constants, 3-D references ...) raises FormulaSyntax and the
formula is simply reported as "not checked". The Doctor never guesses.
"""

import re
from dataclasses import dataclass

from openpyxl.utils import column_index_from_string, get_column_letter

ERROR_LITERALS = ("#REF!", "#DIV/0!", "#N/A", "#NAME?", "#VALUE!", "#NUM!", "#NULL!")


class FormulaSyntax(Exception):
    """The text is not a formula this reader understands."""


# ---------------------------------------------------------------------------------------------------------------
# TOKENS
# ---------------------------------------------------------------------------------------------------------------
_SHEET = r"(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!"
_CELL = r"\$?[A-Za-z]{1,3}\$?\d+"
TOKEN_RE = re.compile("|".join([
    r"(?P<ws>\s+)",
    r'(?P<string>"(?:[^"]|"")*")',
    r"(?P<error>#REF!|#DIV/0!|#N/A|#NAME\?|#VALUE!|#NUM!|#NULL!)",
    r"(?P<func>[A-Za-z_][A-Za-z0-9_.]*(?=\())",
    rf"(?P<ref>(?:{_SHEET})?(?:{_CELL}(?::{_CELL})?|\$?[A-Za-z]{{1,3}}:\$?[A-Za-z]{{1,3}}(?![A-Za-z0-9_(])|\$?\d+:\$?\d+(?![\d(])))",
    r"(?P<number>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)",
    r"(?P<name>[A-Za-z_][A-Za-z0-9_.]*)",
    r"(?P<op><=|>=|<>|[-+*/^&=<>%])",
    r"(?P<lparen>\()", r"(?P<rparen>\))", r"(?P<comma>,)",
]))


@dataclass
class Token:
    kind: str
    text: str


def tokenize(text: str) -> list:
    tokens, position = [], 0
    while position < len(text):
        match = TOKEN_RE.match(text, position)
        if not match:
            raise FormulaSyntax(f"unexpected character {text[position]!r}")
        position = match.end()
        if match.lastgroup != "ws":
            tokens.append(Token(match.lastgroup, match.group()))
    return tokens


# ---------------------------------------------------------------------------------------------------------------
# TREE
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Num:
    value: float


@dataclass
class Str:
    value: str


@dataclass
class Err:
    code: str


@dataclass
class Name:
    name: str


@dataclass
class Ref:
    sheet: str            # None = the formula's own sheet
    kind: str             # "cell" | "range" | "col" | "row"
    r1: int
    c1: int
    r2: int               # None = to the end of the sheet (whole column)
    c2: int
    abs_r1: bool = False
    abs_c1: bool = False
    abs_r2: bool = False
    abs_c2: bool = False
    text: str = ""


@dataclass
class Call:
    name: str
    args: list


@dataclass
class Bin:
    op: str
    left: object
    right: object


@dataclass
class Neg:
    operand: object


@dataclass
class Pct:
    operand: object


def _split_cell(text: str):
    match = re.fullmatch(r"(\$?)([A-Za-z]{1,3})(\$?)(\d+)", text)
    return column_index_from_string(match.group(2).upper()), int(match.group(4)), bool(match.group(1)), bool(match.group(3))


def parse_ref(text: str) -> Ref:
    """A reference token such as 'My sheet'!$A$1:B5 -> Ref."""
    sheet = None
    body = text
    match = re.match(rf"({_SHEET})(.*)$", text)
    if match:
        raw = match.group(1)[:-1]
        sheet = raw[1:-1].replace("''", "'") if raw.startswith("'") else raw
        body = match.group(2)
    if re.fullmatch(rf"{_CELL}(?::{_CELL})?", body):
        first, _, last = body.partition(":")
        c1, r1, ac1, ar1 = _split_cell(first)
        if last:
            c2, r2, ac2, ar2 = _split_cell(last)
            return Ref(sheet, "range", min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2), ar1, ac1, ar2, ac2, text)
        return Ref(sheet, "cell", r1, c1, r1, c1, ar1, ac1, ar1, ac1, text)
    columns = re.fullmatch(r"(\$?)([A-Za-z]{1,3}):(\$?)([A-Za-z]{1,3})", body)
    if columns:
        c1, c2 = column_index_from_string(columns.group(2).upper()), column_index_from_string(columns.group(4).upper())
        return Ref(sheet, "col", 1, min(c1, c2), None, max(c1, c2), True, bool(columns.group(1)), True, bool(columns.group(3)), text)
    rows = re.fullmatch(r"(\$?)(\d+):(\$?)(\d+)", body)
    if rows:
        r1, r2 = int(rows.group(2)), int(rows.group(4))
        return Ref(sheet, "row", min(r1, r2), 1, max(r1, r2), None, bool(rows.group(1)), True, bool(rows.group(3)), True, text)
    raise FormulaSyntax(f"bad reference {text!r}")


# ---------------------------------------------------------------------------------------------------------------
# PARSER (precedence climbing)
# ---------------------------------------------------------------------------------------------------------------
_BINARY = {"=": 1, "<>": 1, "<": 1, ">": 1, "<=": 1, ">=": 1, "&": 2, "+": 3, "-": 3, "*": 4, "/": 4, "^": 5}


class _Parser:
    def __init__(self, tokens):
        self.tokens, self.position = tokens, 0

    def peek(self):
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self):
        token = self.peek()
        if token is None:
            raise FormulaSyntax("the formula ends too soon")
        self.position += 1
        return token

    def expression(self, minimum=1):
        left = self.unary()
        while True:
            token = self.peek()
            if token is None or token.kind != "op" or token.text not in _BINARY or _BINARY[token.text] < minimum:
                return left
            self.take()
            left = Bin(token.text, left, self.expression(_BINARY[token.text] + 1))

    def unary(self):
        token = self.peek()
        if token is not None and token.kind == "op" and token.text in "+-":
            self.take()
            operand = self.unary()
            return Neg(operand) if token.text == "-" else operand
        node = self.primary()
        while self.peek() is not None and self.peek().kind == "op" and self.peek().text == "%":
            self.take()
            node = Pct(node)
        return node

    def primary(self):
        token = self.take()
        if token.kind == "number":
            return Num(float(token.text))
        if token.kind == "string":
            return Str(token.text[1:-1].replace('""', '"'))
        if token.kind == "error":
            return Err(token.text)
        if token.kind == "ref":
            return parse_ref(token.text)
        if token.kind == "name":
            return Name(token.text)
        if token.kind == "func":
            self.take()                                               # the "("
            args = []
            if self.peek() is not None and self.peek().kind == "rparen":
                self.take()
                return Call(token.text.upper(), args)
            while True:
                nxt = self.peek()
                args.append(None if nxt is not None and nxt.kind in ("comma", "rparen") else self.expression())
                closing = self.take()
                if closing.kind == "rparen":
                    return Call(token.text.upper(), args)
                if closing.kind != "comma":
                    raise FormulaSyntax("expected a comma or a closing bracket")
        if token.kind == "lparen":
            node = self.expression()
            if self.take().kind != "rparen":
                raise FormulaSyntax("missing closing bracket")
            return node
        raise FormulaSyntax(f"unexpected {token.text!r}")


def parse(formula: str):
    """Formula text (with or without the leading '=') -> tree. Raises FormulaSyntax."""
    text = formula[1:] if formula.startswith("=") else formula
    parser = _Parser(tokenize(text))
    node = parser.expression()
    if parser.peek() is not None:
        raise FormulaSyntax("unexpected text after the formula")
    return node


# ---------------------------------------------------------------------------------------------------------------
# WALKING
# ---------------------------------------------------------------------------------------------------------------
def walk(node):
    """Every node of the tree, parents before children."""
    if node is None:
        return
    yield node
    if isinstance(node, Call):
        for arg in node.args:
            yield from walk(arg)
    elif isinstance(node, Bin):
        yield from walk(node.left)
        yield from walk(node.right)
    elif isinstance(node, (Neg, Pct)):
        yield from walk(node.operand)


def refs(node) -> list:
    return [n for n in walk(node) if isinstance(n, Ref)]


def calls(node) -> list:
    return [n for n in walk(node) if isinstance(n, Call)]


def to_relative(formula: str, row: int, col: int) -> str:
    """The formula with every reference written relative to its own cell (R[1]C[0], or R5C3 when absolute), so the
    formulas of a filled-down column have the same text. Returns the original text if it cannot be tokenized."""
    try:
        tokens = tokenize(formula[1:] if formula.startswith("=") else formula)
    except FormulaSyntax:
        return formula

    def one(r, c, abs_r, abs_c):
        return ("R%d" % r if abs_r else "R[%d]" % (r - row)) + ("C%d" % c if abs_c else "C[%d]" % (c - col))

    parts = []
    for token in tokens:
        if token.kind != "ref":
            parts.append(token.text)
            continue
        ref = parse_ref(token.text)
        prefix = token.text[:len(token.text) - len(re.sub(rf"^{_SHEET}", "", token.text))]
        if ref.kind in ("cell", "range"):
            first = one(ref.r1, ref.c1, ref.abs_r1, ref.abs_c1)
            last = "" if ref.kind == "cell" else ":" + one(ref.r2, ref.c2, ref.abs_r2, ref.abs_c2)
            parts.append(prefix + first + last)
        else:
            parts.append(token.text)
    return "".join(parts)


def column_name(index: int) -> str:
    return get_column_letter(index)
