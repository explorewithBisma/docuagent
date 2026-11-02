"""
dedupe/analyze.py
------------------
Near-duplicate detection for one cleaned sheet: pick the identity columns, normalise, find candidate pairs, score them in words, group
them. Returns everything the page needs - and changes nothing.

    result = find_near_duplicates(df, DedupeOptions(locale="AE"))
    result.groups      the groups (high first)
    result.report      the "What I found" card (toolkit Report)
    result.summary     "23 groups of possible duplicates, 51 rows (14 high-confidence, 9 maybe)"
"""

import time
from dataclasses import dataclass, field

import pandas as pd

from dedupe import blocking, columns, groups as grouping, normalize as nz, prepare, score
from toolkit.report import Fact, Report

KIND_TEXT = {"contacts": "a list of people or companies", "transactions": "a list of transactions", "unknown": "not clear"}


@dataclass
class DedupeOptions:
    strictness: str = "normal"                  # strict | normal | loose
    locale: str = "PK"                          # which country's phone numbers ("0300-1234567" in Pakistan)
    gmail_rule: bool = False                    # ignore dots and +tags in gmail addresses
    mode: str = "auto"                          # auto | contacts | transactions
    overrides: dict = field(default_factory=dict)       # {column: role or "ignore"}
    variants: list = None                       # name spelling groups; None = the shipped list

    def key(self) -> str:
        return repr((self.strictness, self.locale, self.gmail_rule, self.mode, sorted(self.overrides.items()), self.variants))


@dataclass
class DedupeResult:
    profile: object
    groups: list
    stats: dict
    notes: list
    report: Report
    summary: str
    options: DedupeOptions


def summarize(groups: list) -> str:
    if not groups:
        return "No near-duplicates were found."
    rows = sum(len(g.rows) for g in groups)
    high = sum(1 for g in groups if g.level == "high")
    maybe = len(groups) - high
    return (f"{len(groups)} group{'s' if len(groups) != 1 else ''} of possible duplicates, {rows} rows "
            f"({high} high-confidence, {maybe} maybe)")


def find_near_duplicates(df: pd.DataFrame, options: DedupeOptions = None) -> DedupeResult:
    options = options or DedupeOptions()
    started = time.perf_counter()
    profile = columns.build_profile(df, options.overrides, options.mode)
    notes = []
    results, groups = [], []
    pairs_compared = 0

    if profile.kind == "unknown" or len(df) < 2:
        notes.append("No column that identifies a person or company was found, so there is nothing to compare. "
                     "Choose the columns under Edit details." if profile.kind == "unknown" else "There are fewer than two rows.")
    else:
        variants = nz.variant_map(options.variants if options.variants is not None else nz.load_variant_groups())
        P = prepare.prepare(df, profile, options.locale, variants, options.gmail_rule)
        keys = blocking.record_keys(P) if profile.kind == "transactions" else blocking.contact_keys(P)
        pairs, block_notes = blocking.candidate_pairs(keys)
        notes += block_notes
        scorer = score.score_record_pair if profile.kind == "transactions" else score.score_pair
        for i, j in pairs:
            result = scorer(P, i, j, options.strictness)
            if result is not None:
                results.append(result)
        results.sort(key=lambda r: (r.i, r.j))           # a fixed order, whatever order the pairs were found in
        pairs_compared = len(pairs)
        groups = grouping.build_groups(P, results)

    stats = {"rows": len(df), "pairs_compared": pairs_compared, "groups": len(groups), "high": sum(1 for g in groups if g.level == "high"),
             "maybe": sum(1 for g in groups if g.level == "maybe"), "rows_in_groups": sum(len(g.rows) for g in groups),
             "seconds": round(time.perf_counter() - started, 2)}
    summary = summarize(groups)
    return DedupeResult(profile, groups, stats, notes, build_report(profile, summary, notes), summary, options)


def build_report(profile, summary: str, notes: list) -> Report:
    facts = [Fact("File type", KIND_TEXT[profile.kind], profile.kind_reason, profile.kind_confidence)]
    used = profile.used()
    if used:
        listing = ", ".join(f"{r.column} ({r.role})" if r.role != "record" else r.column for r in used if r.role != "record")
        if profile.kind == "transactions":
            listing += (", " if listing else "") + f"and {sum(1 for r in used if r.role == 'record')} other columns of the whole record"
        reasons = "; ".join(f"{r.column}: {r.why}" for r in used if r.role != "record")
        facts.append(Fact("Compared on", listing, reasons or "every column except the record number", "high"))
    ignored = [r for r in profile.roles if not r.used]
    if ignored:
        facts.append(Fact("Not used", ", ".join(r.column for r in ignored),
                          "; ".join(f"{r.column}: {r.why}" for r in ignored[:6]) + (" ..." if len(ignored) > 6 else ""), "high"))
    return Report("Near-duplicates", facts=facts, notes=notes)
