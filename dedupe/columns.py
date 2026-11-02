"""
dedupe/columns.py
------------------
Decides, from the headers AND the values, which columns say WHO a row is (name, email, phone, id, company, address) and which
columns differ legitimately between two rows of the same person (dates, amounts, quantities, status), with a plain reason for each.

It also decides what kind of file this is:
  contacts      - a list of people or companies: the same person on two rows is a duplicate; dates and amounts are ignored
  transactions  - the same customer appears on many rows on purpose (orders, invoices): rows are compared as WHOLE records,
                  and a different date or amount means a different record
"""

import re
from dataclasses import dataclass, field

import pandas as pd

from quality import _name_words, is_blank, is_phone_like

STRONG_ROLES = ("email", "phone", "id")
SUPPORT_ROLES = ("company", "address")

EMAIL_WORDS = {"email", "emails", "mail", "emailid"}
PHONE_WORDS = {"phone", "mobile", "cell", "tel", "telephone", "whatsapp", "fax", "gsm", "mob"}
ID_WORDS = {"id", "cnic", "passport", "nid", "ssn", "iqama", "emirates", "licence", "license", "nic", "tin", "vat", "trn", "aadhaar", "aadhar"}
TRANSACTION_WORDS = {"order", "invoice", "transaction", "txn", "receipt", "ticket"}      # an id of a purchase / document: the file lists transactions
COUNTER_WORDS = {"serial", "sr", "sno", "index", "row", "line", "record"}                 # a row counter
OTHER_ID_WORDS = {"product", "item", "sku", "category", "store", "branch", "region", "shipment", "delivery", "batch", "lot"}
ID_SUFFIX_WORDS = {"id", "code", "no", "number", "ref", "reference"}
PERSON_WORDS = {"customer", "client", "member", "employee", "student", "patient", "account", "staff"}
NAME_WORDS = {"name", "fullname", "firstname", "lastname", "surname", "forename", "given", "family", "customer", "client", "person",
              "employee", "student", "patient", "beneficiary", "owner", "applicant", "member", "contact"}
NAME_PARTS = {"first": 0, "given": 0, "forename": 0, "middle": 1, "last": 2, "surname": 2, "family": 2}
COMPANY_WORDS = {"company", "organisation", "organization", "employer", "business", "firm", "supplier", "vendor", "merchant"}
ADDRESS_WORDS = {"address", "street", "addr"}
NOT_A_NAME = {"product", "item", "file", "sheet", "user", "username", "category", "type", "status", "product_name", "brand", "city", "country"}
DATE_TEXT = re.compile(r"^\s*(\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4})([ T]\d{1,2}:\d{2}.*)?\s*$")


@dataclass
class ColumnRole:
    column: str
    role: str                       # name | email | phone | id | company | address | record | ignore
    used: bool
    why: str
    order: int = 0                  # position of a name part (first, middle, last)
    tag: str = ""                   # "transaction_id" (an order / invoice number) or "counter" (a row counter) for ignored record numbers

    @property
    def strong(self) -> bool:
        return self.role in STRONG_ROLES


@dataclass
class Profile:
    roles: list = field(default_factory=list)       # [ColumnRole] in column order
    kind: str = "contacts"                           # contacts | transactions | unknown
    kind_reason: str = ""
    kind_confidence: str = "medium"
    key_column: str = ""

    def used(self, *roles):
        return [r for r in self.roles if r.used and (not roles or r.role in roles)]

    def role_of(self, column):
        return next((r for r in self.roles if r.column == column), None)


def _values(series: pd.Series, limit: int = 400) -> list:
    return [v for v in series.dropna().head(limit) if not is_blank(v)]


def _share(values, test) -> float:
    return sum(1 for v in values if test(v)) / len(values) if values else 0.0


def _is_datetime_like(series: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    values = _values(series)
    if not values:
        return False
    return _share(values, lambda v: hasattr(v, "year") or (isinstance(v, str) and DATE_TEXT.match(v))) >= 0.8


def _is_numeric(series: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)


def _distinct_share(series: pd.Series) -> float:
    values = series.dropna()
    return values.nunique() / len(values) if len(values) else 0.0


def classify_column(series: pd.Series, name) -> ColumnRole:
    """The role of one column with the reason for it."""
    tokens = _name_words(name)
    values = _values(series)
    n_rows = len(series.dropna())
    if n_rows == 0:
        return ColumnRole(name, "ignore", False, "empty column")

    # --- identity columns: the header says so, or the values do ------------------------------------------------------
    if tokens & EMAIL_WORDS or (values and _share(values, lambda v: isinstance(v, str) and "@" in v and " " not in v.strip()) >= 0.6):
        return ColumnRole(name, "email", True, f"header '{name}'" if tokens & EMAIL_WORDS else "most values look like email addresses")
    phone_header = tokens & PHONE_WORDS or ("contact" in tokens and not tokens & {"name", "person"})
    if phone_header or (not _is_numeric(series) and is_phone_like("", series)):
        return ColumnRole(name, "phone", True, f"header '{name}'" if phone_header else "most values look like phone numbers")
    if tokens & (TRANSACTION_WORDS | COUNTER_WORDS) and tokens & (ID_SUFFIX_WORDS | COUNTER_WORDS):
        return ColumnRole(name, "ignore", False, "a record number: each row has its own, so it says nothing about who the row is",
                          tag="transaction_id" if tokens & TRANSACTION_WORDS else "counter")
    if tokens & OTHER_ID_WORDS and tokens & (ID_WORDS | ID_SUFFIX_WORDS):
        return ColumnRole(name, "ignore", False, "an id of something else (not of the person or company)")
    person_id = tokens & ID_WORDS or (tokens & PERSON_WORDS and tokens & ID_SUFFIX_WORDS)
    if person_id:
        if _distinct_share(series) >= 0.999:
            return ColumnRole(name, "ignore", False, "an id, but every row has a different one, so it cannot show two rows are the same person")
        return ColumnRole(name, "id", True, f"header '{name}' (an id that repeats on some rows)")

    # --- name, company, address ------------------------------------------------------------------------------------------
    texty = values and _share(values, lambda v: isinstance(v, str) and re.search(r"[^\W\d_]", v) is not None) >= 0.8
    if tokens & COMPANY_WORDS and texty and not tokens & {"id", "code", "number", "no"}:
        return ColumnRole(name, "company", True, f"header '{name}'")
    if tokens & ADDRESS_WORDS and texty:
        return ColumnRole(name, "address", True, f"header '{name}'")
    part = next((order for word, order in NAME_PARTS.items() if word in tokens), None)
    if tokens & NAME_WORDS and not tokens & NOT_A_NAME and texty and not tokens & {"id", "code", "number", "no"}:
        mostly_words = _share(values, lambda v: isinstance(v, str) and len(v.split()) <= 6 and not re.search(r"\d{3,}", v)) >= 0.8
        if mostly_words:
            return ColumnRole(name, "name", True, f"header '{name}'" + (" (part of a name)" if part is not None else ""), order=part or 0)

    # --- what differs legitimately -----------------------------------------------------------------------------------------
    if _is_datetime_like(series):
        return ColumnRole(name, "ignore", False, "dates: two rows of the same person can have different dates")
    if _is_numeric(series):
        return ColumnRole(name, "ignore", False, "amounts or quantities: two rows of the same person can differ here")
    distinct = series.dropna().nunique()
    if distinct <= max(12, 0.05 * n_rows):
        return ColumnRole(name, "ignore", False, f"only {distinct} different values, shared by many rows (a status or category)")
    if values and sum(len(str(v)) for v in values) / len(values) > 60:
        return ColumnRole(name, "ignore", False, "long free text")
    return ColumnRole(name, "ignore", False, "does not look like a column that identifies a person or company")


def detect_roles(df: pd.DataFrame) -> list:
    return [classify_column(df[column], column) for column in df.columns]


def _key_column(roles):
    for wanted in ("id", "email", "phone"):
        found = [r for r in roles if r.used and r.role == wanted]
        if found:
            return found[0].column
    names = [r for r in roles if r.used and r.role == "name"]
    return names[0].column if names else ""


def detect_kind(df: pd.DataFrame, roles: list) -> tuple:
    """-> (kind, reason, confidence). See the module docstring."""
    if not any(r.used for r in roles):
        return "unknown", "No column that identifies a person or company was found.", "low"
    key = _key_column(roles)
    values = df[key].dropna() if key else pd.Series(dtype=object)
    values = values[values.astype(str).str.strip() != ""]
    counts = values.astype(str).str.casefold().str.strip().value_counts()
    repeat_share = float(counts[counts >= 2].sum() / len(values)) if len(values) else 0.0
    row_keys = [r for r in roles if r.tag == "transaction_id"]
    dates = [r.column for r in roles if not r.used and r.why.startswith("dates")]
    amounts = [r.column for r in roles if not r.used and r.why.startswith("amounts")]
    varying = 0
    groups = 0
    if key and (dates or amounts) and repeat_share > 0:
        repeated = df[df[key].astype(str).str.casefold().str.strip().isin(counts[counts >= 2].index)]
        for _, group in repeated.groupby(repeated[key].astype(str).str.casefold().str.strip()):
            groups += 1
            if any(group[c].dropna().astype(str).nunique() > 1 for c in dates + amounts):
                varying += 1
    variation_share = varying / groups if groups else 0.0
    if row_keys and (dates or amounts):
        return ("transactions", f"'{row_keys[0].column}' is a different order / document number on every row, next to "
                + ("dates" if dates else "amounts") + f" ('{(dates or amounts)[0]}'): each row is a transaction, not a person.", "high")
    if repeat_share >= 0.25 and variation_share >= 0.5:
        return ("transactions", f"'{key}' repeats on {repeat_share:.0%} of the rows and their dates/amounts differ: each row looks like a "
                "transaction, not a person.", "high" if repeat_share >= 0.4 else "medium")
    if not key:
        return "contacts", "No clear identifying column; treated as a list of people.", "low"
    return ("contacts", f"'{key}' repeats on only {repeat_share:.0%} of the rows, so each row is a person or company, not a transaction.",
            "high" if repeat_share < 0.15 else "medium")


def build_profile(df: pd.DataFrame, overrides: dict = None, mode: str = "auto") -> Profile:
    """Roles for every column (with the user's overrides applied: {column: role or 'ignore'}) and the file kind."""
    roles = detect_roles(df)
    for role in roles:
        wanted = (overrides or {}).get(role.column)
        if wanted and wanted != role.role:
            role.role, role.used = wanted, wanted != "ignore"
            role.why = "set by you"
            role.order = 0
    kind, reason, confidence = detect_kind(df, roles)
    if mode in ("contacts", "transactions"):
        kind, reason, confidence = mode, "chosen by you", "high"
    if kind == "transactions":
        for role in roles:                                     # whole records are compared: every column except the record number counts
            if not role.used and not role.tag and role.role == "ignore" and role.why not in ("empty column", "set by you"):
                role.role, role.used = "record", True
                role.why = "compared as part of the whole record (" + role.why.split(":")[0] + ")"
    profile = Profile(roles=roles, kind=kind, kind_reason=reason, kind_confidence=confidence, key_column=_key_column(roles))
    return profile
