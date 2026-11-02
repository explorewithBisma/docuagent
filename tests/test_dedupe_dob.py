from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Date of birth as an identity role: it can add a Maybe, never a High; a different date removes a name-only Maybe."""
import sys

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import pandas as pd

import dedupe_fixtures as fx
from dedupe import columns as cols, normalize as nz
from dedupe.analyze import DedupeOptions, find_near_duplicates
from dedupe.score import classify

print("TEST: dates of birth are read in the usual written forms, and nonsense is not a date")
assert nz.norm_dob("19081209") == nz.norm_dob(19081209) == nz.norm_dob("1908-12-09") == nz.norm_dob("09/12/1908") == "1908-12-09"
assert nz.norm_dob(pd.Timestamp("1980-04-03")) == "1980-04-03" and nz.norm_dob("3 Apr 1980") == "1980-04-03"
assert nz.norm_dob("") == nz.norm_dob(None) == nz.norm_dob("abc") == nz.norm_dob("31/02/1980") == nz.norm_dob("1850-01-01") == nz.norm_dob("2999-01-01") == ""
print("PASSED")

print("TEST: the DOB role needs the header AND date-like values; postcode / suburb / city are weak 'place' columns")
frame = pd.DataFrame({"Name": ["Ali Khan", "Sara Raza", "Omar Farooq", "Hina Gill"], "Date of Birth": ["1980-01-02", "1975-05-06", "1990-07-08", "1960-09-10"],
                      "Birthplace": ["Lahore"] * 4, "Born": ["x", "y", "z", "w"], "DOB": ["19800102", "19750506", "19900708", "19600910"],
                      "Zip Code": ["54000", "54000", "75500", "10001"], "Suburb": ["A", "B", "C", "D"]})
roles = {r.column: r.role for r in cols.detect_roles(frame)}
print("  ", roles)
assert roles["Date of Birth"] == "dob" and roles["DOB"] == "dob" and roles["Born"] != "dob" and roles["Birthplace"] != "dob"
assert roles["Zip Code"] == roles["Suburb"] == "place"
values_only = pd.DataFrame({"When": ["1980-01-02", "1975-05-06", "1990-07-08"]})
assert cols.detect_roles(values_only)[0].role == "ignore"                      # dates without a birth header stay ignored
print("PASSED")

print("TEST: classification table (dob exact/different x name x place x strong evidence)")
E = lambda **kw: {k: (v, "") for k, v in kw.items()}
table = [
    (E(name="exact", dob="exact"), "maybe"),
    (E(name="almost", dob="exact"), "maybe"),
    (E(name="blank", dob="exact", place="exact"), "maybe"),
    (E(name="blank", dob="exact", place="different"), None),
    (E(name="blank", dob="exact"), None),                                    # a birthday alone is nothing
    (E(name="different", dob="exact", place="exact"), None),
    (E(name="different", dob="exact"), None),
    (E(name="exact", dob="exact", id="different"), None),                     # the different-id veto stays
    (E(name="exact", dob="exact", phone="different", email="different"), None),
    (E(name="exact", dob="different"), None),                                 # a name-only Maybe dies on a different birthday
    (E(name="exact", dob="blank"), "maybe"),                                  # no birthday on one row: as before
    (E(name="exact", phone="exact", dob="different"), "high"),                # a High is never changed by the birthday
    (E(name="exact", phone="exact", dob="exact"), "high"),
    (E(name="almost", address="exact", company="exact", dob="different"), "maybe"),
    (E(name="exact", dob="exact", phone="different"), "maybe"),
]
for ev, expected in table:
    assert classify(ev, "normal") == expected, (ev, expected, classify(ev, "normal"))
assert classify(E(name="almost", dob="exact"), "strict") is None             # careful mode: no birthday-created Maybe
assert all(classify(E(name="almost", dob="exact", place="exact"), s) != "high" for s in ("strict", "normal", "loose"))
print("PASSED")

print("TEST: synthetic list WITH dates of birth - every planted pair found, no look-alike grouped, no look-alike in High (precision / recall printed)")
df, truth = fx.customers(300, with_dob=True)
result = find_near_duplicates(df, DedupeOptions(locale="PK"))


def pairs_of(groups, level=None):
    out = set()
    for g in groups:
        if level is None or g.level == level:
            rows = sorted(g.rows)
            out |= {(a, b) for i, a in enumerate(rows) for b in rows[i + 1:]}
    return out


def pr(found, truth_pairs):
    tp = len(found & truth_pairs)
    return (tp / len(found) if found else 1.0), (tp / len(truth_pairs) if truth_pairs else 1.0)


found, high = pairs_of(result.groups), pairs_of(result.groups, "high")
print("   roles:", ", ".join(f"{r.column}={r.role}" for r in result.profile.used()))
print("   all  precision %.3f recall %.3f | high precision %.3f recall %.3f" % (*pr(found, truth.pairs()), *pr(high, truth.pairs("high"))))
print("  ", result.summary)
assert not (found & truth.lookalike_pairs()), sorted(found & truth.lookalike_pairs())
assert found == truth.pairs() and high == truth.pairs("high"), (sorted(found ^ truth.pairs()), sorted(high ^ truth.pairs("high")))
print("PASSED")
