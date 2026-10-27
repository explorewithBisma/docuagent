"""
toolkit/report.py
------------------
The data shapes every tool shares, so they can all use the same "What I found" card, the same "Please check" list and the
same highlighted-output engine:

  Finding  - one detected value with its confidence and where it came from (re-exported by budget/detect.py)
  Fact     - one line of the "What I found" card: label, value, source, confidence
  Issue    - one problem or doubt: how serious, what, why it matters, the evidence, and where it is in the workbook
  Report   - facts + issues + counts + notes: what a tool hands to the page
"""

from dataclasses import dataclass, field

SEVERITIES = ("high", "medium", "low")
CONFIDENCES = ("high", "medium", "low")


@dataclass
class Finding:
    value: object
    confidence: str                     # "high" | "medium" | "low"
    source: str                         # a plain sentence: where it came from
    alternatives: list = field(default_factory=list)      # [(value, source)] other candidates


@dataclass
class Fact:
    label: str
    value: str
    source: str
    confidence: str = "high"


@dataclass
class Issue:
    kind: str                           # machine name of the check that found it, e.g. "criteria_no_match"
    severity: str                       # "high" | "medium" | "low"
    title: str                          # one short sentence
    detail: str                         # plain-language explanation: what is wrong and why it matters
    evidence: str = ""                  # the numbers / texts the finding is based on
    locations: list = field(default_factory=list)       # [(sheet name, "B5")]: the first one is the main cell
    impact: float = 0.0                 # money (or quantity) affected, for ordering; 0 when unknown
    fix: str = ""                       # what the user could do
    action: str = None                  # optional one-click fix the page can offer
    facts: dict = field(default_factory=dict)          # machine-readable details a later step can use (e.g. to join related findings)

    @property
    def where(self) -> str:
        """'Sheet!B5' of the main cell (and '+N more' when several cells are involved), or ''."""
        if not self.locations:
            return ""
        sheet, cell = self.locations[0]
        more = f" (+{len(self.locations) - 1} more)" if len(self.locations) > 1 else ""
        return f"{sheet}!{cell}{more}"


@dataclass
class Report:
    title: str
    facts: list = field(default_factory=list)           # [Fact]
    issues: list = field(default_factory=list)          # [Issue], most important first
    stats: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)           # plain sentences: what could not be checked, etc.

    def counts(self) -> dict:
        return {severity: sum(1 for i in self.issues if i.severity == severity) for severity in SEVERITIES}
