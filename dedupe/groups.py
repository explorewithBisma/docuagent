"""
dedupe/groups.py
-----------------
Turns scored pairs into groups of rows that are probably the same record.

* High groups are built from HIGH pairs only. A row is added to a group only if it does not clearly contradict any row already in it
  (a different id, two different strong identifiers ...), so a chain A~B, B~C never joins an A and a C that are different people.
* Maybe groups are built the same way from the rows that are not in a high group. A maybe link never extends a high group.
* Each group has a default row to keep: the one with the most filled cells, then the earliest.
"""

from dataclasses import dataclass, field

from dedupe.score import PairResult, evidence, hard_conflict

LARGE_GROUP = 8


@dataclass
class Group:
    id: int
    level: str                                  # "high" | "maybe"
    rows: list                                  # positions in the data frame, in file order
    labels: list                                # the data frame's row labels, same order
    keep: int                                   # position of the default row to keep
    score: int                                  # the weakest link that joined the group
    reasons: dict = field(default_factory=dict)    # position of a non-kept row -> why it matches the kept row
    warnings: list = field(default_factory=list)


def _cluster(P, pairs: list, allowed: set) -> list:
    """Greedy clustering over `pairs` (strongest first), refusing merges that put two contradicting rows together."""
    cluster_of, members, weakest = {}, {}, {}
    next_id = iter(range(10 ** 9))
    for pair in sorted(pairs, key=lambda p: -p.score):
        if pair.i not in allowed or pair.j not in allowed:
            continue
        ci, cj = cluster_of.get(pair.i), cluster_of.get(pair.j)
        if ci is not None and ci == cj:
            continue
        left = members[ci] if ci is not None else [pair.i]
        right = members[cj] if cj is not None else [pair.j]
        if any(hard_conflict(evidence(P, a, b)) for a in left for b in right):
            continue
        new_id = ci if ci is not None else (cj if cj is not None else next(next_id))
        merged = sorted(set(left) | set(right))
        for old in (ci, cj):
            if old is not None and old != new_id:
                members.pop(old, None)
        members[new_id] = merged
        weakest[new_id] = min(weakest.get(ci, 100), weakest.get(cj, 100), pair.score)
        for row in merged:
            cluster_of[row] = new_id
    return [(rows, weakest[cid]) for cid, rows in members.items() if len(rows) > 1]


def build_groups(P, results: list) -> list:
    """results = [PairResult] -> [Group], high first, then by size and score."""
    high = [r for r in results if r.level == "high"]
    maybe = [r for r in results if r.level == "maybe"]
    everyone = set(range(P.n))
    groups = []
    taken = set()
    for rows, score in _cluster(P, high, everyone):
        groups.append(("high", rows, score))
        taken |= set(rows)
    for rows, score in _cluster(P, maybe, everyone - taken):
        groups.append(("maybe", rows, score))

    by_pair = {(r.i, r.j): r for r in results}
    out = []
    for level, rows, score in groups:
        keep = max(rows, key=lambda i: (P.completeness[i], -i))
        group = Group(0, level, rows, [P.labels[i] for i in rows], keep, score)
        for other in rows:
            if other == keep:
                continue
            pair = by_pair.get((min(keep, other), max(keep, other)))
            if pair is not None:
                group.reasons[other] = pair.phrase
            else:                                                      # linked only through another row of the group
                best = max((r for (a, b), r in by_pair.items() if other in (a, b) and a in rows and b in rows), key=lambda r: r.score, default=None)
                group.reasons[other] = (best.phrase + " (through another row of the group)") if best else "linked through another row of the group"
        if len(rows) > LARGE_GROUP:
            group.warnings.append(f"{len(rows)} rows: a group this big may be a shared contact or a very common name - check it carefully.")
        out.append(group)
    out.sort(key=lambda g: (g.level != "high", -len(g.rows), -g.score, g.rows[0]))
    for number, group in enumerate(out, start=1):
        group.id = number
    return out
