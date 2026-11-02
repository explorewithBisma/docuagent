"""
dedupe/blocking.py
-------------------
Which pairs of rows are worth comparing at all. Two rows are only compared when they share a KEY - the same normalised email, phone or
id, or a spelling-tolerant name key - never "every row against every row" (20,000 rows would be 200 million pairs).

A key shared by too many rows (a very common name, a switchboard number) is not used: those rows are still found through their other
keys, and the report says which keys were skipped.
"""

from collections import defaultdict

from dedupe import normalize as nz

NAME_BLOCK_CAP = 150           # a name key shared by more rows than this is too common to pair on its own
STRONG_BLOCK_CAP = 400
MAX_PAIRS = 3_000_000


def contact_keys(P) -> list:
    """For every row, the keys under which it meets other rows."""
    keys = []
    for i in range(P.n):
        row = [("email", e) for e in P.emails[i]] + [("id", d) for d in P.ids[i]]
        for e164, last9 in P.phones[i]:
            if e164:
                row.append(("phone", e164))
            if last9 and len(last9) == 9:
                row.append(("phone9", last9))
        row += nz.name_block_keys(P.names[i])
        if P.companies[i] and P.names[i].tokens:
            row.append(("co", nz.skeleton(P.companies[i].split()[0]) + "|" + (P.names[i].tokens[-1] if P.names[i].tokens else "")))
        keys.append(row)
    return keys


def record_keys(P) -> list:
    """Transactions: rows meet when they share the whole normalised record, an identity key, or a name key."""
    keys = contact_keys(P)
    for i, record in enumerate(P.records):
        filled = tuple(v for v in record if v is not None)
        if filled:
            keys[i].append(("rec", filled))
    return keys


def candidate_pairs(keys: list, max_pairs: int = MAX_PAIRS):
    """-> (set of (i, j) with i < j, notes). Rows are compared only inside a block of rows that share a key."""
    blocks = defaultdict(list)
    for i, row in enumerate(keys):
        for key in set(row):
            blocks[key].append(i)
    pairs, skipped, notes = set(), defaultdict(int), []
    for key, members in blocks.items():
        if len(members) < 2:
            continue
        cap = NAME_BLOCK_CAP if key[0].startswith(("n", "co")) else STRONG_BLOCK_CAP
        if len(members) > cap:
            skipped[key[0]] += 1
            continue
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                pairs.add((members[a], members[b]))
        if len(pairs) > max_pairs:
            notes.append(f"Stopped after {max_pairs:,} candidate pairs: the data has very many look-alike rows. The remaining rows were not compared.")
            break
    for kind, count in skipped.items():
        label = {"n1": "single names", "n2": "names", "n2i": "names", "co": "company names", "email": "emails", "phone": "phone numbers",
                 "phone9": "phone numbers", "id": "ids", "rec": "records"}.get(kind, kind)
        notes.append(f"{count} {label} shared by too many rows were not used to pair rows (the rows are still compared through their other columns).")
    return pairs, list(dict.fromkeys(notes))
