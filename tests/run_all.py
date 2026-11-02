"""Run every test script in this folder and report pass/fail.
Usage (from the project folder):  python tests/run_all.py          (scripts run side by side, up to 6 at a time)
                                  python tests/run_all.py -j 1     (one at a time)"""
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

here = Path(__file__).resolve().parent
scripts = sorted(p for p in here.glob("*.py") if p.name != "run_all.py" and not p.name.startswith("_"))
workers = int(sys.argv[sys.argv.index("-j") + 1]) if "-j" in sys.argv else min(6, os.cpu_count() or 1)


def run(script):
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})  # tests print arrows etc.
    return script, result


failed = []
with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
    for script, result in pool.map(run, scripts):             # results come back in file order
        ok = result.returncode == 0
        print(f"{'PASS' if ok else 'FAIL'}  {script.name}")
        if not ok:
            failed.append(script.name)
            print((result.stdout + result.stderr)[-1500:])
print(f"\n{len(scripts) - len(failed)}/{len(scripts)} test scripts passed")
sys.exit(1 if failed else 0)
