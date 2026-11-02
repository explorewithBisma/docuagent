"""
reconcile/report.py
--------------------
The "What I found" card and the "Please check" list for a reconciliation, in the toolkit's shapes (Report / Fact / Issue), plus the
money totals of every bucket.
"""

import numpy as np

from reconcile.columns import ROLES
from toolkit.ranking import rank
from toolkit.report import Fact, Issue, Report

LIST_LIMIT = 60
BUCKET_TITLES = {"exact": "Matched (exact)", "close": "Matched (close)", "maybe": "Maybe", "mismatch": "Amount mismatch", "only_a": "Only in A", "only_b": "Only in B"}


def money(value) -> str:
    return f"{value:,.2f}"


def row_label(side, i: int, table, mapping) -> str:
    """'row 12: 2026-03-05 · Etisalat Bill · -450.00' - enough to find the row in the user's file."""
    parts = []
    for role in ("date", "description"):
        column = mapping.column(role)
        if column and str(table.iloc[i][column]) not in ("", "nan", "None", "NaT"):
            parts.append(str(table.iloc[i][column])[:40])
    if not np.isnan(side.amounts[i]):
        parts.append(money(side.amounts[i]))
    return f"row {side.row_numbers[i]}: " + " · ".join(parts)


def totals(A, B, result) -> dict:
    """Money in every bucket (A's and B's amounts as read, signs as in each file)."""
    def total(side, rows):
        return float(np.nansum([side.amounts[i] for i in rows])) if rows else 0.0

    out = {}
    for bucket in ("exact", "close", "maybe"):
        pairs = result.bucket(bucket)
        out[bucket] = {"count": len(pairs), "a": total(A, [p.a for p in pairs]), "b": total(B, [p.b for p in pairs])}
    out["mismatch"] = {"count": len(result.mismatches), "a": total(A, [p.a for p in result.mismatches]), "b": total(B, [p.b for p in result.mismatches])}
    out["mismatch"]["difference"] = sum(p.difference for p in result.mismatches)
    out["only_a"] = {"count": len(result.only_a), "a": total(A, result.only_a), "b": 0.0}
    out["only_b"] = {"count": len(result.only_b), "a": 0.0, "b": total(B, result.only_b)}
    return out


def summary_text(result) -> str:
    exact, close = len(result.bucket("exact")), len(result.bucket("close"))
    parts = [f"{exact + close:,} matched ({exact:,} exact, {close:,} close)", f"{len(result.bucket('maybe')):,} to confirm",
             f"{len(result.only_a):,} only in A", f"{len(result.only_b):,} only in B", f"{len(result.mismatches):,} amount mismatch{'es' if len(result.mismatches) != 1 else ''}"]
    return ", ".join(parts)


def _mapping_facts(label, name, mapping, side):
    facts = []

    def find(role, text):
        finding = mapping.findings.get(role)
        if finding:
            return Fact(f"{label} · {text}", f"'{finding.value}'", finding.source, finding.confidence)
        return None

    facts.append(find("date", "date") or Fact(f"{label} · date", "not found", "no column looks like dates", "low"))
    if mapping.money_mode == "debit_credit":
        for role, text in (("debit", "money out"), ("credit", "money in")):
            fact = find(role, text)
            if fact:
                facts.append(fact)
    else:
        facts.append(find("amount", "amount") or Fact(f"{label} · amount", "not found", "no column looks like amounts", "low"))
    for role, text in (("reference", "reference / id"), ("description", "description")):
        fact = find(role, text)
        if fact:
            facts.append(fact)
        else:
            facts.append(Fact(f"{label} · {text}", "none", "no suitable column", "medium"))
    return facts


def build_report(name_a, name_b, table_a, table_b, map_a, map_b, A, B, result, accepted=frozenset()) -> Report:
    facts = _mapping_facts("File A", name_a, map_a, A) + _mapping_facts("File B", name_b, map_b, B)
    sign_confidence = "high" if result.sign == "same" and "line up" in result.sign_text else "medium"
    facts.append(Fact("Money in / out", result.sign_text, "how many amounts line up in each reading", sign_confidence))
    window = result.options.day_window
    facts.append(Fact("Matching", f"same reference first; then same amount and date; then same amount within {window} day{'s' if window != 1 else ''}; "
                                  f"then amount within {result.options.tolerance:.1%} (always a 'maybe')",
                      "the settings under Edit details", "high"))
    notes = list(map_a.notes) + list(map_b.notes) + [f"File A: {n}" for n in A.notes] + [f"File B: {n}" for n in B.notes]

    issues = []
    for pair in result.mismatches:
        issues.append(Issue("amount_mismatch", "high", f"Same reference, different amount ({money(abs(pair.difference))})",
                            "Both files have this reference but not the same amount. One of them is wrong, or part of the payment is missing.",
                            evidence=f"A {row_label(A, pair.a, table_a, map_a)}\nB {row_label(B, pair.b, table_b, map_b)}", impact=abs(pair.difference),
                            locations=[("File A", f"row {A.row_numbers[pair.a]}"), ("File B", f"row {B.row_numbers[pair.b]}")],
                            fix="Check which amount is right and correct the other file."))
    for label, rows, side, table, mapping, other in (("A", result.only_a, A, table_a, map_a, "B"), ("B", result.only_b, B, table_b, map_b, "A")):
        for i in rows:
            amount = 0.0 if np.isnan(side.amounts[i]) else abs(float(side.amounts[i]))
            issues.append(Issue(f"only_in_{label.lower()}", "medium", f"Only in {label}: {money(amount)}",
                                f"This row has no partner in file {other}. It may be missing there, recorded with a different amount or date, or not yet processed.",
                                evidence=row_label(side, i, table, mapping), impact=amount, locations=[(f"File {label}", f"row {side.row_numbers[i]}")]))
    for pair in result.bucket("maybe"):
        if (pair.a, pair.b) in accepted:
            continue
        issues.append(Issue("maybe_match", "low", f"Possible match to confirm ({money(abs(A.amounts[pair.a]))})", pair.reason.capitalize() + ". Not matched until you confirm it.",
                            evidence=f"A {row_label(A, pair.a, table_a, map_a)}\nB {row_label(B, pair.b, table_b, map_b)}", impact=abs(float(A.amounts[pair.a])),
                            locations=[("File A", f"row {A.row_numbers[pair.a]}"), ("File B", f"row {B.row_numbers[pair.b]}")]))
    impacts = sorted(i.impact for i in issues if i.kind != "maybe_match")
    typical = impacts[len(impacts) // 2] if impacts else 0.0
    for issue in issues:                                   # the big ones first: serious = at least 5 x the typical unmatched amount
        if issue.kind != "maybe_match":
            issue.severity = "high" if typical and issue.impact >= 5 * typical else "medium"
    ranked = rank(issues)
    if len(ranked) > LIST_LIMIT:
        notes.append(f"The list below shows the {LIST_LIMIT} most important of {len(ranked):,}; the Excel file has every row.")
        shown = ranked[:LIST_LIMIT]
    else:
        shown = ranked
    report = Report("Match two files", facts=facts, issues=shown, notes=notes)
    report.stats = {"totals": totals(A, B, result), "all_issues": len(ranked)}
    return report
