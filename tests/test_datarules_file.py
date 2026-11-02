from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Check every month: the rules file (JSON) - round trip, readable errors, and the one-line setting of each rule."""
import json
import sys

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import datarules_fixtures as fx
from datarules import model, suggest
from reconcile import columns as rc
from reconcile_helpers import to_raw

table, _ = rc.table_from_raw(to_raw(fx.sales_month(2026, 4)))
rules = suggest.suggest(table, "april.xlsx", "AE", name="Monthly sales")

print("TEST: a rules file is saved as JSON and read back unchanged")
data = model.to_json(rules)
back = model.from_json(data)
assert back == rules and json.loads(data)["version"] == 1 and json.loads(data)["region"] == "AE"
rules.rules[3].enabled = False
assert model.from_json(model.to_json(rules)).rules[3].enabled is False                                    # an unticked rule stays unticked
print("PASSED")

print("TEST: a file that is not a rules file says so in words")
bad = {
    b"not json at all": "not valid JSON",
    b'{"hello": 1}': "no list of rules",
    json.dumps({"version": 7, "rules": []}).encode(): "version 7",
    json.dumps({"version": 1, "rules": [{"type": "magic", "column": "A"}]}).encode(): "unknown type",
    json.dumps({"version": 1, "rules": [{"type": "required", "column": ""}]}).encode(): "no column name",
    json.dumps({"version": 1, "rules": [{"type": "allowed_values", "column": "A", "params": {}}]}).encode(): "list of allowed values",
    json.dumps({"version": 1, "rules": [{"type": "number_range", "column": "A", "params": {"min": 9, "max": 1}}]}).encode(): "minimum is above the maximum",
    json.dumps({"version": 1, "rules": [{"type": "number_range", "column": "A", "params": {"min": "x"}}]}).encode(): "must be numbers",
    json.dumps({"version": 1, "rules": [{"type": "date_range", "column": "A", "params": {"mode": "weekly"}}]}).encode(): "mode must be",
    json.dumps({"version": 1, "rules": [{"type": "date_range", "column": "A", "params": {"mode": "fixed", "min": "May 1"}}]}).encode(): "written like 2026-05-31",
}
for blob, words in bad.items():
    try:
        model.from_json(blob)
    except ValueError as error:
        assert words in str(error), (blob[:40], str(error))
    else:
        raise AssertionError(f"accepted {blob[:50]}")
print("PASSED")

print("TEST: the setting text of every rule type is written and parsed back (this is what the editable table uses)")
for rule in model.from_json(data).rules:
    text = model.setting_text(rule)
    assert model.parse_setting(rule.type, text) == rule.params, (rule.id, text)
assert model.parse_setting("number_range", "0 .. 250") == {"min": 0, "max": 250}
assert model.parse_setting("number_range", "1,000.5 to 2,000") == {"min": 1000.5, "max": 2000}
assert model.parse_setting("number_range", " .. 99") == {"min": None, "max": 99} and model.parse_setting("number_range", "-5 .. ") == {"min": -5, "max": None}
assert model.parse_setting("date_range", "within 45 days") == {"mode": "span", "max_span_days": 45}
assert model.parse_setting("date_range", "2026-05-01 .. 2026-05-31") == {"mode": "fixed", "min": "2026-05-01", "max": "2026-05-31"}
assert model.parse_setting("allowed_values", "Paid | Pending |  Refunded ") == {"values": ["Paid", "Pending", "Refunded"]}
assert model.parse_setting("column_present", "A | B") == {"columns": ["A", "B"]} and model.parse_setting("unique", "ignored") == {}
for rule_type, text, words in [("number_range", "lots", "write the range"), ("number_range", "9 .. 1", "above the maximum"), ("date_range", "soonish", "write 'within"),
                               ("date_range", "2026-06-01 .. 2026-05-01", "after the last"), ("allowed_values", " | ", "allowed values"), ("column_present", "", "column names")]:
    try:
        model.parse_setting(rule_type, text)
    except ValueError as error:
        assert words in str(error), (rule_type, text, str(error))
    else:
        raise AssertionError((rule_type, text))
print("PASSED")
