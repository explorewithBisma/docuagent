from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Contact list or transaction list? Repeats that come in PAIRS are duplicates, not transactions (a regression found on a real file)."""
import sys
from datetime import date, timedelta

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import pandas as pd

import dedupe_fixtures as fx
from dedupe.analyze import DedupeOptions, find_near_duplicates

print("TEST: a customer list whose repeats are pairs is a CONTACT list - and the 7 planted pairs are found")
df = fx.pair_customer_list()
assert len(df) == 24
result = find_near_duplicates(df, DedupeOptions(locale="PK"))
print("  ", result.profile.kind, "|", result.profile.kind_reason[:110], "|", result.profile.kind_confidence)
print("  ", result.summary)
assert result.profile.kind == "contacts"
assert len(result.groups) == 7 and all(len(g.rows) == 2 for g in result.groups), result.summary
truth = {frozenset(df.index[df["Customer Name"].isin([fx.PAIR_PEOPLE[k][0], fx.PAIR_DUPLICATES[k][0]])]) for k in fx.PAIR_DUPLICATES}
assert {frozenset(g.rows) for g in result.groups} == truth
print("PASSED")

print("TEST: when the repeats are only pairs the file type is 'not sure' (a guess), says why, and defaults to the safe contact list")
assert result.profile.kind_confidence == "low" and result.profile.kind_reason.startswith("Not sure")
assert "twice" in result.profile.kind_reason
print("PASSED")

print("TEST: the same file still works when the user switches the type by hand")
forced = find_near_duplicates(df, DedupeOptions(locale="PK", mode="transactions"))
assert forced.profile.kind == "transactions" and forced.profile.kind_reason == "chosen by you"
print("PASSED")

print("TEST: real transactions are still a transaction list: customers who come back many times")
orders = pd.DataFrame({"Customer": [f"Person {i % 8}" for i in range(80)], "Email": [f"p{i % 8}@mail.test" for i in range(80)],
                       "Item": [("Tea", "Cake", "Coffee")[i % 3] for i in range(80)], "Quantity": [1 + i % 4 for i in range(80)],
                       "Order Date": [date(2026, 1, 1) + timedelta(days=i) for i in range(80)], "Amount": [3.5 + i for i in range(80)]})
kind = find_near_duplicates(orders, DedupeOptions(locale="PK")).profile
print("  ", kind.kind, "|", kind.kind_reason[:100])
assert kind.kind == "transactions" and kind.kind_confidence == "high" and "Item" in kind.kind_reason
numbered = orders.assign(**{"Order ID": [f"A{i:05d}" for i in range(80)]})
assert find_near_duplicates(numbered, DedupeOptions(locale="PK")).profile.kind == "transactions"
print("PASSED")

print("TEST: 'Last ...' and 'Total ...' columns are per-person, not shopping headers")
from dedupe.columns import _shopping_header
assert not _shopping_header("Last Order Date") and not _shopping_header("Total Spent") and not _shopping_header("Total Orders")
assert _shopping_header("Order Date") and _shopping_header("Item") and _shopping_header("Qty") and _shopping_header("Product Name")
print("PASSED")

RETAIL = _Path.home() / "Downloads" / "archive" / "retail_sales_dataset.csv"
if RETAIL.exists():
    print("TEST: the real retail file (not part of the repository) is still a transaction list with 0 groups")
    import changes as ch
    import cleaning
    raw = pd.read_csv(RETAIL, dtype=str, keep_default_na=False)
    out, _ = cleaning.clean_dataframe_with_steps(raw, {"headers": True, "trim": True, "duplicates": True, "empty": True}, {"changes": ch.ChangeLog()})
    retail = find_near_duplicates(out, DedupeOptions(locale="AE"))
    print("  ", retail.profile.kind, retail.profile.kind_confidence, "|", retail.summary)
    assert retail.profile.kind == "transactions" and retail.profile.kind_confidence == "high" and not retail.groups
    print("PASSED")
