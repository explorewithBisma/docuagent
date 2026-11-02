from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""dedupe on 20,000 rows: blocking keeps it fast (no 200-million-pair comparison), and it stays accurate. Timing and quality are printed."""
import sys
import time

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import dedupe_fixtures as fx
from dedupe.analyze import DedupeOptions, find_near_duplicates

TARGET_SECONDS = 10          # what we aim for on an idle machine
LIMIT_SECONDS = 25           # the test fails above this (the suite runs several scripts at once)

print("TEST: 20,000 rows (19,000 customers + 900 planted duplicates + the look-alikes) - speed and accuracy")
frame, truth = fx.customers(19000, seed=11)
frame, truth = fx.inflate(frame, truth, 900, seed=5)
started = time.perf_counter()
result = find_near_duplicates(frame, DedupeOptions(locale="PK"))
elapsed = time.perf_counter() - started
found = set()
for group in result.groups:
    rows = sorted(group.rows)
    found |= {(a, b) for i, a in enumerate(rows) for b in rows[i + 1:]}
truth_pairs = truth.pairs()
precision, recall = len(found & truth_pairs) / len(found), len(found & truth_pairs) / len(truth_pairs)
everything = len(frame) * (len(frame) - 1) // 2
print(f"   {len(frame):,} rows in {elapsed:.1f} s (target {TARGET_SECONDS} s, limit {LIMIT_SECONDS} s); "
      f"{result.stats['pairs_compared']:,} pairs compared instead of {everything:,} ({result.stats['pairs_compared'] / everything:.2%})")
print(f"   {result.summary}")
print(f"   precision {precision:.4f}   recall {recall:.4f}   look-alikes grouped: {len(found & truth.lookalike_pairs())}")
assert elapsed < LIMIT_SECONDS, f"{elapsed:.1f} s"
assert result.stats["pairs_compared"] < everything * 0.01
assert precision >= 0.98 and recall >= 0.95 and not (found & truth.lookalike_pairs())
print("PASSED")
