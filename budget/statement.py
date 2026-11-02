"""
budget/statement.py
--------------------
Recognises a BANK STATEMENT among the uploaded sheets and reads what only a statement can tell:

  * which columns are the date, the description, the debit / credit columns or the one signed amount - found by the same detection
    the "Match two files" tool uses (reconcile/columns.py) - and the running BALANCE column;
  * whether the rows are oldest-first or newest-first, and whether money out is written as a minus sign or in a debit column, by
    checking the running balance row by row ("balance after = balance before + amount");
  * the opening balance (the balance before the first transaction of the month) and the closing balance (after the last one).

The balance column is used for those two figures and as a check, never as an amount. Nothing here changes the user's file.
"""

import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from budget.importer import parse_amount
from reconcile import columns as rc, prepare as rp
from toolkit.report import Finding

MIN_CHAIN_SHARE = 0.8              # at least this share of consecutive rows must obey "balance after = balance before + amount"
MIN_CHAIN_PAIRS = 3
TOLERANCE = 0.011                  # a cent of rounding
TRANSFER_WORDS = r"\b(transfer|trf|ibft|fund transfer|remittance)\b"


@dataclass
class StatementInfo:
    sheet: str
    columns: dict                   # role -> column name (date, description, amount | debit + credit, balance)
    money_mode: str                 # signed | debit_credit
    sign: str                       # same | opposite: whether the file's signs had to be turned around
    order: str                      # oldest_first | newest_first
    chain_share: float              # share of consecutive rows that obey the balance arithmetic
    transactions: int
    flows: dict = field(default_factory=dict)        # Excel row -> {"signed": money in +, out -, "balance": after, "rank": position in time, "date": date or None}
    notes: list = field(default_factory=list)
    swapped: bool = False

    @property
    def confidence(self) -> str:
        return "high" if self.chain_share >= 0.95 else "medium"

    def describe(self) -> str:
        money = (f"one amount column '{self.columns['amount']}'" if self.money_mode == "signed"
                 else f"debit column '{self.columns.get('debit')}' and credit column '{self.columns.get('credit')}'")
        order = "newest first" if self.order == "newest_first" else "oldest first"
        return (f"{self.transactions} transactions; date '{self.columns['date']}', description '{self.columns['description']}', {money}, "
                f"running balance '{self.columns['balance']}'; rows are {order}")

    def source(self) -> str:
        sign = ("money out is a minus sign" if self.money_mode == "signed" else "debits are money out") if self.sign == "same" else \
            "the file's signs were turned around to fit its balance"
        return (f"the running balance adds up on {self.chain_share:.0%} of the rows ({sign}); columns found like in 'Match two files'")

    # ---- balances -------------------------------------------------------------------------------------------------------
    def with_dates(self, rows: pd.DataFrame) -> "StatementInfo":
        """The statement with the dates the importer read (Source row -> Date) - the same dates the budget uses."""
        dates = {int(r["Source row"]): r["Date"] for _, r in rows.iterrows() if isinstance(r["Date"], date)}
        flows = {src: {**flow, "date": dates.get(src)} for src, flow in self.flows.items()}
        return StatementInfo(**{**self.__dict__, "flows": flows})

    def _sequence(self, month_start: date) -> list:
        """Flows dated in the month, in the order things happened."""
        end = date(month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1)
        inside = [(src, f) for src, f in self.flows.items() if isinstance(f["date"], date) and month_start <= f["date"] < end and f["balance"] is not None]
        return sorted(inside, key=lambda item: (item[1]["date"], item[1]["rank"]))

    def opening(self, month_start: date):
        """Finding for the balance before the first transaction of the month (None if no transaction has a balance)."""
        sequence = self._sequence(month_start)
        if not sequence:
            return None
        src, first = sequence[0]
        value = round(first["balance"] - first["signed"], 2)
        return Finding(value, "high" if self.chain_share >= 0.95 else "medium",
                       f"the statement's balance column: {first['balance']:,.2f} after the first transaction of {month_start:%B %Y} "
                       f"({first['date']:%d %b}, row {src}), before its {first['signed']:+,.2f}")

    def closing(self, month_start: date):
        sequence = self._sequence(month_start)
        if not sequence:
            return None
        src, last = sequence[-1]
        return Finding(round(last["balance"], 2), "high" if self.chain_share >= 0.95 else "medium",
                       f"the statement's balance column after the last transaction of {month_start:%B %Y} ({last['date']:%d %b}, row {src})")


# ---------------------------------------------------------------------------------------------------------------
# DETECTION
# ---------------------------------------------------------------------------------------------------------------
def _chain(amounts: list, balances: list):
    """-> (order, flip, share): how the running balance relates to the amounts, the best of 4 readings.
    order 'oldest_first': balance[i] = balance[i-1] + s * amount[i];  'newest_first': balance[i-1] = balance[i] + s * amount[i-1];  s = -1 when flipped."""
    best = None
    for order in ("oldest_first", "newest_first"):
        for flip in (False, True):
            sign = -1.0 if flip else 1.0
            hits = pairs = 0
            for i in range(1, len(amounts)):
                newer, older = (i, i - 1) if order == "oldest_first" else (i - 1, i)
                if balances[newer] is None or balances[older] is None or amounts[newer] is None:
                    continue
                pairs += 1
                if abs(balances[newer] - (balances[older] + sign * amounts[newer])) <= TOLERANCE:
                    hits += 1
            share = hits / pairs if pairs else 0.0
            if best is None or share > best[2] + 1e-9:          # on a tie the plain reading wins (oldest first, signs as written)
                best = (order, flip, share, pairs)
    return best


def detect(raw_sheets: dict, default_day_first: bool = True):
    """-> (StatementInfo, sheet name, importer layout, raw_sheets ready for the importer) for the sheet that is a bank statement, or None.

    A sheet is a statement when it has a date, a description, a money column (or a debit and a credit column) AND a running-balance column
    whose numbers follow from the amounts row by row. The returned raw sheets differ from the uploaded ones only in one column: when the
    balance proves that the file writes money out the other way round, the amounts are turned around (a copy; the file is untouched)."""
    best = None
    for name, raw in raw_sheets.items():
        if len(raw) < 5:
            continue
        table, header_row = rc.table_from_raw(raw)
        if len(table) < MIN_CHAIN_PAIRS + 1:
            continue
        mapping = rc.detect_mapping(table, default_day_first)
        if not (mapping.column("date") and mapping.column("description") and mapping.money_mode):
            continue
        balance = rc.detect_balance(table, mapping)
        if not balance:
            continue
        side = rp.prepare_side(table, mapping, header_row)
        amounts = [None if pd.isna(a) else float(a) for a in side.amounts]
        balances = [parse_amount(v) for v in table[balance]]
        order, flip, share, pairs = _chain(amounts, balances)
        if share < MIN_CHAIN_SHARE or pairs < MIN_CHAIN_PAIRS:
            continue
        if best is None or len(table) > best[0]:
            best = (len(table), name, table, header_row, mapping, balance, amounts, balances, order, flip, share)
    if best is None:
        return None
    _, name, table, header_row, mapping, balance, amounts, balances, order, flip, share = best
    sign = -1.0 if flip else 1.0
    n = len(table)
    flows = {}
    for i, src in enumerate(rc.source_rows(table)):
        if amounts[i] is None:
            continue
        flows[int(src)] = {"signed": sign * amounts[i], "balance": balances[i], "rank": i if order == "oldest_first" else n - i, "date": None}
    columns = {role: mapping.column(role) for role in ("date", "description", "amount", "debit", "credit") if mapping.column(role)}
    columns["balance"] = balance
    notes = []
    if flip:
        notes.append("The running balance shows that this file writes money in and out the opposite way round from its column names; the amounts were turned around.")
    if order == "newest_first":
        notes.append("The statement lists the newest transaction first; the budget puts them in date order.")
    info = StatementInfo(sheet=name, columns=columns, money_mode=mapping.money_mode, sign="opposite" if flip else "same", order=order, chain_share=share,
                         transactions=len(flows), flows=flows, notes=notes, swapped=flip)

    # --- what the importer needs: the layout (raw column numbers) and the amounts the right way round -------------------------
    positions = dict(zip(table.columns, rc.source_columns(table)))
    layout_columns = {"date": positions[columns["date"]], "description": positions[columns["description"]]}
    raw_out = dict(raw_sheets)
    if mapping.money_mode == "signed":
        layout_columns["amount"] = positions[columns["amount"]]
        if flip:
            sheet = raw_sheets[name].copy()
            column = layout_columns["amount"]
            sheet.iloc[:, column] = [None if (a := parse_amount(v)) is None else -a for v in sheet.iloc[:, column]]
            raw_out[name] = sheet
    else:
        debit, credit = columns.get("debit"), columns.get("credit")
        if flip:
            debit, credit = credit, debit
        if debit:
            layout_columns["debit"] = positions[debit]
        if credit:
            layout_columns["credit"] = positions[credit]
    used = set(layout_columns.values())
    layout = {"header_row": header_row, "columns": layout_columns,
              "ignored": [str(c) for c, p in positions.items() if p not in used and c != balance]}
    return info, name, layout, raw_out


# ---------------------------------------------------------------------------------------------------------------
# THE CLOSING-BALANCE CHECK AND TRANSFERS
# ---------------------------------------------------------------------------------------------------------------
def check(info: StatementInfo, rows: pd.DataFrame, month_start: date, opening: float, dashboard_closing: float, card_type: str):
    """Does the budget reproduce the statement's closing balance? -> dict or None.

    The Dashboard does not count 'Card bill payment' rows (card bills and transfers between your own accounts: they move money, they are
    not spending), so its closing balance differs from the bank's by exactly those rows. They are added back here before comparing."""
    theirs = info.closing(month_start)
    if theirs is None:
        return None
    excluded = unknown = 0.0
    counted = 0
    for _, row in rows[rows["Type"] == card_type].iterrows():
        flow = info.flows.get(int(row["Source row"])) if pd.notna(row.get("Source row")) else None
        inside = isinstance(row["Date"], date) and row["Date"].replace(day=1) == month_start
        if not inside:
            continue
        if flow is None:
            unknown += float(row["Amount"] or 0)
            continue
        excluded += flow["signed"]
        counted += 1
    ours = dashboard_closing + excluded
    return {"theirs": float(theirs.value), "dashboard": dashboard_closing, "excluded": excluded, "excluded_rows": counted, "unknown": unknown,
            "ours": ours, "difference": ours - float(theirs.value), "source": theirs.source}


def check_text(result: dict, currency: str = "") -> str:
    cur = f"{currency} " if currency else ""
    text = (f"Closing balance check: the statement ends the month at {cur}{result['theirs']:,.2f}; the budget gives {cur}{result['ours']:,.2f} "
            f"(difference {result['difference']:,.2f}).")
    if result["excluded_rows"]:
        text += (f" That includes {result['excluded']:+,.2f} from {result['excluded_rows']} card bill payment / own-account transfer row(s), "
                 "which move money but are not counted as spending (so the Dashboard's own closing balance is "
                 f"{cur}{result['dashboard']:,.2f}).")
    if abs(result["difference"]) > 0.01:
        text += " A row may be missing, left out (see 'Rows not imported'), have the wrong type, or be outside this month."
    return text


def payment_method(description) -> str:
    """The payment method a bank's prefix tells: POS / ECOM -> Card, ATM -> Cash withdrawal, CHQ -> Cheque, IBFT / transfer -> Bank transfer ('' if none)."""
    text = str(description).strip().casefold()
    prefix = text.split(" ", 1)[0].split("-", 1)[0].split(":", 1)[0].split("/", 1)[0]
    if prefix in ("pos", "ecom", "e-com"):
        return "Card"
    if prefix == "atm":
        return "Cash withdrawal"
    if prefix in ("chq", "cheque"):
        return "Cheque"
    if prefix == "ibft" or re.search(TRANSFER_WORDS, text):
        return "Bank transfer"
    return ""


def fill_payment_methods(rows: pd.DataFrame) -> pd.DataFrame:
    """Rows without a payment method get the one their statement prefix tells (a copy)."""
    rows = rows.copy()
    for index, row in rows.iterrows():
        if pd.isna(row["Payment method"]) or not str(row["Payment method"]).strip():
            method = payment_method(row["Description"])
            if method:
                rows.at[index, "Payment method"] = method
    return rows


def transfer_rows(rows: pd.DataFrame, card_type: str) -> pd.DataFrame:
    """Rows that look like a bank transfer but were not recognised as moving your own money (Card bill payment) or saving it - the user decides."""
    pattern = re.compile(TRANSFER_WORDS, re.IGNORECASE)
    wanted = rows["Description"].astype(str).map(lambda d: pattern.search(d) is not None) & ~rows["Type"].isin([card_type, "Saving"])
    return rows[wanted]
