"""
reconcile/match.py
-------------------
Matches the rows of file A with the rows of file B in rounds, strongest evidence first. A row is matched at most ONCE (one-to-one:
no splits, no many-to-one - what is left over is listed). Every match carries the reason it was made, in words.

  Round 1  exact   the same reference (a different amount is NOT a match: it is listed as a mismatch),
                   or the same amount + the same date + a similar description
  Round 2  close   the same amount, dates within N days, the most similar description wins
  Round 3  maybe   the amount within a small tolerance (rounding), dates within N days - always a "maybe" for the user to confirm

Rows are found through blocks (same amount; same amount within a date window; amounts in a range), never by comparing every row with
every row, so 10,000 x 10,000 rows take seconds.
"""

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import numpy as np

from reconcile.prepare import NO_DATE

SIMILAR = 0.6                       # description similarity that counts as "similar"
CENT = 0.011                        # a difference of one cent is rounding, not a different amount
MAX_REF_GROUP = 60


@dataclass
class Options:
    day_window: int = 3
    tolerance: float = 0.005        # 0.5 %
    sign: str = "auto"              # auto | same | opposite | absolute


@dataclass
class Pair:
    a: int
    b: int
    bucket: str                     # exact | close | maybe | mismatch
    round: int
    reason: str
    similarity: float = None
    day_gap: int = None
    difference: float = 0.0         # B minus A


@dataclass
class Result:
    pairs: list = field(default_factory=list)           # exact / close / maybe
    mismatches: list = field(default_factory=list)
    only_a: list = field(default_factory=list)
    only_b: list = field(default_factory=list)
    options: Options = None
    sign: str = "same"
    sign_text: str = ""
    notes: list = field(default_factory=list)
    seconds: float = 0.0

    def bucket(self, name) -> list:
        return [p for p in self.pairs if p.bucket == name]


def similarity(a, b, i, j) -> float:
    """How alike two descriptions are, 0..1: the better of the letter-level ratio and the share of words that one has from the other."""
    ta, tb = a.texts[i], b.texts[j]
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    ratio = SequenceMatcher(None, ta, tb).ratio()
    wa, wb = a.tokens[i], b.tokens[j]
    contained = len(wa & wb) / min(len(wa), len(wb)) if wa and wb else 0.0
    return max(ratio, contained)


def detect_sign(A, B, requested: str = "auto"):
    """-> (how to read B's signs: 'same' | 'opposite' | 'absolute', a plain sentence). Looks at how many amounts line up in each reading."""
    if requested in ("same", "opposite", "absolute"):
        return requested, {"same": "signs used as they are", "opposite": "the signs of file B are flipped", "absolute": "only the size of the amounts is compared"}[requested] + " (set by you)"
    ca, cb = Counter(A.cents()[~np.isnan(A.amounts)].tolist()), Counter(B.cents()[~np.isnan(B.amounts)].tolist())
    same = sum(min(n, cb.get(c, 0)) for c, n in ca.items())
    opposite = sum(min(n, cb.get(-c, 0)) for c, n in ca.items())
    aa, ab = Counter(abs(c) for c in ca.elements()), Counter(abs(c) for c in cb.elements())
    absolute = sum(min(n, ab.get(c, 0)) for c, n in aa.items())
    smaller = max(1, min(sum(ca.values()), sum(cb.values())))
    known_a, known_b = A.amounts[~np.isnan(A.amounts)], B.amounts[~np.isnan(B.amounts)]
    one_unsigned = (len(known_a) and len(known_b) and ((known_b >= 0).all() and (known_a < 0).any() or (known_a >= 0).all() and (known_b < 0).any()))
    if one_unsigned and absolute > max(same, opposite) and absolute >= 0.3 * smaller:
        return "absolute", f"one file has no minus signs, so only the size of the amounts is compared ({absolute} amounts line up by size, {same} with their signs)"
    if opposite > same and opposite >= 0.3 * smaller:
        return "opposite", f"file B writes money out with the opposite sign: {opposite} amounts line up when its signs are flipped, only {same} as they are"
    if absolute > 1.3 * max(same, opposite) and absolute >= 0.3 * smaller:
        return "absolute", f"one file has no minus signs: {absolute} amounts line up by size, only {same} with their signs"
    return "same", f"money in is positive and money out negative in both files ({same} of {smaller} amounts line up as they are)"


def reconcile(A, B, options: Options = None) -> Result:
    import time
    started = time.perf_counter()
    options = options or Options()
    result = Result(options=options)
    sign, text = detect_sign(A, B, options.sign)
    result.sign, result.sign_text = sign, text

    amount_a = A.amounts.copy()
    amount_b = B.amounts.copy()
    if sign == "opposite":
        amount_b = -amount_b
    elif sign == "absolute":
        amount_a, amount_b = np.abs(amount_a), np.abs(amount_b)
    cents_a = np.where(np.isnan(amount_a), 0, np.rint(np.nan_to_num(amount_a) * 100)).astype(np.int64)
    cents_b = np.where(np.isnan(amount_b), 0, np.rint(np.nan_to_num(amount_b) * 100)).astype(np.int64)
    known_a, known_b = ~np.isnan(amount_a), ~np.isnan(amount_b)
    free_a, free_b = np.ones(A.n, dtype=bool), np.ones(B.n, dtype=bool)
    window = options.day_window
    descriptions = A.has_text and B.has_text

    def take(a, b, bucket, rnd, reason, sim=None):
        gap = None if A.dates[a] == NO_DATE or B.dates[b] == NO_DATE else int(B.dates[b] - A.dates[a])
        pair = Pair(a, b, bucket, rnd, reason, sim, abs(gap) if gap is not None else None, float(amount_b[b] - amount_a[a]) if known_a[a] and known_b[b] else 0.0)
        free_a[a] = free_b[b] = False
        (result.mismatches if bucket == "mismatch" else result.pairs).append(pair)

    def sim_of(a, b):
        return similarity(A, B, a, b) if descriptions else 0.0

    # ---- round 1a: the same reference ------------------------------------------------------------------------------------
    if A.has_ref and B.has_ref:
        by_ref_a, by_ref_b = defaultdict(list), defaultdict(list)
        for i, ref in enumerate(A.refs):
            if ref:
                by_ref_a[ref].append(i)
        for j, ref in enumerate(B.refs):
            if ref:
                by_ref_b[ref].append(j)
        for ref, rows_a in by_ref_a.items():
            rows_b = by_ref_b.get(ref)
            if not rows_b or len(rows_a) * len(rows_b) > MAX_REF_GROUP * MAX_REF_GROUP:
                continue
            options_ = []
            for a in rows_a:
                for b in rows_b:
                    diff = abs(amount_b[b] - amount_a[a]) if known_a[a] and known_b[b] else 1e18
                    gap = abs(int(B.dates[b] - A.dates[a])) if A.dates[a] != NO_DATE and B.dates[b] != NO_DATE else 10 ** 6
                    options_.append((diff, gap, a, b))
            for diff, gap, a, b in sorted(options_):
                if not (free_a[a] and free_b[b]):
                    continue
                shown = A.refs[a]
                if diff <= CENT:
                    note = "" if gap in (0, 10 ** 6) else f", dates {gap} day{'s' if gap != 1 else ''} apart"
                    take(a, b, "exact", 1, f"same reference {shown}, same amount{note}")
                elif known_a[a] and known_b[b]:
                    rel = diff / max(abs(amount_a[a]), abs(amount_b[b]), 1e-9)
                    hint = " (a rounding-size difference)" if rel <= options.tolerance else ""
                    take(a, b, "mismatch", 1, f"same reference {shown}, but the amounts differ by {diff:,.2f}{hint}")

    # ---- round 1b: the same amount + the same date + a similar description ----------------------------------------------------
    block = defaultdict(list)
    for j in np.nonzero(free_b & known_b & (B.dates != NO_DATE))[0]:
        block[(int(cents_b[j]), int(B.dates[j]))].append(int(j))
    candidates = []
    for i in np.nonzero(free_a & known_a & (A.dates != NO_DATE))[0]:
        for j in block.get((int(cents_a[i]), int(A.dates[i])), ()):
            sim = sim_of(int(i), j)
            if not descriptions or sim >= SIMILAR:
                candidates.append((-sim, int(i), j, sim))
    for _, i, j, sim in sorted(candidates):
        if free_a[i] and free_b[j]:
            take(i, j, "exact", 1, "same amount and date, descriptions similar" if descriptions else "same amount and date (no description to compare)", sim if descriptions else None)

    # ---- round 2: the same amount, dates within N days ------------------------------------------------------------------------
    by_cents = defaultdict(list)
    for j in np.nonzero(free_b & known_b & (B.dates != NO_DATE))[0]:
        by_cents[int(cents_b[j])].append((int(B.dates[j]), int(j)))
    for rows in by_cents.values():
        rows.sort()
    candidates = []
    for i in np.nonzero(free_a & known_a & (A.dates != NO_DATE))[0]:
        rows = by_cents.get(int(cents_a[i]))
        if not rows:
            continue
        day = int(A.dates[i])
        lo, hi = bisect_left(rows, (day - window, -1)), bisect_right(rows, (day + window, 1 << 60))
        for k in range(lo, hi):
            j = rows[k][1]
            if free_b[j]:
                candidates.append((-sim_of(int(i), j), abs(rows[k][0] - day), int(i), j))
    for neg_sim, gap, i, j in sorted(candidates):
        if free_a[i] and free_b[j]:
            parts = "same amount and date" if gap == 0 else f"same amount, dates {gap} day{'s' if gap != 1 else ''} apart"
            if descriptions:
                parts += f", description {-neg_sim:.0%} similar" if -neg_sim >= SIMILAR else ", descriptions differ"
            take(i, j, "close", 2, parts, -neg_sim if descriptions else None)

    # ---- round 3: the amount within a small tolerance (always a maybe) ---------------------------------------------------------------
    order = [int(j) for j in np.nonzero(free_b & known_b & (B.dates != NO_DATE))[0]]
    order.sort(key=lambda j: amount_b[j])
    sorted_amounts = [float(amount_b[j]) for j in order]
    candidates = []
    for i in np.nonzero(free_a & known_a & (A.dates != NO_DATE))[0]:
        amount = float(amount_a[i])
        slack = max(abs(amount) * options.tolerance, CENT)
        lo, hi = bisect_left(sorted_amounts, amount - slack), bisect_right(sorted_amounts, amount + slack)
        day = int(A.dates[i])
        for k in range(lo, hi):
            j = order[k]
            gap = abs(int(B.dates[j]) - day)
            if free_b[j] and gap <= window:
                candidates.append((-sim_of(int(i), j), abs(sorted_amounts[k] - amount), gap, int(i), j))
    for neg_sim, diff, gap, i, j in sorted(candidates):
        if free_a[i] and free_b[j]:
            pct = diff / max(abs(amount_a[i]), 1e-9)
            reason = f"amount differs by {diff:,.2f} ({pct:.2%}), " + ("same date" if gap == 0 else f"dates {gap} day{'s' if gap != 1 else ''} apart")
            if descriptions:
                reason += f", description {-neg_sim:.0%} similar"
            take(i, j, "maybe", 3, reason, -neg_sim if descriptions else None)

    result.only_a = [int(i) for i in np.nonzero(free_a)[0]]
    result.only_b = [int(j) for j in np.nonzero(free_b)[0]]
    result.pairs.sort(key=lambda p: (p.a, p.b))
    result.mismatches.sort(key=lambda p: (p.a, p.b))
    result.seconds = round(time.perf_counter() - started, 2)
    return result
