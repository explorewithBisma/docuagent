from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import io
import sys

sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

import cleaning
import report
from io_utils import load_sheets

ALL = {"headers": True, "trim": True, "duplicates": True, "empty": True, "placeholders": True, "numeric": True, "fills": False}


def sample(real_rows=40, duplicates=5, empty_rows=30, seed=1):
    """`real_rows` unique rows, `duplicates` exact copies of some of them, and `empty_rows` blank rows scattered through."""
    rng = np.random.default_rng(seed)
    base = pd.DataFrame({"id": [f"A{i:03d}" for i in range(real_rows)], "name": [f"Person {i}" for i in range(real_rows)],
                         "score": rng.integers(1, 100, real_rows).astype(str)})
    dupes = base.sample(duplicates, random_state=seed)
    blanks = pd.DataFrame({c: [np.nan] * empty_rows for c in base.columns})
    mixed = pd.concat([base, dupes, blanks], ignore_index=True).sample(frac=1, random_state=seed).reset_index(drop=True)
    return mixed


print("REGRESSION: empty rows are NOT counted as duplicates of each other")
frame = sample()
cleaned, stats = cleaning.clean_dataframe_with_steps(frame.copy(), ALL)
print({k: stats[k] for k in ("duplicates_removed", "empty_rows_removed")}, "->", len(frame), "rows in,", len(cleaned), "out")
assert stats["duplicates_removed"] == 5, stats["duplicates_removed"]          # the old code said 5 + 29 = 34
assert stats["empty_rows_removed"] == 30, stats["empty_rows_removed"]         # the old code said 1
assert len(cleaned) == 40 and len(frame) == 75
print("PASSED")

print("every combination of the two toggles keeps the counts honest")
only_dups, s = cleaning.clean_dataframe_with_steps(frame.copy(), {**ALL, "empty": False})
assert s["duplicates_removed"] == 5 and s["empty_rows_removed"] is None and len(only_dups) == 75 - 5     # empty rows stay, and are not touched by the duplicate step
only_empty, s = cleaning.clean_dataframe_with_steps(frame.copy(), {**ALL, "duplicates": False})
assert s["empty_rows_removed"] == 30 and s["duplicates_removed"] is None and len(only_empty) == 45
neither, s = cleaning.clean_dataframe_with_steps(frame.copy(), {**ALL, "duplicates": False, "empty": False})
assert len(neither) == 75
print("PASSED")

print("blank-looking rows of any kind count as EMPTY, not duplicates")
tricky = pd.DataFrame({"a": ["x", "  ", "", np.nan, "ERROR", "x", "y"], "b": ["1", " ", np.nan, np.nan, "UNKNOWN", "1", "2"]})
cleaned, s = cleaning.clean_dataframe_with_steps(tricky.copy(), ALL)
assert s["empty_rows_removed"] == 4 and s["duplicates_removed"] == 1 and cleaned["a"].tolist() == ["x", "y"], (s, cleaned)
print("PASSED")

print("the report says the same thing")
text = report.build_report_text("f.csv", {"Data": {"cleaned": True, "stats": stats}})
assert "Duplicate rows removed: 5" in text and "Fully empty rows removed: 30" in text
print("PASSED")

print("through the app")
csv = frame.to_csv(index=False).encode()
at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
at.run()
at.file_uploader[0].upload("rows.csv", csv, "text/csv")
at.run()
next(b for b in at.button if b.label == "Clean my file").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
ui = at.session_state["sheet_stats"]["Data"]["stats"]
assert (ui["duplicates_removed"], ui["empty_rows_removed"]) == (5, 30)
assert "Duplicate rows removed: 5" in at.session_state["report_text"]
assert [m.value for m in at.metric][0] == "40" and "35 removed" in [m.delta for m in at.metric][0]
print("PASSED")

print("the real retail file (skipped if absent)")
retail = _Path.home() / "Downloads" / "archive" / "retail_sales_dataset.csv"
if retail.exists():
    original = load_sheets(io.BytesIO(retail.read_bytes()), is_csv=True)["Data"]
    cleaned, s = cleaning.clean_dataframe_with_steps(original.copy(), ALL)
    print({k: s[k] for k in ("duplicates_removed", "empty_rows_removed")}, len(original), "->", len(cleaned))
    assert s["duplicates_removed"] == 80 and s["empty_rows_removed"] == 30 and len(cleaned) == len(original) - 110
    print("PASSED")
else:
    print("skipped")

print("\nALL ROW COUNT TESTS PASSED")
