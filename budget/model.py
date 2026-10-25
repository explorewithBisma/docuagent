"""
budget/model.py
----------------
The shared vocabulary of the budget builder: the transaction Types, the Transactions
sheet columns, and the settings a generated workbook is built from.
"""

from dataclasses import dataclass, field
from datetime import date

# Direction of each Type (see budget/workbook.py for the Dashboard formulas):
#   Income, Loan received         -> money in
#   Expense, Loan payment, Saving -> money out of the balance
#   Card bill payment             -> moves money only; never counted as spending or in the net balance
INCOME, EXPENSE, LOAN_RECEIVED, LOAN_PAYMENT, SAVING, CARD_BILL = (
    "Income", "Expense", "Loan received", "Loan payment", "Saving", "Card bill payment"
)
TYPES = (INCOME, EXPENSE, LOAN_RECEIVED, LOAN_PAYMENT, SAVING, CARD_BILL)

TX_COLUMNS = ("Date", "Description", "Type", "Category", "Amount", "Payment method")

DEFAULT_PAYMENT_METHODS = ("Cash", "Card", "Credit card", "Bank transfer", "Wallet")
DEFAULT_CATEGORIES = {
    "Food": 0.0, "Transport": 0.0, "Bills": 0.0, "Shopping": 0.0, "Health": 0.0, "Entertainment": 0.0, "Other": 0.0,
}

MAX_CATEGORIES = 30
MAX_PAYMENT_METHODS = 10
TRANSACTION_ROWS = 1000          # rows 2..1001 of the Transactions sheet are formatted and counted


@dataclass
class BudgetConfig:
    """Everything the generated workbook needs apart from the transactions."""
    currency: str = "$"
    month_start: date = field(default_factory=lambda: date.today().replace(day=1))
    opening_balance: float = 0.0
    categories: dict = field(default_factory=lambda: dict(DEFAULT_CATEGORIES))   # name -> monthly budget
    payment_methods: list = field(default_factory=lambda: list(DEFAULT_PAYMENT_METHODS))

    def validate(self) -> list:
        """Human-readable problems with the settings (empty list = fine)."""
        problems = []
        if self.month_start.day != 1:
            problems.append("The month must start on the 1st.")
        names = [str(name).strip() for name in self.categories]
        if any(not name for name in names):
            problems.append("A category has no name.")
        if len({name.casefold() for name in names}) != len(names):
            problems.append("Category names must be unique (ignoring capitals).")
        if len(names) > MAX_CATEGORIES:
            problems.append(f"At most {MAX_CATEGORIES} categories are supported.")
        if any(budget is None or budget < 0 for budget in self.categories.values()):
            problems.append("A monthly budget cannot be negative.")
        methods = [str(method).strip() for method in self.payment_methods]
        if any(not method for method in methods) or len({m.casefold() for m in methods}) != len(methods):
            problems.append("Payment methods must be non-empty and unique.")
        if len(methods) > MAX_PAYMENT_METHODS:
            problems.append(f"At most {MAX_PAYMENT_METHODS} payment methods are supported.")
        return problems
