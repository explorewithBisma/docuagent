from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""main.py: the start screen offers the five tasks (unfinished ones greyed out, or hidden by config) and leads to each finished
one; app.py (the cleaner) is untouched."""
import subprocess
import sys
from unittest import mock

sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest

import config


def start():
    at = AppTest.from_file(str(ROOT / "main.py"), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


print("TEST: the start screen shows all five tasks with their one-line descriptions; an unfinished one would be disabled 'Coming soon'")
at = start()
assert [s.value for s in at.subheader] == ["🧹 Clean my file", "🩺 Check my file", "🔗 Match two files", "💰 Build my budget", "✅ Check every month"]
buttons = {b.key: b for b in at.button}
assert all(buttons[k].label == "Open" for k in ("start_cleaner", "start_check", "start_match", "start_budget", "start_monthly"))       # every card has an "Open" button
assert {"hero_clean", "hero_match"} <= set(buttons) and buttons["hero_clean"].label == "Clean a file" and buttons["hero_match"].label == "Match two files"
for key in ("start_cleaner", "start_check", "start_match", "start_budget", "start_monthly"):
    assert buttons[key].proto.disabled is False
text = " ".join(m.value for m in at.markdown)
assert "Compare a bank statement with your records" in text and "Find typed-in totals, broken formulas" in text
print("PASSED")

print("TEST: SHOW_UNFINISHED_FEATURES = False hides the cards that are not built yet (all five are built now, so nothing is hidden)")
with mock.patch.object(config, "SHOW_UNFINISHED_FEATURES", False):
    at = start()
assert [s.value for s in at.subheader] == ["🧹 Clean my file", "🩺 Check my file", "🔗 Match two files", "💰 Build my budget", "✅ Check every month"]
assert {b.key for b in at.button} == {"start_cleaner", "start_check", "start_match", "start_budget", "start_monthly", "hero_clean", "hero_match"}
print("PASSED")

print("TEST: 'Clean my file' opens the unchanged cleaner")
next(b for b in start().button if b.key == "start_cleaner").click()
at = start()
next(b for b in at.button if b.key == "start_cleaner").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
assert [t.value for t in at.title] == ["🧹 Excel & CSV Cleaner"]
print("PASSED")

print("TEST: 'Check my file' opens Excel Doctor, which links back to the start screen")
at = start()
next(b for b in at.button if b.key == "start_check").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
assert any("Check my file" in t.value for t in at.title) and any(b.key == "back_to_start" for b in at.button)
print("PASSED")

print("TEST: 'Build my budget' opens the budget builder, which links back to the start screen")
at = start()
next(b for b in at.button if b.key == "start_budget").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
assert any("budget" in t.value.lower() for t in at.title) and any(b.key == "back_to_start" for b in at.button)
print("PASSED")

print("TEST: the cleaning engine and the Excel output code are untouched by the 'Clean my file' redesign (pinned to the last commit before it, ae597e2)")
engine = ["cleaning.py", "changes.py", "xlsx_export.py", "derive.py", "flags.py", "quality.py"]
diff = subprocess.run(["git", "diff", "ae597e2", "--", *engine], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
assert diff.returncode == 0 and diff.stdout.strip() == "", diff.stdout[:600]
source = (ROOT / "app.py").read_text(encoding="utf-8")
assert "clean_dataframe_with_steps(" in source and "export_workbook" in source and "ChangeLog()" in source           # the page reuses them, it does not reimplement them
print("PASSED")

print("TEST: 'Match two files' opens the matcher, which links back to the start screen")
at = start()
next(b for b in at.button if b.key == "start_match").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
assert any("Match two files" in t.value for t in at.title) and any(b.key == "back_to_start" for b in at.button)
print("PASSED")

print("TEST: 'Check every month' opens the monthly checker, which links back to the start screen")
at = start()
next(b for b in at.button if b.key == "start_monthly").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
assert any("Check every month" in t.value for t in at.title) and any(b.key == "back_to_start" for b in at.button)
print("PASSED")
