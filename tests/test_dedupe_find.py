from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""dedupe: candidate pairs, explainable scores, groups - measured against the synthetic customer list's ground truth."""
import sys

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import pandas as pd

import dedupe_fixtures as fx
from dedupe.analyze import DedupeOptions, find_near_duplicates


def pairs_of(groups, level=None):
    out = set()
    for g in groups:
        if level is None or g.level == level:
            rows = sorted(g.rows)
            out |= {(a, b) for i, a in enumerate(rows) for b in rows[i + 1:]}
    return out


def measure(found, truth_pairs):
    true_positive = len(found & truth_pairs)
    return (true_positive / len(found) if found else 1.0), (true_positive / len(truth_pairs) if truth_pairs else 1.0)


print("TEST: ground truth on the customer list - every planted duplicate found, no look-alike grouped (precision / recall printed)")
df, truth = fx.customers(300)
result = find_near_duplicates(df, DedupeOptions(locale="PK"))
found = pairs_of(result.groups)
precision, recall = measure(found, truth.pairs())
high_precision, high_recall = measure(pairs_of(result.groups, "high"), truth.pairs("high"))
print(f"   all groups:  precision {precision:.3f}  recall {recall:.3f}   |   high-confidence: precision {high_precision:.3f}  recall {high_recall:.3f}")
print(f"   {result.summary}")
assert precision >= 0.98 and recall >= 0.95 and high_precision >= 0.98 and high_recall >= 0.9
assert not (found & truth.lookalike_pairs()), sorted(found & truth.lookalike_pairs())
assert found == truth.pairs() and pairs_of(result.groups, "high") == truth.pairs("high") and pairs_of(result.groups, "maybe") == truth.pairs("maybe")
print("PASSED")

print("TEST: precision and recall stay high on other random lists (5 seeds)")
total_found = total_true = total_tp = 0
for seed in range(21, 26):
    d, t = fx.customers(250, seed=seed)
    r = find_near_duplicates(d, DedupeOptions(locale="PK"))
    f = pairs_of(r.groups)
    assert not (f & t.lookalike_pairs()), (seed, sorted(f & t.lookalike_pairs()))
    total_found, total_true, total_tp = total_found + len(f), total_true + len(t.pairs()), total_tp + len(f & t.pairs())
    assert pairs_of(r.groups, "high") <= t.pairs("high") | t.pairs("maybe"), seed
print(f"   5 lists: precision {total_tp / total_found:.3f}  recall {total_tp / total_true:.3f}")
assert total_tp / total_found >= 0.98 and total_tp / total_true >= 0.95
print("PASSED")

print("TEST: each kind of planted duplicate is explained in words")
by_kind = {}
for group in truth.groups:
    found_group = next(g for g in result.groups if set(g.rows) == set(group["rows"]))
    by_kind[group["kind"]] = (found_group, " | ".join(found_group.reasons.values()))
assert "typo 'malik' / 'mailk'" in by_kind["typo"][1] and "spelling variant" in by_kind["variant"][1]
assert "initial 'm' for 'muhammad'" in by_kind["initial"][1] and "phone same" in by_kind["phone_format"][1] and "email same" in by_kind["email_case"][1]
assert "company same" in by_kind["name_company_address"][1] and "address same" in by_kind["name_company_address"][1]
assert "name same" in by_kind["case_space_punct"][1] and by_kind["triple"][0].level == "high" and len(by_kind["triple"][0].rows) == 3
assert by_kind["name_only"][0].level == "maybe" and "email blank in one row" in by_kind["name_only"][1]
assert by_kind["two_phones"][0].level == "maybe" and "phone differs" in by_kind["two_phones"][1]
assert by_kind["id_and_variant"][0].level == "high" and "ID same" in by_kind["id_and_variant"][1]
print("PASSED")

print("TEST: the default row to keep is the most complete one (then the earliest)")
for group in result.groups:
    filled = [int(df.iloc[r].notna().sum()) for r in group.rows]
    assert df.iloc[group.keep].notna().sum() == max(filled)
    assert group.keep == min(r for r in group.rows if int(df.iloc[r].notna().sum()) == max(filled))
name_only = by_kind["name_only"][0]
assert name_only.keep == min(name_only.rows), "the base row has every field; the duplicate only a name"
print("PASSED")

print("TEST: groups come high first, bigger first; every row is in at most one group; labels are the frame's own")
levels = [g.level for g in result.groups]
assert levels == sorted(levels, key=["high", "maybe"].index) and [g.id for g in result.groups] == list(range(1, len(result.groups) + 1))
rows_seen = [r for g in result.groups for r in g.rows]
assert len(rows_seen) == len(set(rows_seen))
shifted = df.copy()
shifted.index = shifted.index + 1000
labelled = find_near_duplicates(shifted, DedupeOptions(locale="PK"))
assert [g.labels for g in labelled.groups][0] == [r + 1000 for r in labelled.groups[0].rows]
print("PASSED")

print("TEST: strictness - strict hides the name-only match, loose adds a lone typo'd name")
strict = find_near_duplicates(df, DedupeOptions(strictness="strict"))
assert strict.stats["maybe"] < result.stats["maybe"] and strict.stats["high"] == result.stats["high"]
assert not any("only" in " ".join(g.reasons.values()) for g in strict.groups)
print("PASSED")

print("TEST: a chain A~B, B~C never joins an A and a C that are different people")
chain = pd.DataFrame({"Full Name": ["Sami Rao", "Sami Rao", "Sami Rao"], "Email": ["a@x.com", "a@x.com", "c@x.com"],
                      "Mobile": ["0300-1111111", "0300-2222222", "0300-2222222"]})
r = find_near_duplicates(chain)
sizes = sorted(len(g.rows) for g in r.groups)
assert sizes == [2], "A (email a, phone 1) and C (email c, phone 2) differ on both, so only one of the two links survives: " + str(sizes)
assert r.groups[0].level == "maybe" and 1 in r.groups[0].rows, "each link has one identifier the same and one different: a maybe"
print("PASSED")

print("TEST: a file with only a name column - 'Ali Khan' / 'ali  khan.' is a MAYBE, never high; strict mode hides it")
names_only = pd.DataFrame({"Name": ["Ali Khan", "ali  khan.", "Sara Raza", "Omar Farooq", "Zed Q"]})
r = find_near_duplicates(names_only)
assert [(g.level, g.rows) for g in r.groups] == [("maybe", [0, 1])] and r.groups[0].reasons[1] == "name same"
assert find_near_duplicates(names_only, DedupeOptions(strictness="strict")).groups == []
print("PASSED")

print("TEST: phone and email only (no name column): both the same = high, one the same = maybe")
contact = pd.DataFrame({"Email": ["a@x.com", "A@X.com", "b@x.com", "c@x.com"], "Phone": ["0300-1234567", "+92 300 1234567", "0300-1234567", "0311-9999999"]})
r = find_near_duplicates(contact, DedupeOptions(locale="PK"))
assert [(g.level, g.rows) for g in r.groups] == [("high", [0, 1])], "rows 0 and 2 share only the phone, and a maybe link never extends a high group"
one_link = find_near_duplicates(contact.iloc[[0, 2]].reset_index(drop=True), DedupeOptions(locale="PK"))
assert [(g.level, g.rows) for g in one_link.groups] == [("maybe", [0, 1])], "the same phone but another email: one person with two emails? a maybe"
print("PASSED")

print("TEST: very common names and shared lines do not blow up or pair strangers (and the report says what was skipped)")
crowd = pd.DataFrame({"Full Name": ["John Smith"] * 300, "Mobile": [f"0300-{1000000 + i}" for i in range(300)], "Email": [f"j{i}@x.com" for i in range(300)]})
r = find_near_duplicates(crowd)
assert r.groups == [] and any("names shared by too many rows" in n for n in r.notes), r.notes
office = pd.DataFrame({"Full Name": [f"Person {chr(65 + i % 26)}{i}" for i in range(500)], "Mobile": ["0300-1234567"] * 500})
r = find_near_duplicates(office)
assert r.groups == [] and any("phone numbers shared by too many rows" in n for n in r.notes)
print("PASSED")

print("TEST: nothing to compare, tiny and empty inputs")
nothing = find_near_duplicates(pd.DataFrame({"a": range(5), "b": [x * 1.5 for x in range(5)]}))
assert nothing.groups == [] and nothing.profile.kind == "unknown" and "nothing to compare" in " ".join(nothing.notes)
assert find_near_duplicates(pd.DataFrame({"Name": ["Ali Khan"]})).groups == [] and find_near_duplicates(pd.DataFrame({"Name": []})).groups == []
print("PASSED")

print("TEST: transaction lists - repeat customers with different dates/amounts are NOT duplicates; a double-entered order is")
orders = pd.DataFrame({
    "Order ID": [f"O{i}" for i in range(40)], "Order Date": pd.date_range("2026-01-01", periods=40).date,
    "Customer ID": [f"K{i % 8}" for i in range(40)], "Customer Name": [f"Customer {i % 8}" for i in range(40)],
    "Product": [f"Product {i % 5}" for i in range(40)], "City": [f"City {i % 4}" for i in range(40)], "Amount": [10.0 + i for i in range(40)]})
r = find_near_duplicates(orders)
assert r.profile.kind == "transactions" and r.groups == [], [(g.level, g.rows) for g in r.groups]
doubled = pd.concat([orders, orders.iloc[[3]].assign(**{"Order ID": "O3b", "Product": "PRODUCT 3", "City": " city 3 "})], ignore_index=True)
r = find_near_duplicates(doubled)
assert [(g.level, g.rows) for g in r.groups] == [("high", [3, 40])] and "match" in r.groups[0].reasons[40 if r.groups[0].keep == 3 else 3]
changed_amount = pd.concat([orders, orders.iloc[[3]].assign(**{"Order ID": "O3c", "Amount": 99.0})], ignore_index=True)
assert find_near_duplicates(changed_amount).groups == [], "a different amount is a different transaction"
forced = find_near_duplicates(orders, DedupeOptions(mode="contacts"))
assert forced.profile.kind == "contacts" and forced.profile.kind_reason == "chosen by you"
print("PASSED")

print("TEST: the report card says what was compared, what was not, and the result")
facts = {f.label: f for f in result.report.facts}
assert facts["File type"].value == "a list of people or companies" and "National ID" in facts["File type"].source
assert "Full Name (name)" in facts["Compared on"].value and "Mobile (phone)" in facts["Compared on"].value
assert "Customer ID" in facts["Not used"].value and "Signup Date" in facts["Not used"].value and "dates" in facts["Not used"].source
assert result.summary == "12 groups of possible duplicates, 25 rows (10 high-confidence, 2 maybe)", result.summary
print("PASSED")

print("TEST: the user's choices change the result - a role override, the phone country, the name list")
no_phone = find_near_duplicates(df, DedupeOptions(overrides={"Mobile": "ignore"}))
assert "Mobile" not in [r.column for r in no_phone.profile.used()]
ae = find_near_duplicates(pd.DataFrame({"Full Name": ["Sara Noor", "Sara Noor"], "Mobile": ["050 123 4567", "+971 50 123 4567"]}), DedupeOptions(locale="AE"))
assert [g.level for g in ae.groups] == ["high"]
custom = find_near_duplicates(pd.DataFrame({"Full Name": ["Nadeem Shah", "Nadim Shah"], "Mobile": ["0300-1234567", "0300-1234567"]}),
                              DedupeOptions(variants=[["nadeem", "nadim"]]))
assert [g.level for g in custom.groups] == ["high"] and "spelling variant" in custom.groups[0].reasons[1]
gmail = pd.DataFrame({"Full Name": ["Ali Khan", "Ali Khan"], "Email": ["a.li@gmail.com", "ali+news@gmail.com"]})
assert find_near_duplicates(gmail).groups == [], "two different addresses, as far as anyone can tell"
assert [g.level for g in find_near_duplicates(gmail, DedupeOptions(gmail_rule=True)).groups] == ["high"]
print("PASSED")

print("TEST: a lone typo'd name (nothing else to confirm it) is shown only in loose mode")
typo_only = pd.DataFrame({"Name": ["Ali Khan", "Ali Khna", "Sara Raza", "Omar Farooq"]})
assert find_near_duplicates(typo_only).groups == [] and find_near_duplicates(typo_only, DedupeOptions(strictness="strict")).groups == []
loose = find_near_duplicates(typo_only, DedupeOptions(strictness="loose"))
assert [(g.level, g.rows) for g in loose.groups] == [("maybe", [0, 1])]
print("PASSED")
