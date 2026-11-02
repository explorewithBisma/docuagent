"""
dedupe/score.py
----------------
Scores a pair of rows in words, not in a black box. For every identity column the pair gets a verdict - exact, almost, different, or
blank (one row has nothing there: unknown, neither a match nor a conflict) - and the level follows from the verdicts:

  HIGH   a strong identifier (email, phone, id) is the same AND the name is the same or almost the same, with nothing that differs;
         or the name is the same and company and address both agree;
         or two strong identifiers are the same.
  MAYBE  a name that matches with nothing to confirm or contradict it ("only the name matches"), a name plus one supporting column,
         or a strong identifier that is the same while another differs.
  MAYBE  (extra) a date of birth that is the same, with a name that is the same or almost the same - or, with no name to go on, the
         same postcode / suburb too. A date of birth can only ever create a MAYBE, never a HIGH; a different date of birth
         removes a Maybe that rested on the name alone.
  never  two different people: a different id, two different strong identifiers, the same phone under a different name (a shared line),
         the same name with a different phone or email (a common name).

A name on its own can never be HIGH. A pair is also never HIGH when anything strong differs.
"""

from dataclasses import dataclass, field

from dedupe import normalize as nz

STRONG = ("email", "phone", "id")
SUPPORT = ("company", "address")


@dataclass
class PairResult:
    i: int
    j: int
    level: str                      # "high" | "maybe"
    score: int                      # 1-100, for ordering only
    phrase: str                     # "name almost same (typo 'khan' / 'khna'), phone same"
    evidence: dict = field(default_factory=dict)       # role -> (verdict, why)


def _sets(a, b):
    if not a or not b:
        return "blank"
    return "exact" if a & b else "different"


def evidence(P, i, j) -> dict:
    """role -> (verdict, why) for the rows i and j."""
    out = {}
    if P.has.get("email"):
        out["email"] = (_sets(P.emails[i], P.emails[j]), "")
    if P.has.get("id"):
        out["id"] = (_sets(P.ids[i], P.ids[j]), "")
    if P.has.get("phone"):
        out["phone"] = (_sets(P.phone_keys[i], P.phone_keys[j]), "")
    if P.has.get("name"):
        out["name"] = nz.compare_names(P.names[i], P.names[j])
    if P.has.get("company"):
        out["company"] = (nz.compare_texts(P.companies[i], P.companies[j]), "")
    if P.has.get("address"):
        out["address"] = (nz.compare_sets(P.addresses[i], P.addresses[j]), "")
    if P.has.get("dob"):
        a, b = P.dobs[i], P.dobs[j]
        out["dob"] = ("blank" if not a or not b else "exact" if a == b else "different", "")
    if P.has.get("place"):
        out["place"] = (_sets(P.places[i], P.places[j]), "")
    return out


def hard_conflict(ev: dict) -> bool:
    """Evidence that two rows are DIFFERENT people: a different id, two different strong identifiers, or a different strong
    identifier under a different name."""
    verdict = {role: v[0] for role, v in ev.items()}
    different = [r for r in STRONG if verdict.get(r) == "different"]
    return verdict.get("id") == "different" or len(different) >= 2 or (len(different) == 1 and verdict.get("name") == "different")


def describe(ev: dict) -> str:
    """The pair in words: name first, then the strong identifiers, then company and address."""
    parts = []
    labels = {"phone": "phone", "email": "email", "id": "ID", "company": "company", "address": "address"}
    name = ev.get("name")
    if name and name[0] != "blank":
        if name[0] == "exact":
            parts.append("name same")
        elif name[0] == "almost":
            parts.append(f"name almost same ({name[1]})" if name[1] else "name almost same")
        else:
            parts.append("name different")
    for role in STRONG + SUPPORT:
        if role not in ev:
            continue
        verdict = ev[role][0]
        if verdict == "exact":
            parts.append(f"{labels[role]} same")
        elif verdict == "almost":
            parts.append(f"{labels[role]} almost same")
        elif verdict == "different" and role in STRONG:
            parts.append(f"{labels[role]} differs")
        elif verdict == "blank" and role in STRONG:
            parts.append(f"{labels[role]} blank in one row")
    if "dob" in ev:
        if ev["dob"][0] == "exact":
            parts.append("date of birth same")
            if ev.get("place", ("",))[0] == "exact":
                parts.append("postcode / suburb same")
        elif ev["dob"][0] == "different":
            parts.append("date of birth differs")
    return ", ".join(parts) if parts else "no identifying column to compare"


def _score(ev: dict) -> int:
    points = 0
    strong_exact = sum(1 for r in STRONG if ev.get(r, ("",))[0] == "exact")
    points += 40 * min(strong_exact, 2)
    name = ev.get("name", ("blank",))[0]
    points += {"exact": 30, "almost": 20}.get(name, 0)
    for role in SUPPORT:
        points += {"exact": 8, "almost": 4}.get(ev.get(role, ("",))[0], 0)
    points += 25 if ev.get("dob", ("",))[0] == "exact" else 0
    points -= 25 * sum(1 for r in STRONG if ev.get(r, ("",))[0] == "different")
    return max(1, min(100, points))


def classify(ev: dict, strictness: str = "normal"):
    """-> "high", "maybe" or None for the pair's evidence. strictness: "strict" (no name-only), "normal", "loose" (also a lone typo'd name).
    The date of birth never changes a High; it can add a Maybe, and a different one removes a Maybe that rested on the name alone."""
    level = _classify_core(ev, strictness)
    verdict = {role: v[0] for role, v in ev.items()}
    if verdict.get("dob") == "different" and level == "maybe" and not any(verdict.get(r) == "exact" for r in STRONG + SUPPORT):
        return None                                                # same name but a different birthday: two people
    if level is not None or strictness == "strict" or verdict.get("dob") != "exact":
        return level
    different_strong = [r for r in STRONG if verdict.get(r) == "different"]
    if verdict.get("id") == "different" or len(different_strong) >= 2:
        return None
    name = verdict.get("name", "blank")
    if name in ("exact", "almost") or (name == "blank" and verdict.get("place") == "exact"):
        return "maybe"
    return None


def _classify_core(ev: dict, strictness: str):
    verdict = {role: v[0] for role, v in ev.items()}
    exact_strong = [r for r in STRONG if verdict.get(r) == "exact"]
    different_strong = [r for r in STRONG if verdict.get(r) == "different"]
    name = verdict.get("name", "blank")
    support_ok = [r for r in SUPPORT if verdict.get(r) in ("exact", "almost")]
    support_bad = [r for r in SUPPORT if verdict.get(r) == "different"]

    if verdict.get("id") == "different" or len(different_strong) >= 2:
        return None                                                # two different people (or a veto)
    if exact_strong:
        if name in ("exact", "almost"):
            return "maybe" if different_strong else "high"          # same phone and name, other email differs: one person, two emails?
        if name == "blank":
            return "high" if len(exact_strong) >= 2 else "maybe"
        return None                                                # a shared phone / email under a different name: a shared line
    if different_strong:
        return None                                                # a common name: the phone or email is not the same
    if name == "exact":
        if len(support_ok) >= 2:
            return "high"
        if support_bad and not support_ok:
            return None                                            # same name, but company AND address say otherwise
        if len(support_ok) == 1:
            return "maybe"
        return "maybe" if strictness != "strict" else None         # only the name matches: never HIGH
    if name == "almost":
        if len(support_ok) >= 2:
            return "maybe"
        if strictness == "loose" or (len(support_ok) == 1 and strictness != "strict"):
            return "maybe"
    return None


def score_pair(P, i, j, strictness: str = "normal"):
    """-> PairResult or None. Pairs that two different strong identifiers already rule out are dropped before any name is compared."""
    ids_i, ids_j = P.ids[i], P.ids[j]
    if ids_i and ids_j and not (ids_i & ids_j):
        return None                                                # a different id: two different people
    different = same = 0
    for a, b in ((P.emails[i], P.emails[j]), (P.phone_keys[i], P.phone_keys[j]), (ids_i, ids_j)):
        if a and b:
            if a & b:
                same += 1
            else:
                different += 1
    if different >= 2 or (different == 1 and not same):
        return None                                                # (what classify() would decide, without the cost of the name)
    ev = evidence(P, i, j)
    level = classify(ev, strictness)
    if level is None:
        return None
    return PairResult(i, j, level, _score(ev), describe(ev), ev)


# ---------------------------------------------------------------------------------------------------------------
# TRANSACTION LISTS: compare whole records
# ---------------------------------------------------------------------------------------------------------------
def _same_value(a, b) -> str:
    if a is None or b is None:
        return "blank"
    if a == b:
        return "exact"
    if a[0] == b[0] == "t":
        limit = nz.typo_limit(min(len(a[1]), len(b[1])))
        if limit and min(len(a[1]), len(b[1])) >= 5 and nz.osa_distance(a[1], b[1], limit) <= limit:
            return "almost"
    return "different"


def score_record_pair(P, i, j, strictness: str = "normal"):
    """Two transactions are a near-duplicate only when NO comparable column differs (a different date or amount is a different
    transaction) and enough columns could be compared at all."""
    a, b = P.records[i], P.records[j]
    columns = len(a)
    if columns == 0:
        return None
    verdicts = []
    for x, y in zip(a, b):
        verdict = _same_value(x, y)
        if verdict == "different":
            return None                                            # one differing column makes it another transaction: stop at once
        verdicts.append(verdict)
    comparable = [v for v in verdicts if v != "blank"]
    if len(comparable) < 4:
        return None
    share = len(comparable) / columns
    almost = verdicts.count("almost")
    if share < 0.5:
        return None
    level = "high" if share >= 0.8 else "maybe"
    differing = [P.record_columns[k] for k, v in enumerate(verdicts) if v == "almost"]
    blanks = [P.record_columns[k] for k, v in enumerate(verdicts) if v == "blank"]
    phrase = f"all {len(comparable)} comparable columns match" + (f" ({almost} with a small spelling difference: {', '.join(differing[:3])})" if almost else "")
    if blanks:
        phrase += f"; blank in one row: {', '.join(blanks[:3])}" + (" ..." if len(blanks) > 3 else "")
    return PairResult(i, j, level, int(100 * share) - 5 * almost, phrase, {"record": ("exact" if not almost else "almost", phrase)})
