from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Offline test: every rule counts `converted` only for cells that really changed,
and reports valid-but-unchanged cells as `already_correct`."""
import sys
sys.path.insert(0, str(ROOT))

import pandas as pd
import rule_engine as re_mod
from report import _outcome_stats_text

CASES = [
    ("parse_date", {"output_format": "%Y-%m-%d", "day_first": True},
     ["2024-01-05", "2024-02-10", "05/03/2024", "garbage", None], 1, 2, 1),
    ("normalize_phone", {"country": "PK"},
     ["+923001234567", "0300-1234567", "+92-333-1112222", "nope", None], 2, 1, 1),
    ("unify_casing", {"style": "title"},
     ["Alice", "bob", "CAROL", "Dave", None], 2, 2, 0),
    ("strip_currency_to_number", {"decimal_places": 2},
     ["1500", "$20", "Rs. 1,500", "20.50", "abc"], 2, 2, 1),
    ("map_values", {"mapping": {"m": "Male", "male": "Male", "f": "Female"}},
     ["m", "Male", "male", "f", "zzz", None], 3, 1, 0),
]

for rule_type, params, values, converted, correct, failed in CASES:
    print("TEST: counts for", rule_type)
    df = pd.DataFrame({"c": values})
    df, stats = re_mod.apply_rule(df, {"column": "c", "rule_type": rule_type, "params": params})
    print(stats)
    assert stats["converted"] == converted, (rule_type, stats)
    assert stats["already_correct"] == correct, (rule_type, stats)
    assert stats["failed"] == failed, (rule_type, stats)
    print("PASSED")

print("TEST: report wording")
assert _outcome_stats_text({"converted": 5, "already_correct": 90, "failed": 1}) == "converted 5, already correct 90, failed 1"
print("PASSED")
