from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Independent accuracy check for near-duplicates on the FEBRL benchmark (Freely Extensible Biomedical Record Linkage), which we did not
generate. Test-only dependency: pip install -r tests/requirements-test.txt  (the test is skipped when it is not installed).

The numbers are REPORTED, not tuned to: the thresholds below are loose floors that catch a break, not targets."""
import sys

sys.path.insert(0, str(ROOT))

try:
    import recordlinkage.datasets as rl_datasets
except ImportError:
    print("SKIPPED: recordlinkage is not installed (pip install -r tests/requirements-test.txt)")
    sys.exit(0)

import pandas as pd

from dedupe.analyze import DedupeOptions, find_near_duplicates


def entity(rec_id: str) -> str:
    return rec_id.split("-")[1]                       # rec-223-org / rec-223-dup-0 -> 223


def pairs_of(groups, level=None):
    out = set()
    for g in groups:
        if level is None or g.level == level:
            rows = sorted(g.rows)
            out |= {(a, b) for i, a in enumerate(rows) for b in rows[i + 1:]}
    return out


def true_pairs(ids):
    by = {}
    for position, rec_id in enumerate(ids):
        by.setdefault(entity(rec_id), []).append(position)
    return {(a, b) for rows in by.values() for i, a in enumerate(rows) for b in rows[i + 1:]}


def show(df, ids, pair):
    rows = [df.iloc[p] for p in pair]
    return " | ".join(f"{ids[p]}: " + ", ".join(f"{v}" for v in r.tolist()) for p, r in zip(pair, rows))


def run(name, loader):
    frame = loader()
    ids = list(frame.index)
    df = frame.reset_index(drop=True).fillna("").astype(str)        # the record id is the ground truth: it is not given to the finder
    result = find_near_duplicates(df, DedupeOptions(locale="AU"))
    truth = true_pairs(ids)
    print(f"\n== {name}: {len(df)} records, {len(truth)} true duplicate pairs ==")
    print(f"   detected as: {result.profile.kind}; columns used: " + ", ".join(f"{r.column} ({r.role})" for r in result.profile.used()))
    print(f"   {result.summary}   [{result.stats['seconds']} s]")
    out = {}
    for label, level in (("High", "high"), ("High+Maybe", None)):
        found = pairs_of(result.groups, level)
        tp = len(found & truth)
        precision = tp / len(found) if found else 1.0
        recall = tp / len(truth) if truth else 1.0
        print(f"   {label:<11} precision {precision:.3f}  recall {recall:.3f}   (found {len(found)} pairs, {tp} correct)")
        out[label] = (precision, recall, found)
    found = out["High+Maybe"][2]
    misses = sorted(truth - found)
    false = sorted(found - truth)
    print(f"   missed pairs: {len(misses)}; wrong pairs: {len(false)}")
    for pair in misses[:6]:
        print("   MISS ", show(df, ids, pair))
    for pair in false[:6]:
        print("   FALSE", show(df, ids, pair))
    return out


# Regression floors: slightly BELOW the numbers last measured (the exact numbers are printed above). A change that lowers recall or
# precision below them fails; when the finder genuinely improves, raise the floors to just below the new numbers.
FLOORS = {"febrl1": {"High": (0.99, 0.66), "High+Maybe": (0.99, 0.67)},
          "febrl3": {"High": (0.99, 0.54), "High+Maybe": (0.99, 0.545)}}

print("TEST: FEBRL benchmark - precision / recall for High and High+Maybe, held to regression floors")
for name, loader in (("febrl1", rl_datasets.load_febrl1), ("febrl3", rl_datasets.load_febrl3)):
    out = run(name, loader)
    for level, (precision_floor, recall_floor) in FLOORS[name].items():
        precision, recall, _ = out[level]
        assert precision >= precision_floor, f"{name} {level}: precision {precision:.3f} fell below {precision_floor}"
        assert recall >= recall_floor, f"{name} {level}: recall {recall:.3f} fell below {recall_floor}"
print("PASSED")
