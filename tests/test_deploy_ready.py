from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Ready for a public deployment (Streamlit Community Cloud, main.py as the entry point): complete requirements, no secrets in git, every page opens
with no API key, the AI buttons say why they are off, the privacy note is on the start screen."""
import ast
import os
import re
import subprocess
import sys
import tomllib
from unittest import mock

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

from streamlit.testing.v1 import AppTest

import config
import statement_fixtures as sf

PACKAGE_OF = {"dateutil": "python-dateutil", "yaml": "pyyaml", "PIL": "pillow"}
TEST_ONLY = {"formulas", "recordlinkage", "pytest"}


def requirement_names(path):
    names = set()
    for line in (ROOT / path).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line and not line.startswith("-"):
            names.add(re.split(r"[<>=!~ \[]", line)[0].lower().replace("_", "-"))
    return names


def third_party_imports(folder_files):
    standard, local = set(sys.stdlib_module_names), {p.stem for p in ROOT.glob("*.py")} | {p.name for p in ROOT.iterdir() if p.is_dir()}
    found = {}
    for path in folder_files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = [a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import) else \
                [node.module.split(".")[0]] if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module else []
            for module in modules:
                if module not in standard and module not in local:
                    found.setdefault(module, set()).add(path.name)
    return found


print("TEST: requirements.txt has every package the app imports - and none of the test-only ones")
app_files = [p for p in ROOT.rglob("*.py") if "tests" not in p.parts and "__pycache__" not in p.parts]
needed = third_party_imports(app_files)
runtime = requirement_names("requirements.txt")
print("   the app imports:", sorted(needed))
missing = [m for m in needed if PACKAGE_OF.get(m, m).lower() not in runtime]
assert not missing, f"requirements.txt is missing: {missing}"
assert not (runtime & TEST_ONLY) and not (set(needed) & TEST_ONLY), (runtime & TEST_ONLY, set(needed) & TEST_ONLY)
test_files = list((ROOT / "tests").glob("*.py"))
test_needs = {m for m in third_party_imports(test_files) if m not in needed and m not in {"pytest"}} - {_Path(p).stem for p in os.listdir(ROOT / "tests")}
test_requirements = requirement_names("tests/requirements-test.txt")
print("   the tests also import:", sorted(test_needs), "| tests/requirements-test.txt has:", sorted(test_requirements))
assert {"formulas", "recordlinkage"} <= test_requirements
assert not [m for m in test_needs if PACKAGE_OF.get(m, m).lower() not in test_requirements | runtime], test_needs
assert requirement_names("requirements-dev.txt") == set()                                    # it only points at the two files above
print("PASSED")

print("TEST: no secret is in git - the key file is ignored and untracked, and no tracked file contains something that looks like an API key")
tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
if tracked:
    assert ".streamlit/secrets.toml" not in tracked and not any(t.endswith((".env", ".pem", ".key")) for t in tracked)
    ignored = subprocess.run(["git", "check-ignore", ".streamlit/secrets.toml"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert ignored == ".streamlit/secrets.toml", "secrets.toml must be in .gitignore"
    pattern = re.compile(r"gsk_[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}")
    leaks = []
    for name in tracked:
        path = ROOT / name
        if path.suffix.lower() in (".py", ".md", ".toml", ".txt", ".json", ".csv", ".yml", ".yaml", ".cfg", ".ini") and path.is_file():
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                leaks.append(name)
    assert not leaks, f"something that looks like an API key is in: {leaks}"
    print(f"   {len(tracked)} tracked files checked")
else:
    print("   (not a git checkout: skipped)")
print("PASSED")

print("TEST: the public settings - unfinished tools hidden, the entry point and its pages exist, the Streamlit config is valid")
assert config.SHOW_UNFINISHED_FEATURES is False
main = (ROOT / "main.py").read_text(encoding="utf-8")
pages = re.findall(r'st\.Page\("([^"]+\.py)"', main)
print("   pages:", pages)
assert len(pages) == 5 and all((ROOT / page).is_file() for page in pages)
settings = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
assert settings["server"]["headless"] is True and settings["server"]["maxUploadSize"] <= 200 and settings["browser"]["gatherUsageStats"] is False
print("PASSED")

clean_env = {k: v for k, v in os.environ.items() if k not in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "AI_PROVIDER", "GROQ_MODEL", "ANTHROPIC_MODEL")}
with mock.patch.dict(os.environ, clean_env, clear=True):
    print("TEST: with no API key every page opens without an error, and the start screen shows all five tools and the privacy note")
    for page in ["main.py"] + pages:
        at = AppTest.from_file(str(ROOT / page), default_timeout=120)
        at.run()
        assert not at.exception, (page, [e.value for e in at.exception])
        assert not at.error, (page, [e.value for e in at.error])
    start = AppTest.from_file(str(ROOT / "main.py"), default_timeout=120)
    start.run()
    assert [s.value for s in start.subheader] == ["🧹 Clean my file", "🩺 Check my file", "🔗 Match two files", "💰 Build my budget", "✅ Check every month"]
    notes = [c.value for c in start.caption]
    assert any("Files are processed in memory and not stored" in c for c in notes), notes
    print("PASSED")

    print("TEST: cleaner - with no API key the AI button is off and says why; the deterministic cleaning still works")
    cleaner = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    cleaner.run()
    cleaner.file_uploader[0].upload("messy.csv", (ROOT / "test_data" / "messy_sample.csv").read_bytes(), "text/csv")
    cleaner.run()
    next(b for b in cleaner.button if b.label == "Clean File").click()
    cleaner.run()
    assert not cleaner.exception and not cleaner.error
    button = next(b for b in cleaner.button if b.label == "Suggest Cleaning Rules")
    assert button.proto.disabled is True
    reasons = [c.value for c in cleaner.caption if "AI suggestions are switched off" in c.value]
    assert reasons and "No API key is set (ANTHROPIC_API_KEY)" in reasons[0], reasons
    assert any(b.label.startswith("Download Cleaned File") or b.label == "Download Cleaned File" for b in cleaner.download_button)
    print("PASSED")

    print("TEST: budget - with no API key 'Use AI to suggest categories' is off and says why; the keyword map and the rest still work")
    day = sf.day
    budget = AppTest.from_file(str(ROOT / "budget_app.py"), default_timeout=240)
    budget.run()
    budget.file_uploader[0].upload("statement.csv", sf.raw_statement([(day(1), "Mazaya Trading LLC", -150.0), (day(2), "Coffee shop", -20.0), (day(3), "Salary", 9000.0)]), "text/csv")
    budget.run()
    assert not budget.exception and not budget.error
    ai = next(b for b in budget.button if b.key == "bd_use_ai")
    assert ai.proto.disabled is True and any("No API key is set" in c.value for c in budget.caption)
    print("PASSED")
