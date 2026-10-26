"""
toolkit/ranking.py
-------------------
The one rule that orders a "Please check" list, for every tool: the more serious first; within the same seriousness
the bigger amount first, then the one touching more cells, then the sheet that comes first in the workbook.
"""

from toolkit.report import SEVERITIES


def rank(issues, sheet_order=()):
    """The issues, most important first (a new list)."""
    order = {name: position for position, name in enumerate(sheet_order)}

    def key(issue):
        first_sheet = issue.locations[0][0] if issue.locations else None
        return (SEVERITIES.index(issue.severity) if issue.severity in SEVERITIES else len(SEVERITIES),
                -abs(float(issue.impact or 0)), -len(issue.locations), order.get(first_sheet, len(order)))

    return sorted(issues, key=key)
