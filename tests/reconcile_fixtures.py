"""
A SYNTHETIC bank statement and ledger (invented merchants and amounts) with GROUND TRUTH, for the "Match two files" tests.

  bank_vs_ledger(seed)      -> (bank, ledger, truth)    a few hundred rows with every kind of case planted
  big_pair(n, seed)         -> (A, B, truth)            n x n rows, for the timing test

bank   : Date, Description, Reference, Debit, Credit, Balance          (debit / credit columns, dates as text 2026-03-05)
ledger : Date, Memo, Doc No, Amount                                    (one signed amount: negative = money out, dates as 05/03/2026)

truth.a[row] = (bucket, partner row in the ledger or None); truth.b[row] = (bucket, partner row in the bank or None)
bucket is one of exact | close | maybe | mismatch | only.
"""

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

MERCHANTS = ["Starbucks Coffee", "Carrefour Market", "Etisalat Bill", "Uber Trip", "Amazon Marketplace", "Noon Online", "Shell Petrol Station", "DEWA Utilities",
             "Spinneys Grocery", "Emirates Airline", "Careem Ride", "Talabat Food Order", "Lulu Hypermarket", "IKEA Home", "Zara Fashion", "Netflix Subscription",
             "Gold's Gym Membership", "Pharmacy Boots", "Cinema Vox", "Salary Transfer"]


@dataclass
class Truth:
    a: dict = field(default_factory=dict)
    b: dict = field(default_factory=dict)
    kinds: dict = field(default_factory=dict)            # kind name -> number of pairs planted


def typo(text: str, rng: random.Random) -> str:
    if len(text) < 6:
        return text
    i = rng.randrange(1, len(text) - 2)
    return text[:i] + text[i + 1] + text[i] + text[i + 2:] if rng.random() < 0.5 else text[:i] + text[i + 1:]


class Builder:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.used_cents = set()
        self.a_rows, self.b_rows = [], []                # dict rows with a hidden "_key"
        self.truth_pairs = []                            # (a position, b position or None, bucket)
        self.kinds = {}
        self.counter = 0

    def amount(self, low=500, high=250000) -> int:
        while True:
            cents = self.rng.randint(low, high)
            if cents not in self.used_cents:
                self.used_cents.add(cents)
                return cents

    def day(self) -> date:
        return date(2026, 1, 1) + timedelta(days=self.rng.randint(0, 330))

    def ref(self) -> str:
        self.counter += 1
        return f"TX{self.counter:07d}"

    def add_a(self, when, text, ref, cents, outflow=True):
        self.a_rows.append({"date": when, "text": text, "ref": ref, "cents": -cents if outflow else cents})
        return len(self.a_rows) - 1

    def add_b(self, when, text, ref, cents, outflow=True):
        self.b_rows.append({"date": when, "text": text, "ref": ref, "cents": -cents if outflow else cents})
        return len(self.b_rows) - 1

    def plant(self, kind, a, b, bucket):
        self.truth_pairs.append((a, b, bucket))
        self.kinds[kind] = self.kinds.get(kind, 0) + 1


def _frames(builder: Builder, shuffle_seed: int):
    rng = random.Random(shuffle_seed)
    order_a = list(range(len(builder.a_rows)))
    order_b = list(range(len(builder.b_rows)))
    rng.shuffle(order_a)
    rng.shuffle(order_b)
    position_a = {old: new for new, old in enumerate(order_a)}
    position_b = {old: new for new, old in enumerate(order_b)}
    bank = []
    for old in order_a:
        r = builder.a_rows[old]
        out = r["cents"] < 0
        bank.append({"Date": r["date"].isoformat(), "Description": r["text"], "Reference": r["ref"],
                     "Debit": f"{abs(r['cents']) / 100:.2f}" if out else None, "Credit": None if out else f"{abs(r['cents']) / 100:.2f}", "Balance": f"{rng.randint(1000, 90000)}.00"})
    ledger = []
    for old in order_b:
        r = builder.b_rows[old]
        ledger.append({"Date": r["date"].strftime("%d/%m/%Y"), "Memo": r["text"], "Doc No": r["ref"], "Amount": r["cents"] / 100})
    truth = Truth(kinds=dict(builder.kinds))
    paired_a, paired_b = set(), set()
    for a, b, bucket in builder.truth_pairs:
        if a is not None:
            truth.a[position_a[a]] = (bucket, position_b[b] if b is not None else None)
            paired_a.add(a)
        if b is not None:
            truth.b[position_b[b]] = (bucket, position_a[a] if a is not None else None)
            paired_b.add(b)
    return pd.DataFrame(bank), pd.DataFrame(ledger), truth


def bank_vs_ledger(seed: int = 5):
    g = Builder(seed)
    rng = g.rng
    # 1. exact: the same reference, amount and date (the ledger may write the description differently)
    for _ in range(120):
        m, d, c = rng.choice(MERCHANTS), g.day(), g.amount()
        ref = g.ref()
        a = g.add_a(d, m.upper(), ref, c)
        b = g.add_b(d, m, ref, c)
        g.plant("exact_reference", a, b, "exact")
    # 2. exact without a reference on the ledger side: same amount + date + similar description
    for _ in range(30):
        m, d, c = rng.choice(MERCHANTS), g.day(), g.amount()
        a = g.add_a(d, f"POS {rng.randint(1000, 9999)} {m}", g.ref(), c)
        b = g.add_b(d, m, "", c)
        g.plant("amount_date_description", a, b, "exact")
    # 3. description with a typo, same amount and date
    for _ in range(20):
        m, d, c = rng.choice(MERCHANTS), g.day(), g.amount()
        a = g.add_a(d, m, "", c)
        b = g.add_b(d, typo(m, rng), "", c)
        g.plant("description_typo", a, b, "exact")
    # 4. close: the ledger date is 1-2 days later (or earlier)
    for _ in range(30):
        m, d, c = rng.choice(MERCHANTS), g.day(), g.amount()
        a = g.add_a(d, m, "", c)
        b = g.add_b(d + timedelta(days=rng.choice([-2, -1, 1, 2])), m, "", c)
        g.plant("date_shifted", a, b, "close")
    # 5. maybe: a rounding difference in the amount (0.1 - 0.4 %), same or nearby date
    for _ in range(10):
        m, d, c = rng.choice(MERCHANTS), g.day(), g.amount(20000, 250000)
        a = g.add_a(d, m, "", c)
        cents_b = c + max(1, int(c * rng.uniform(0.001, 0.004)))
        while cents_b in g.used_cents:
            cents_b += 1
        g.used_cents.add(cents_b)
        b = g.add_b(d + timedelta(days=rng.choice([0, 1])), m, "", cents_b)
        g.plant("rounding_difference", a, b, "maybe")
    # 6. mismatch: the same reference but a different amount
    for _ in range(10):
        m, d, c = rng.choice(MERCHANTS), g.day(), g.amount()
        ref = g.ref()
        a = g.add_a(d, m, ref, c)
        b = g.add_b(d, m, ref, g.amount(c + 5000, c + 20000))
        g.plant("reference_amount_mismatch", a, b, "mismatch")
    # 7. the same amount on the same day twice: each bank row must get its own ledger row (by description), never both the same one
    for _ in range(6):
        d, c = g.day(), g.amount()
        first, second = rng.sample(MERCHANTS, 2)
        a1, a2 = g.add_a(d, first, "", c), g.add_a(d, second, "", c)
        b1, b2 = g.add_b(d, second, "", c), g.add_b(d, first, "", c)
        g.plant("duplicate_amount_same_day", a1, b2, "exact")
        g.plant("duplicate_amount_same_day", a2, b1, "exact")
    # 8. the same amount on the same day twice in the bank, once in the ledger: ONE match, the other row is only in the bank
    for _ in range(6):
        d, c = g.day(), g.amount()
        first, second = rng.sample(MERCHANTS, 2)
        a1, a2 = g.add_a(d, first, "", c), g.add_a(d, second, "", c)
        b1 = g.add_b(d, first, "", c)
        g.plant("double_in_bank_single_in_ledger", a1, b1, "exact")
        g.plant("double_in_bank_single_in_ledger", a2, None, "only")
    # 9. items in only one file
    for _ in range(15):
        a = g.add_a(g.day(), rng.choice(MERCHANTS), g.ref(), g.amount())
        g.plant("only_in_bank", a, None, "only")
    for _ in range(15):
        b = g.add_b(g.day(), rng.choice(MERCHANTS), g.ref(), g.amount())
        g.plant("only_in_ledger", None, b, "only")
    # 10. money in (a salary): positive in the ledger, a credit in the bank
    for _ in range(8):
        d, c = g.day(), g.amount(300000, 900000)
        ref = g.ref()
        a = g.add_a(d, "Salary Transfer", ref, c, outflow=False)
        b = g.add_b(d, "Salary", ref, c, outflow=False)
        g.plant("money_in", a, b, "exact")
    return _frames(g, seed + 100)


def big_pair(n: int = 10000, seed: int = 11):
    """n rows each: about 70% share a reference, 15% the same amount + date, 5% a date shift, 3% a rounding difference, the rest only in one file."""
    g = Builder(seed)
    rng = g.rng
    for _ in range(n):
        kind = rng.random()
        m, d = rng.choice(MERCHANTS), g.day()
        c = g.amount(100, 9_000_000)
        if kind < 0.70:
            ref = g.ref()
            g.plant("exact_reference", g.add_a(d, m.upper(), ref, c), g.add_b(d, m, ref, c), "exact")
        elif kind < 0.85:
            g.plant("amount_date_description", g.add_a(d, m, "", c), g.add_b(d, m, "", c), "exact")
        elif kind < 0.90:
            g.plant("date_shifted", g.add_a(d, m, "", c), g.add_b(d + timedelta(days=rng.choice([-2, -1, 1, 2])), m, "", c), "close")
        elif kind < 0.93:
            cents_b = c + max(1, int(c * 0.002))
            while cents_b in g.used_cents:
                cents_b += 1
            g.used_cents.add(cents_b)
            g.plant("rounding_difference", g.add_a(d, m, "", c), g.add_b(d, m, "", cents_b), "maybe")
        elif kind < 0.965:
            g.plant("only_in_bank", g.add_a(d, m, g.ref(), c), None, "only")
        else:
            g.plant("only_in_ledger", None, g.add_b(d, m, g.ref(), c), "only")
    return _frames(g, seed + 100)
