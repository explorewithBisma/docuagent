"""Run every test script in this folder and report pass/fail.
Usage (from the project folder):  python tests/run_all.py"""
import os
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
scripts = sorted(p for p in here.glob("*.py") if p.name != "run_all.py" and not p.name.startswith("_"))
failed = []
for script in scripts:
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})  # tests print arrows etc.
    ok = result.returncode == 0
    print(f"{'PASS' if ok else 'FAIL'}  {script.name}")
    if not ok:
        failed.append(script.name)
        print((result.stdout + result.stderr)[-1500:])
print(f"\n{len(scripts) - len(failed)}/{len(scripts)} test scripts passed")
sys.exit(1 if failed else 0)
