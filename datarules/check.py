"""
datarules/check.py
-------------------
Checks a new file against a rules file. The data is only READ.

  1. Columns: every column the rules expect is looked for in the new file - the same name, the same words in another order, or a very
     similar name ("Amount" -> "Amount (AED)"). Renamed columns are reported; missing columns fail the check and the rules that
     depend on them are skipped (and say so); columns nobody has a rule for are listed as new.
  2. Rules: every enabled rule is run on its column; a failing rule lists the rows (Excel row numbers) and a few examples.
  3. The result is a Report in the toolkit's shape (card facts + a Please-check list, worst first) plus the cells to highlight.
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import pandas as pd
from openpyxl.utils import get_column_letter

from dedupe.normalize import norm_phone
from datarules import model
from datarules.suggest import EMAIL, as_number, as_text, blank
from locales import LOCALES
from quality import _name_words
from reconcile import columns as rc
from toolkit.ranking import rank
from toolkit.report import Fact, Issue, Report

MAX_ROWS_LISTED = 300               # per rule: rows kept for highlighting / listing (the count is always exact)
EXAMPLES = 4
SIMILAR_NAME = 0.82


@dataclass
class ColumnMatch:
    expected: str
    actual: str = None              # the column of the new file, or None when it is missing
    how: str = "missing"            # exact | reordered | similar | missing


@dataclass
class Outcome:
    rule: model.Rule
    status: str                     # pass | fail | skipped
    checked: int = 0                # cells looked at
    failing: list = field(default_factory=list)       # positions (0-based rows of the table) that broke the rule, capped at MAX_ROWS_LISTED
    failing_total: int = 0
    column: str = None              # the column of the new file the rule was run on
    examples: list = field(default_factory=list)      # [(Excel row, value, what was wrong)]
    note: str = ""


@dataclass
class CheckResult:
    status: str                     # PASS | FAIL
    matches: list
    extras: list                    # columns of the new file that no rule mentions
    outcomes: list
    rows: int
    report: Report
    cells: list                     # [(severity, Excel row, Excel column number (1-based), text)] for the highlighted copy
    notes: list = field(default_factory=list)

    def counts(self) -> dict:
        out = {"pass": 0, "fail": 0, "skipped": 0}
        for o in self.outcomes:
            out[o.status] += 1
        return out


# ---------------------------------------------------------------------------------------------------------------
# COLUMNS
# ---------------------------------------------------------------------------------------------------------------
def _words(name) -> tuple:
    return tuple(sorted(_name_words(name)))


def _plain(name) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).casefold())


def match_columns(expected: list, actual: list, allow_similar: bool = True):
    """-> ([ColumnMatch], [new columns]). Exact first, then the same words in another order, then a similar name; each column of the new file is used once."""
    taken, matches = set(), {}
    for how, same in (("exact", lambda e, a: _plain(e) == _plain(a)), ("reordered", lambda e, a: _words(e) == _words(a) and _words(e))):
        for e in expected:
            if e in matches:
                continue
            found = next((a for a in actual if a not in taken and same(e, a)), None)
            if found is not None:
                matches[e] = ColumnMatch(e, found, how)
                taken.add(found)
    if allow_similar:
        scored = []
        for e in expected:
            if e in matches:
                continue
            for a in actual:
                if a in taken:
                    continue
                ratio = SequenceMatcher(None, _plain(e), _plain(a)).ratio()
                we, wa = set(_words(e)), set(_words(a))
                contained = bool(we) and bool(wa) and (we <= wa or wa <= we) and min(len(_plain(e)), len(_plain(a))) >= 3
                if ratio >= SIMILAR_NAME or contained:
                    scored.append((max(ratio, 0.9 if contained else 0), e, a))
        for _, e, a in sorted(scored, key=lambda s: (-s[0], s[1], s[2])):
            if e not in matches and a not in taken:
                matches[e] = ColumnMatch(e, a, "similar")
                taken.add(a)
    ordered = [matches.get(e, ColumnMatch(e)) for e in expected]
    return ordered, [a for a in actual if a not in taken]


# ---------------------------------------------------------------------------------------------------------------
# THE RULES
# ---------------------------------------------------------------------------------------------------------------
def _failures(rule: model.Rule, series: pd.Series, day_first: bool, region: str, row_numbers: list):
    """-> (cells checked, [(position, value, what was wrong)]) for one rule on one column."""
    p = rule.params
    positions = [i for i, v in enumerate(series) if not blank(v)]
    values = {i: series.iloc[i] for i in positions}
    bad = []
    if rule.type == "required":
        bad = [(i, series.iloc[i], "blank") for i, v in enumerate(series) if blank(v)]
        return len(series), bad
    if rule.type == "allowed_values":
        allowed = {as_text(v).casefold() for v in p["values"]}
        bad = [(i, v, "not an allowed value") for i, v in values.items() if as_text(v).casefold() not in allowed]
    elif rule.type == "number_range":
        low, high = p.get("min"), p.get("max")
        for i, v in values.items():
            number = as_number(v)
            if number is None:
                bad.append((i, v, "not a number"))
            elif (low is not None and number < low) or (high is not None and number > high):
                bad.append((i, v, f"outside {model.setting_text(rule)}"))
    elif rule.type == "date_range":
        parsed = {i: rc.parse_date(v, day_first, True) for i, v in values.items()}
        bad = [(i, values[i], "not a date") for i, d in parsed.items() if d is None]
        dates = {i: d for i, d in parsed.items() if d is not None}
        if p.get("mode") == "fixed":
            low, high = p.get("min"), p.get("max")
            bad += [(i, values[i], f"outside {model.setting_text(rule)}") for i, d in dates.items()
                    if (low and d.isoformat() < low) or (high and d.isoformat() > high)]
        elif dates:
            # the window of max_span_days that holds the most dates; the dates outside it are the odd ones
            ordered = sorted(dates.items(), key=lambda item: item[1])
            span, best, start = p["max_span_days"], (0, 0), 0
            for end in range(len(ordered)):
                while (ordered[end][1] - ordered[start][1]).days > span:
                    start += 1
                if end - start + 1 > best[1] - best[0]:
                    best = (start, end + 1)
            inside = {i for i, _ in ordered[best[0]:best[1]]}
            first, last = ordered[best[0]][1], ordered[best[1] - 1][1]
            bad += [(i, values[i], f"{abs((d - (first if d < first else last)).days)} days outside the {span}-day window {first:%d %b} - {last:%d %b}")
                    for i, d in ordered if i not in inside]
    elif rule.type == "valid_email":
        bad = [(i, v, "not an email address") for i, v in values.items() if EMAIL.match(str(v).strip()) is None]
    elif rule.type == "valid_phone":
        bad = [(i, v, "not a phone number") for i, v in values.items() if not norm_phone(v, region)[0]]
    elif rule.type == "unique":
        first_seen = {}
        for i, v in values.items():
            key = as_text(v).casefold()
            if key in first_seen:
                bad.append((i, v, f"duplicate of row {first_seen[key]}"))
            else:
                first_seen[key] = row_numbers[i]
        return len(values), bad
    return len(values), sorted(bad, key=lambda b: b[0])


FAR_FACTOR = 10                     # a number this many times beyond the biggest limit is not a slip of the pen: it is serious


def is_far_outside(rule: model.Rule, value) -> bool:
    """True for a number that is out of its range by a mile - more than 10 x the largest limit (250,000 when the range is 160 .. 5,440)."""
    if rule.type != "number_range":
        return False
    number = as_number(value)
    low, high = rule.params.get("min"), rule.params.get("max")
    if number is None:
        return False
    outside = (high is not None and number > high) or (low is not None and number < low)
    return outside and abs(number) > FAR_FACTOR * max(abs(low or 0), abs(high or 0), 1.0)


def severity_of(rule: model.Rule, value=None) -> str:
    """How serious a broken rule is: the rule's usual level, High when a number is far outside its range."""
    return "high" if is_far_outside(rule, value) else model.SEVERITY[rule.type]


# ---------------------------------------------------------------------------------------------------------------
# THE CHECK
# ---------------------------------------------------------------------------------------------------------------
def check(table: pd.DataFrame, ruleset: model.RuleSet, name: str = "", sheet: str = "", allow_similar: bool = True) -> CheckResult:
    """Run the enabled rules of `ruleset` on `table` (as made by reconcile.columns.table_from_raw). Never changes the table."""
    source_rows = rc.source_rows(table) or [i + 2 for i in range(len(table))]
    source_columns = rc.source_columns(table) or list(range(len(table.columns)))
    column_number = dict(zip(table.columns, source_columns))                     # 0-based column of the sheet
    header_row = (source_rows[0] - 1) if source_rows else 1                      # Excel row of the header (data rows follow it)
    region = ruleset.region if ruleset.region in LOCALES else "PK"
    expected = list(dict.fromkeys(ruleset.columns + [r.column for r in ruleset.rules if r.column] +
                                  [c for r in ruleset.rules if r.type == "column_present" for c in r.params.get("columns", [])]))
    actual = [str(c) for c in table.columns]
    matches, extras = match_columns(expected, actual, allow_similar)
    found = {m.expected: m.actual for m in matches}
    day_first_cache = {}

    outcomes, cells, issues = [], [], []
    for rule in ruleset.enabled():
        if rule.type == "column_present":
            wanted = rule.params.get("columns", [])
            missing = [c for c in wanted if found.get(c) is None]
            outcome = Outcome(rule, "fail" if missing else "pass", checked=len(wanted), failing_total=len(missing), note=", ".join(missing))
            outcomes.append(outcome)
            if missing:
                issues.append(Issue("column_missing", "high", f"{len(missing)} expected column{'s' if len(missing) != 1 else ''} missing: {', '.join(missing)}",
                                    "The rules expect these columns, but the new file does not have them (or none looks like them). The rules for those columns could not be checked.",
                                    evidence=f"columns in the file: {', '.join(actual[:12])}{' ...' if len(actual) > 12 else ''}", impact=len(missing) * 1000,
                                    locations=[(sheet, f"{get_column_letter(column_number[actual[0]] + 1)}{header_row}")] if actual else [],
                                    fix="Add the column back (or rename it), or untick its rules in the rules file.", facts={"rule": rule.id}))
            continue
        column = found.get(rule.column)
        if column is None:
            outcomes.append(Outcome(rule, "skipped", note=f"the column '{rule.column}' is not in this file"))
            continue
        series = table[column]
        if column not in day_first_cache:
            day_first_cache[column] = rc.date_order(series, LOCALES[region]["day_first"])[0]
        checked, bad = _failures(rule, series, day_first_cache[column], region, source_rows)
        rows = [b[0] for b in bad]
        outcome = Outcome(rule, "fail" if bad else "pass", checked=checked, failing=rows[:MAX_ROWS_LISTED], failing_total=len(bad), column=column,
                          examples=[(source_rows[i], str(v)[:40] if not blank(v) else "(blank)", why) for i, v, why in bad[:EXAMPLES]])
        outcomes.append(outcome)
        if not bad:
            continue
        letter = get_column_letter(column_number[column] + 1)
        what = {"required": f"{len(bad)} blank cell{'s' if len(bad) != 1 else ''} in '{rule.column}'", "unique": f"{len(bad)} duplicate value{'s' if len(bad) != 1 else ''} in '{rule.column}'"}.get(
            rule.type, f"{len(bad)} value{'s' if len(bad) != 1 else ''} in '{rule.column}' break{'s' if len(bad) == 1 else ''} the rule: {rule.label.lower()}")
        shown = [f"row {r}: {v} ({w})" for r, v, w in outcome.examples]
        # a blank cell cannot be coloured, so a blank is marked on the column's header; every other problem on its own cell
        if rule.type == "required":
            locations = [(sheet, f"{letter}{header_row}")]
        else:
            locations = [(sheet, f"{letter}{source_rows[i]}") for i in outcome.failing]
        far = [v for _, v, _ in bad if is_far_outside(rule, v)]
        issue_severity = "high" if far else model.SEVERITY[rule.type]
        if far:
            what += f" ({len(far)} far outside the range)"
        issues.append(Issue(f"rule_{rule.type}", issue_severity, what,
                            f"Rule: {rule.label}{(' - ' + model.setting_text(rule)) if model.setting_text(rule) else ''}. " + (rule.why + "." if rule.why else ""),
                            evidence="; ".join(shown) + (f"; and {len(bad) - EXAMPLES} more" if len(bad) > EXAMPLES else ""), locations=locations, impact=len(bad),
                            fix="Correct these cells in the source and send the file again." if rule.type != "required" else "Fill in the blank cells (rows: "
                            + ", ".join(str(source_rows[i]) for i in rows[:10]) + (" ..." if len(rows) > 10 else "") + ").",
                            facts={"rule": rule.id, "column": column, "rows": [source_rows[i] for i in outcome.failing]}))
        for index in (outcome.failing if rule.type != "required" else []):
            why = next((w for r, _, w in outcome.examples if r == source_rows[index]), None)
            cell_severity = severity_of(rule, series.iloc[index])
            cells.append((cell_severity, source_rows[index], column_number[column] + 1,
                          f"[{cell_severity.upper()}] {rule.label}: {rule.column}\n{why or 'breaks the rule'}"))
        if rule.type == "required":
            cells.append((model.SEVERITY[rule.type], header_row, column_number[column] + 1,
                          f"[{model.SEVERITY[rule.type].upper()}] {len(bad)} blank cell(s) in this column (required). Rows: " + ", ".join(str(source_rows[i]) for i in rows[:25]) + (" ..." if len(rows) > 25 else "")))

    failed = [o for o in outcomes if o.status == "fail"]
    if len(table) == 0:
        issues.append(Issue("no_rows", "high", "The file has no data rows", "There is a header but nothing below it.", impact=10 ** 6))
    status = "FAIL" if failed or len(table) == 0 else "PASS"
    report = _report(ruleset, name, sheet, table, matches, extras, outcomes, issues, status, allow_similar)
    return CheckResult(status, matches, extras, outcomes, len(table), report, cells, list(report.notes))


def _report(ruleset, name, sheet, table, matches, extras, outcomes, issues, status, allow_similar) -> Report:
    counts = {"pass": 0, "fail": 0, "skipped": 0}
    for o in outcomes:
        counts[o.status] += 1
    renamed = [m for m in matches if m.actual is not None and m.how != "exact"]
    missing = [m for m in matches if m.actual is None]
    facts = [Fact("Result", f"{status} - {counts['fail']} of {counts['pass'] + counts['fail']} rules failed" if status == "FAIL" else f"PASS - all {counts['pass']} rules passed",
                  f"{len(table):,} rows checked against '{ruleset.name}'", "high"),
             Fact("File", f"{len(table):,} rows, {len(table.columns)} columns" + (f" (sheet '{sheet}')" if sheet else ""), name or "the uploaded file", "high"),
             Fact("Rules", f"{counts['pass'] + counts['fail']} checked" + (f", {counts['skipped']} skipped" if counts["skipped"] else ""),
                  f"rules file made from '{ruleset.source or 'a sample file'}' on {ruleset.created or 'an unknown date'}", "high")]
    found = len(matches) - len(missing)
    columns = f"{found} of {len(matches)} expected columns found"
    if renamed:
        columns += "; renamed: " + ", ".join(f"'{m.expected}' is '{m.actual}'" for m in renamed[:4]) + (" ..." if len(renamed) > 4 else "")
    if missing:
        columns += "; missing: " + ", ".join(f"'{m.expected}'" for m in missing[:4]) + (" ..." if len(missing) > 4 else "")
    if extras:
        columns += "; new, no rules: " + ", ".join(f"'{c}'" for c in extras[:4]) + (" ..." if len(extras) > 4 else "")
    facts.append(Fact("Columns", columns, "matched by name; a renamed column is accepted when its name is nearly the same" if allow_similar else "matched by name only",
                      "high" if not renamed and not missing else "medium"))
    notes = []
    for o in outcomes:
        if o.status == "skipped":
            notes.append(f"Skipped '{o.rule.label}' on '{o.rule.column}': {o.note}.")
    for m in renamed:
        notes.append(f"Column '{m.expected}' was found as '{m.actual}' ({'same words, another order' if m.how == 'reordered' else 'a very similar name'}); its rules were applied to it.")
    return Report("Check every month", facts=facts, issues=rank(issues), notes=notes)
