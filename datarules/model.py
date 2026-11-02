"""
datarules/model.py
-------------------
The rules of "Check every month": what a rule is, how a rules file is saved and read back (JSON), and how the one-line "setting" of a rule
in the editing table is written and parsed. A rules file is plain data - nothing in it can run code.

  {"version": 1, "name": "Monthly sales", "source": "sample.xlsx", "created": "2026-05-31", "region": "PK",
   "columns": ["Order ID", "Date", ...],                      # the columns of the sample, in order
   "rules": [{"id": "Amount:number_range", "column": "Amount", "type": "number_range", "enabled": true,
              "params": {"min": 0, "max": 250}, "why": "the sample had 3.5 to 98 (10% margin)"}, ...]}
"""

import json
import re
from dataclasses import dataclass, field

VERSION = 1
TYPES = ("column_present", "required", "allowed_values", "number_range", "date_range", "valid_email", "valid_phone", "unique")
LABELS = {
    "column_present": "Expected columns are present", "required": "Must not be blank", "allowed_values": "Only these values",
    "number_range": "Number between", "date_range": "Dates", "valid_email": "Valid email address", "valid_phone": "Valid phone number",
    "unique": "Every value different (no duplicates)",
}
SEVERITY = {"column_present": "high", "unique": "high", "required": "medium", "allowed_values": "medium", "number_range": "medium", "date_range": "medium",
            "valid_email": "medium", "valid_phone": "medium"}


@dataclass
class Rule:
    id: str
    column: str                     # '' for the table-level rule (column_present)
    type: str
    params: dict = field(default_factory=dict)
    enabled: bool = True
    why: str = ""

    @property
    def label(self) -> str:
        return LABELS[self.type]


@dataclass
class RuleSet:
    name: str = "My monthly checks"
    source: str = ""
    created: str = ""
    region: str = "PK"
    columns: list = field(default_factory=list)
    rules: list = field(default_factory=list)

    def enabled(self) -> list:
        return [r for r in self.rules if r.enabled]


# ---------------------------------------------------------------------------------------------------------------
# THE FILE
# ---------------------------------------------------------------------------------------------------------------
def to_json(ruleset: RuleSet) -> bytes:
    data = {"version": VERSION, "name": ruleset.name, "source": ruleset.source, "created": ruleset.created, "region": ruleset.region,
            "columns": list(ruleset.columns),
            "rules": [{"id": r.id, "column": r.column, "type": r.type, "enabled": bool(r.enabled), "params": r.params, "why": r.why} for r in ruleset.rules]}
    return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


def _param_problem(rule_type: str, params: dict) -> str:
    """'' when the parameters make sense for the rule type, else what is wrong (in words)."""
    if not isinstance(params, dict):
        return "its settings are not an object"
    if rule_type == "column_present":
        columns = params.get("columns")
        return "" if isinstance(columns, list) and all(isinstance(c, str) for c in columns) else "it needs a list of column names"
    if rule_type == "allowed_values":
        values = params.get("values")
        return "" if isinstance(values, list) and values else "it needs a list of allowed values"
    if rule_type == "number_range":
        low, high = params.get("min"), params.get("max")
        if low is None and high is None:
            return "it needs a minimum or a maximum"
        if any(v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))) for v in (low, high)):
            return "the minimum and maximum must be numbers"
        return "the minimum is above the maximum" if low is not None and high is not None and low > high else ""
    if rule_type == "date_range":
        if params.get("mode") == "span":
            days = params.get("max_span_days")
            return "" if isinstance(days, int) and not isinstance(days, bool) and days >= 0 else "it needs a number of days"
        if params.get("mode") == "fixed":
            low, high = params.get("min"), params.get("max")
            if not (low or high):
                return "it needs a first and/or last date"
            for value in (low, high):
                if value is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
                    return "dates must be written like 2026-05-31"
            return "the first date is after the last" if low and high and str(low) > str(high) else ""
        return "its mode must be 'span' or 'fixed'"
    return ""


def from_json(data) -> RuleSet:
    """Read a rules file (bytes or text). Raises ValueError with a message the user can act on."""
    try:
        raw = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        raise ValueError("This is not a rules file: it is not valid JSON. Use the .json file that 'Make rules' gave you.")
    if not isinstance(raw, dict) or "rules" not in raw or not isinstance(raw["rules"], list):
        raise ValueError("This is not a rules file made by this tool (it has no list of rules).")
    version = raw.get("version")
    if version != VERSION:
        raise ValueError(f"This rules file has version {version!r}; this tool reads version {VERSION}. Make the rules again from your sample file.")
    rules, seen = [], set()
    for position, item in enumerate(raw["rules"], start=1):
        if not isinstance(item, dict) or item.get("type") not in TYPES:
            raise ValueError(f"Rule {position} has an unknown type ({item.get('type') if isinstance(item, dict) else item!r}). Known types: {', '.join(TYPES)}.")
        column = item.get("column", "")
        if not isinstance(column, str) or (item["type"] != "column_present" and not column.strip()):
            raise ValueError(f"Rule {position} ({item['type']}) has no column name.")
        problem = _param_problem(item["type"], item.get("params", {}))
        if problem:
            raise ValueError(f"Rule {position} ({LABELS[item['type']]} on '{column}'): {problem}.")
        rule_id = str(item.get("id") or f"{column}:{item['type']}")
        if rule_id in seen:
            rule_id = f"{rule_id}#{position}"
        seen.add(rule_id)
        rules.append(Rule(rule_id, column, item["type"], dict(item.get("params", {})), bool(item.get("enabled", True)), str(item.get("why", ""))))
    columns = raw.get("columns", [])
    return RuleSet(str(raw.get("name", "My monthly checks")), str(raw.get("source", "")), str(raw.get("created", "")), str(raw.get("region", "PK")),
                   [str(c) for c in columns] if isinstance(columns, list) else [], rules)


# ---------------------------------------------------------------------------------------------------------------
# THE ONE-LINE SETTING OF A RULE (the editable cell of the table)
# ---------------------------------------------------------------------------------------------------------------
def _number_text(value) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else f"{float(value):g}"


def setting_text(rule: Rule) -> str:
    p = rule.params
    if rule.type == "column_present":
        return " | ".join(p.get("columns", []))
    if rule.type == "allowed_values":
        return " | ".join(str(v) for v in p.get("values", []))
    if rule.type == "number_range":
        return f"{_number_text(p.get('min'))} .. {_number_text(p.get('max'))}"
    if rule.type == "date_range":
        if p.get("mode") == "span":
            return f"within {p['max_span_days']} days"
        return f"{p.get('min') or ''} .. {p.get('max') or ''}"
    return ""


def parse_setting(rule_type: str, text: str) -> dict:
    """The parameters from the text of the table cell. Raises ValueError (a sentence) when the text cannot be understood."""
    text = (text or "").strip()
    if rule_type == "column_present":
        names = [n.strip() for n in text.split("|") if n.strip()]
        if not names:
            raise ValueError("write the column names separated by |")
        return {"columns": names}
    if rule_type == "allowed_values":
        values = [v.strip() for v in text.split("|") if v.strip()]
        if not values:
            raise ValueError("write the allowed values separated by |")
        return {"values": values}
    if rule_type == "number_range":
        match = re.fullmatch(r"\s*(-?[\d.,]*\d)?\s*(?:\.\.|to|-{2}|–)\s*(-?[\d.,]*\d)?\s*", text)
        if not match or not (match.group(1) or match.group(2)):
            raise ValueError("write the range like 0 .. 250 (leave one side empty for no limit)")
        low, high = (float(g.replace(",", "")) if g else None for g in match.groups())
        low, high = [None if v is None else (int(v) if v.is_integer() else v) for v in (low, high)]
        if low is not None and high is not None and low > high:
            raise ValueError("the minimum is above the maximum")
        return {"min": low, "max": high}
    if rule_type == "date_range":
        span = re.fullmatch(r"\s*within\s+(\d+)\s*days?\s*", text, re.IGNORECASE)
        if span:
            return {"mode": "span", "max_span_days": int(span.group(1))}
        match = re.fullmatch(r"\s*(\d{4}-\d{2}-\d{2})?\s*(?:\.\.|to)\s*(\d{4}-\d{2}-\d{2})?\s*", text)
        if not match or not (match.group(1) or match.group(2)):
            raise ValueError("write 'within 45 days' or a range like 2026-05-01 .. 2026-05-31")
        low, high = match.groups()
        if low and high and low > high:
            raise ValueError("the first date is after the last")
        return {"mode": "fixed", "min": low, "max": high}
    return {}
