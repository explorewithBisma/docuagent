from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""dedupe/columns.py: which columns identify a person (and why), which differ legitimately, and what kind of file this is."""
import sys
from datetime import date, timedelta

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import pandas as pd

import dedupe_fixtures as fx
from dedupe import columns as co


def roles_of(df, **kw):
    profile = co.build_profile(df, **kw)
    return {r.column: (r.role, r.used) for r in profile.roles}, profile


print("TEST: the customer list - identity columns found with reasons; dates, amounts, status, city and the unique customer id are ignored")
df, _ = fx.customers(300)
roles, profile = roles_of(df)
assert roles == {"Customer ID": ("ignore", False), "Full Name": ("name", True), "Email": ("email", True), "Mobile": ("phone", True),
                 "National ID": ("id", True), "Company": ("company", True), "Address": ("address", True), "City": ("place", True),
                 "Signup Date": ("ignore", False), "Total Spend": ("ignore", False), "Status": ("ignore", False)}, roles
why = {r.column: r.why for r in profile.roles}
assert "every row has a different one" in why["Customer ID"] and why["Signup Date"].startswith("dates") and why["Total Spend"].startswith("amounts")
assert "only 3 different values" in why["Status"] and why["Email"] == "header 'Email'"
assert profile.kind == "contacts" and "National ID" in profile.kind_reason and profile.kind_confidence == "high"
print("PASSED")

print("TEST: values can identify a column when the header does not (emails, phones); a header can too when the values are numbers")
odd = pd.DataFrame({"Col A": [f"user{i}@mail.com" for i in range(30)], "Col B": [f"0300-{1000000 + i}" for i in range(30)],
                    "Phone": [3001000000 + i for i in range(30)], "Contact Name": [f"Person Number{i % 7}" for i in range(30)]})
roles, profile = roles_of(odd)
assert roles["Col A"] == ("email", True) and roles["Col B"] == ("phone", True) and roles["Phone"] == ("phone", True)
assert roles["Contact Name"] == ("name", True), "'contact' with 'name' is a name, not a phone"
assert dict((r.column, r.why) for r in profile.roles)["Col A"] == "most values look like email addresses"
print("PASSED")

print("TEST: first / last name columns are name parts (in order); company and address; names that are not people are not names")
parts = pd.DataFrame({"Last Name": ["Khan", "Raza", "Malik"] * 4, "First Name": ["Ali", "Sara", "Omar"] * 4, "Company Name": ["Acme", "Beta", "Cora"] * 4,
                      "Street Address": [f"{i} Main St" for i in range(12)], "Product Name": [f"Widget {i}" for i in range(12)],
                      "User Name": [f"user_{i}" for i in range(12)]})
profile = co.build_profile(parts)
used = {r.column: (r.role, r.order) for r in profile.roles if r.used}
assert used["First Name"] == ("name", 0) and used["Last Name"] == ("name", 2) and used["Company Name"][0] == "company" and used["Street Address"][0] == "address"
assert "Product Name" not in used and "User Name" not in used
print("PASSED")

print("TEST: ids - a customer id that repeats is an identifier; an order / product id or a row counter is not")
orders = pd.DataFrame({"Order ID": [f"O{i}" for i in range(40)], "Product ID": [f"P{i % 5}" for i in range(40)], "Sr No": range(1, 41),
                       "Customer Code": [f"K{i % 30}" for i in range(40)], "Order Date": [date(2026, 1, 1) + timedelta(days=i) for i in range(40)],
                       "Order Status": ["Open", "Closed"] * 20, "Amount": [10.5 + i for i in range(40)]})
profile = co.build_profile(orders)
by = {r.column: r for r in profile.roles}
assert by["Order ID"].tag == "transaction_id" and by["Sr No"].tag == "counter" and by["Product ID"].role == "record" and "something else" in by["Product ID"].why        # (an order list compares the product too)
assert by["Customer Code"].role == "id" and "(dates)" in by["Order Date"].why and by["Order Status"].role == "record"
product_only = co.build_profile(orders[["Product ID", "Customer Code"]], mode="contacts")
assert {r.column: r.role for r in product_only.roles} == {"Product ID": "ignore", "Customer Code": "id"}, "outside an order list the product id is simply ignored"
print("PASSED")

print("TEST: the file kind - a list of people versus a list of transactions, with the reason")
assert co.build_profile(df).kind == "contacts"
assert profile.kind == "transactions" and "'Order ID' is a different order / document number on every row" in profile.kind_reason and profile.kind_confidence == "high"
repeat = pd.DataFrame({"Customer ID": [f"K{i % 10}" for i in range(60)], "Name": [f"Person {i % 10}" for i in range(60)],
                       "Visit Date": [date(2026, 1, 1) + timedelta(days=i) for i in range(60)], "Spend": [5.0 + i for i in range(60)]})
kind = co.build_profile(repeat)
assert kind.kind == "transactions" and "comes back 6 times per value" in kind.kind_reason, kind.kind_reason
nothing = pd.DataFrame({"a": range(20), "b": [x * 2.5 for x in range(20)]})
assert co.build_profile(nothing).kind == "unknown" and "No column that identifies" in co.build_profile(nothing).kind_reason
print("PASSED")

print("TEST: in a transaction list every other column is compared as part of the whole record; the record number is not")
used = {r.column: r.role for r in profile.roles if r.used}
assert used["Amount"] == "record" and used["Order Date"] == "record" and "Order ID" not in used and "Sr No" not in used
print("PASSED")

print("TEST: the user can switch the file kind and change any column's role - and the table says 'set by you'")
forced = co.build_profile(df, mode="transactions")
assert forced.kind == "transactions" and forced.kind_reason == "chosen by you" and {r.column: r.role for r in forced.roles}["Total Spend"] == "record"
changed = co.build_profile(df, overrides={"City": "address", "Email": "ignore"})
roles = {r.column: (r.role, r.used, r.why) for r in changed.roles}
assert roles["City"] == ("address", True, "set by you") and roles["Email"] == ("ignore", False, "set by you")
print("PASSED")

print("TEST: empty columns and tiny files do not break it")
empty = pd.DataFrame({"Name": ["Ali Khan", "Sara Raza"], "Email": [None, None]})
roles, profile = roles_of(empty)
assert roles["Email"] == ("ignore", False) and roles["Name"][0] == "name"
assert co.build_profile(pd.DataFrame({"x": []})).kind == "unknown"
print("PASSED")
