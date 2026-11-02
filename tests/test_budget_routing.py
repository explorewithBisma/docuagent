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


print("TEST: the start screen shows all five tasks with their one-line descriptions; unfinished ones are disabled 'Coming soon'")
at = start()
assert [s.value for s in at.subheader] == ["🧹 Clean my file", "🩺 Check my file", "🔗 Match two files", "💰 Build my budget", "✅ Check every month"]
buttons = {b.key: b for b in at.button}
assert buttons["start_cleaner"].label == "Clean my file" and buttons["start_check"].label == "Check my file"
assert buttons["start_budget"].label == "Build my budget"
assert buttons["start_monthly"].label == "Coming soon" and buttons["start_monthly"].proto.disabled is True
assert buttons["start_match"].label == "Match two files"
for key in ("start_cleaner", "start_check", "start_match", "start_budget"):
    assert buttons[key].proto.disabled is False
text = " ".join(m.value for m in at.markdown)
assert "Compare a bank statement with your records" in text and "Find typed-in totals, broken formulas" in text
print("PASSED")

print("TEST: SHOW_UNFINISHED_FEATURES = False hides the cards that are not built yet")
with mock.patch.object(config, "SHOW_UNFINISHED_FEATURES", False):
    at = start()
assert [s.value for s in at.subheader] == ["🧹 Clean my file", "🩺 Check my file", "🔗 Match two files", "💰 Build my budget"]
assert {b.key for b in at.button} == {"start_cleaner", "start_check", "start_match", "start_budget"}
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

print("TEST: app.py only changed for the approved near-duplicate hook (the cleaner and its tests are otherwise untouched)")
diff = subprocess.run(["git", "diff", "-U0", "65a059f", "--", "app.py"], cwd=ROOT, capture_output=True, text=True).stdout
removed = [l for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")]
added = [l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
# only the removed-rows caption may be rewritten (it now counts near-duplicates); everything else is added lines for the dedupe panel
assert len(removed) <= 2 and all("duplicate" in l or "fully empty" in l for l in removed), removed
assert len(added) <= 40, len(added)                  # the approved hook is about ten lines, plus the caption
print("PASSED")

print("TEST: 'Match two files' opens the matcher, which links back to the start screen")
at = start()
next(b for b in at.button if b.key == "start_match").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
assert any("Match two files" in t.value for t in at.title) and any(b.key == "back_to_start" for b in at.button)
print("PASSED")
