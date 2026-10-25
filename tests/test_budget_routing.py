from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""main.py: the start screen offers both modes and leads to each; app.py (the cleaner) is untouched."""
import subprocess
import sys

from streamlit.testing.v1 import AppTest

print("TEST: start screen shows both modes")
at = AppTest.from_file(str(ROOT / "main.py"), default_timeout=120)
at.run()
assert not at.exception, [e.value for e in at.exception]
assert [b.label for b in at.button] == ["Clean my Excel/CSV", "Build my budget sheet"]
print("PASSED")

print("TEST: mode A opens the unchanged cleaner")
next(b for b in at.button if b.key == "start_cleaner").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
assert [t.value for t in at.title] == ["🧹 Excel & CSV Cleaner"]
print("PASSED")

print("TEST: mode B opens the budget builder, which links back to the start screen")
at = AppTest.from_file(str(ROOT / "main.py"), default_timeout=120)
at.run()
next(b for b in at.button if b.key == "start_budget").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
assert any("budget" in t.value.lower() for t in at.title)
assert any(b.key == "back_to_start" for b in at.button)
print("PASSED")

print("TEST: app.py is not modified by the budget work (the cleaner and its tests are untouched)")
diff = subprocess.run(["git", "diff", "--stat", "9be2f8f", "--", "app.py"], cwd=ROOT, capture_output=True, text=True).stdout
# app.py was last changed by the loaded-rule check commit; nothing after it may touch it
last = subprocess.run(["git", "log", "-1", "--format=%h", "65a059f", "--", "app.py"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
changes = subprocess.run(["git", "diff", "--stat", "65a059f", "--", "app.py"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
assert changes == "", changes
print("PASSED")
