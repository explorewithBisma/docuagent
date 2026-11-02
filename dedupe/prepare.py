"""
dedupe/prepare.py
------------------
Normalises every identity column once (each DISTINCT value once, not once per row) and keeps the result row by row, so comparing two rows
later is cheap.
"""

from dataclasses import dataclass, field

import pandas as pd

from dedupe import normalize as nz
from quality import is_blank


@dataclass
class Prepared:
    n: int
    labels: list                                    # the data frame's row labels, by position
    names: list = field(default_factory=list)       # [NameKey]
    emails: list = field(default_factory=list)      # [frozenset of normalised emails]
    phones: list = field(default_factory=list)      # [list of (e164, last9)]
    phone_keys: list = field(default_factory=list)  # [frozenset of every e164 / last-9 key]: two rows share a phone when these overlap
    ids: list = field(default_factory=list)         # [frozenset of normalised ids]
    companies: list = field(default_factory=list)   # [normalised company text]
    addresses: list = field(default_factory=list)   # [sorted tuple of address words]
    records: list = field(default_factory=list)     # transactions: [tuple of comparable values per record column]
    record_columns: list = field(default_factory=list)
    completeness: list = field(default_factory=list)   # number of filled cells in the row (all columns)
    has: dict = field(default_factory=dict)         # which roles exist at all


def _mapped(series: pd.Series, function) -> list:
    """function(value) for every row, computed once per distinct value."""
    cache = {}
    out = []
    for value in series.tolist():
        key = value if not isinstance(value, float) or value == value else "__nan__"
        if key not in cache:
            cache[key] = function(value)
        out.append(cache[key])
    return out


def _record_value(value):
    """A value of a transaction record, normalised for comparison (text, number or date)."""
    if is_blank(value):
        return None
    if hasattr(value, "year"):
        return ("d", pd.Timestamp(value).date())
    if isinstance(value, bool):
        return ("t", str(value).lower())
    if isinstance(value, (int, float)):
        return ("n", round(float(value), 9))
    return ("t", nz.norm_text(value))


def prepare(df: pd.DataFrame, profile, locale: str = "PK", variants: dict = None, gmail_rule: bool = False) -> Prepared:
    variants = variants if variants is not None else nz.variant_map(nz.load_variant_groups())
    n = len(df)
    P = Prepared(n=n, labels=list(df.index))
    P.completeness = df.notna().sum(axis=1).tolist() if n else []          # filled cells per row (the cleaner has made blanks real blanks)

    name_columns = sorted(profile.used("name"), key=lambda r: r.order)
    if name_columns:
        parts = [_mapped(df[r.column], lambda v: "" if is_blank(v) else str(v).strip()) for r in name_columns]
        joined = [" ".join(p for p in row if p) for row in zip(*parts)]
        cache = {}
        P.names = [cache.setdefault(text, nz.name_key(text, variants)) for text in joined]
    else:
        P.names = [nz.NameKey("")] * n
    P.has["name"] = bool(name_columns)

    def many(role, function):
        columns = profile.used(role)
        P.has[role] = bool(columns)
        if not columns:
            return [()] * n
        per_column = [_mapped(df[r.column], function) for r in columns]
        return [tuple(v for v in row if v) for row in zip(*per_column)]

    P.emails = [frozenset(v) for v in many("email", lambda v: nz.norm_email(v, gmail_rule))]
    P.ids = [frozenset(v) for v in many("id", nz.norm_id)]
    phone_columns = profile.used("phone")
    P.has["phone"] = bool(phone_columns)
    if phone_columns:
        per_column = [_mapped(df[r.column], lambda v: nz.norm_phone(v, locale)) for r in phone_columns]
        P.phones = [[p for p in row if p[0] or p[1]] for row in zip(*per_column)]
    else:
        P.phones = [[] for _ in range(n)]
    P.phone_keys = [frozenset(k for p in row for k in p if k) for row in P.phones]
    companies = profile.used("company")
    P.has["company"] = bool(companies)
    P.companies = _mapped(df[companies[0].column], nz.norm_company) if companies else [""] * n
    addresses = profile.used("address")
    P.has["address"] = bool(addresses)
    P.addresses = _mapped(df[addresses[0].column], nz.norm_address) if addresses else [()] * n

    if profile.kind == "transactions":
        columns = [r.column for r in profile.used("record", "name", "email", "phone", "id", "company", "address")]
        P.record_columns = columns
        per_column = [_mapped(df[c], _record_value) for c in columns]
        P.records = [tuple(row) for row in zip(*per_column)]
    return P
