from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import io
import json
import os
import sys
from unittest import mock

sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

import ai_rules
import cleaning
import quality
import rule_engine
from io_utils import load_sheets

rng = np.random.default_rng(8)
N = 240


def weighted(values_counts):
    out = [v for v, c in values_counts.items() for _ in range(c)]
    rng.shuffle(out)
    return out


GENDER = {"Male": 80, "Female": 70, "Other": 60, "male": 7, "MALE": 6, "m": 5, "M": 4, "f": 4, "F": 2, "female": 1, "FEMALE": 1}
assert sum(GENDER.values()) == N
frame = pd.DataFrame({
    "gender": weighted(GENDER),
    "order_status": rng.choice(["Delivered", "Shipped", "delivered", "Pending"], N),
    "payment_method": rng.choice(["Cash", "Card", "UPI"], N),
    "city": rng.choice([f"City {i}" for i in range(20)], N),
    # ---- never a full list ----
    "customer_name": rng.choice(["Ada Lovelace", "Bob Stone", "Cy Young"], N),            # personal data (name)
    "first_name": rng.choice(["Ada", "Bob"], N),
    "email": rng.choice(["ada@x.com", "bob@y.org"], N),                                    # personal data (name + values)
    "reach": rng.choice(["ada@x.com", "bob@y.org"], N),                                    # innocent name, email VALUES
    "phone": rng.choice(["0300-1234567", "0321-7654321"], N),                              # phone
    "alt": rng.choice(["+92 300 1111111", "+92 321 2222222"], N),                          # innocent name, phone VALUES
    "customer_id": rng.choice(["C1", "C2", "C3"], N),                                      # identifier name
    "order_id": [f"ORD-{i:05d}" for i in range(N)],                                        # unique
    "address": rng.choice(["1 Main Street", "2 High Road"], N),                            # personal data
    "dob": rng.choice(["1990-01-01", "1985-05-05"], N),
    "notes": rng.choice(["a very long free text note that goes well beyond forty characters", "short"], N),
    "many": [f"v{i % 31}" for i in range(N)],                                              # 31 distinct -> too many
    "edge30": [f"w{i % 30}" for i in range(N)],                                            # exactly 30 distinct -> allowed
    "near_unique": [f"u{i % 20}" for i in range(30)] + [np.nan] * (N - 30),                # 20 distinct in 30 values -> labels, not categories
    "quantity": rng.integers(1, 5, N),                                                     # numeric
})
profiles = {p["column"]: p for p in ai_rules.build_column_profile(frame, mask=False)}
lists = {c for c, p in profiles.items() if "distinct_values" in p}
print("full lists:", sorted(lists))
print("samples   :", sorted(set(profiles) - lists))

print("TEST: which columns get the complete value list")
assert lists == {"gender", "order_status", "payment_method", "city", "edge30"}, lists
for column in ("customer_name", "first_name", "email", "reach", "phone", "alt", "customer_id", "order_id", "address", "dob",
               "notes", "many", "near_unique", "quantity"):
    assert "sample_values" in profiles[column] and "distinct_values" not in profiles[column], column
    assert len(profiles[column]["sample_values"]) <= 8
assert ai_rules.full_value_columns(list(profiles.values())) == [c for c in frame.columns if c in lists]
print("PASSED")

print("TEST: the list is complete, exact, includes the rare variants, and is sorted by count")
gender = profiles["gender"]
assert dict(map(tuple, gender["distinct_values"])) == GENDER
assert gender["distinct_values"][0] == ["Male", 80] and gender["distinct_values"][-1][1] == 1
counts = [c for _, c in gender["distinct_values"]]
assert counts == sorted(counts, reverse=True)
assert {"m", "MALE", "FEMALE", "f"} <= {v for v, _ in gender["distinct_values"]}                  # the variants random sampling would likely miss
assert gender["non_null_count"] == N and gender["null_count"] == 0 and "sample_values" not in gender
print("PASSED")

print("TEST: placeholders and blanks stay out of the list and count as missing")
junk = pd.DataFrame({"status": ["ok", "ok", "ok", "bad", "bad", "bad", "ERROR", "unknown", np.nan, ""]})
p = ai_rules.build_column_profile(junk, mask=False)[0]
assert p["distinct_values"] == [["bad", 3], ["ok", 3]] and p["non_null_count"] == 6 and p["null_count"] == 4
print("PASSED")

print("TEST: masking still applies - shapes only, counts merged")
masked = {p["column"]: p for p in ai_rules.build_column_profile(frame, mask=True)}
mg = masked["gender"]["distinct_values"]
assert all(set(v) <= {"X", "x"} for v, _ in mg)                                                  # no real letter left
merged = dict(map(tuple, mg))
assert merged["Xxxx"] == GENDER["Male"] + 0 and merged["xxxx"] == GENDER["male"] and merged["XXXX"] == GENDER["MALE"]
assert merged["x"] == GENDER["m"] + GENDER["f"] and merged["X"] == GENDER["M"] + GENDER["F"]    # 'm' and 'f' share the mask 'x'
assert sum(merged.values()) == N
merge_frame = pd.DataFrame({"pay": ["Cash"] * 5 + ["Card"] * 3 + ["Upi"] * 4})
merged_pay = ai_rules.build_column_profile(merge_frame, mask=True)[0]["distinct_values"]
assert merged_pay == [["Xxxx", 8], ["Xxx", 4]]                                                   # Cash + Card collapse into one shape
assert masked["customer_name"]["sample_values"] and all(set(v) <= {"X", "x", " "} for v in masked["customer_name"]["sample_values"])
print("PASSED")

print("TEST: the prompt explains both forms and carries the complete list (and only samples for personal data)")
prompt = ai_rules.build_prompt(list(profiles.values()), "IN")
for needle in ['"distinct_values"', '"sample_values"', "COMPLETE", "[[value, count], ...]", "every variant that\nexists, including rare ones",
               "all of\n  distinct_values when the column has it", "never invent a key", "INCLUDING the standard value itself"]:
    assert needle in prompt, needle
profile_part = prompt.split("Column profiles:")[1]
assert '"MALE"' in profile_part and '"FEMALE"' in profile_part and '"m"' in profile_part                  # rare variants are in what is sent
for p_ in json.loads(profile_part):                                                                       # personal data / identifiers: samples only
    if p_["column"] in ("customer_name", "first_name", "email", "reach", "phone", "alt", "customer_id", "address", "dob"):
        assert "distinct_values" not in p_ and len(p_["sample_values"]) <= 8
print("PASSED")

print("TEST: suggest_rules reports which columns got the full list")
reply = json.dumps({"rules": []})
with mock.patch.object(ai_rules, "_call_groq", lambda prompt_: reply):
    result = ai_rules.suggest_rules(list(profiles.values()), "IN", "groq")
assert result["full_value_columns"] == [c for c in frame.columns if c in lists]
print("PASSED")

print("TEST: with the full list the AI can map EVERY variant, and validation accepts it")
mapping = {v.lower(): "Male" for v in ("Male", "male", "MALE", "m", "M")}
mapping.update({v.lower(): "Female" for v in ("Female", "female", "FEMALE", "f", "F")})
rule = {"id": "r1", "column": "gender", "rule_type": "map_values", "params": {"mapping": mapping}, "description": "Standardize gender."}
known = {"gender": quality.casefolded_values(frame["gender"])}
assert rule_engine.validate_rule(rule, list(frame.columns), known) == ""
out, stats = rule_engine.apply_rule(frame.copy(), rule)
assert stats["converted"] == 7 + 6 + 5 + 4 + 4 + 2 + 1 + 1 and stats["unmapped"] == GENDER["Other"]
print("PASSED")

print("TEST: the AI panel tells the user which columns had their full value list sent")
def new_app(csv_frame, name="data.csv"):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.run()
    at.file_uploader[0].upload(name, csv_frame.to_csv(index=False).encode(), "text/csv")
    at.run()
    next(b for b in at.button if b.label == "Clean File").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at

small = pd.DataFrame({
    "gender": weighted({"Male": 20, "Female": 15, "Other": 10, "m": 3, "MALE": 2}),
    "order_status": (["Delivered"] * 25 + ["shipped"] * 25),
    "customer_name": [f"Person {i}" for i in range(50)],
    "order_id": [f"O{i}" for i in range(50)],
})
sent_prompts = []
def spy(prompt_):
    sent_prompts.append(prompt_)
    return json.dumps({"rules": []})
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake", "AI_PROVIDER": "groq"}), mock.patch.object(ai_rules, "_call_groq", spy):
    at = new_app(small)
    assert not [c for c in at.caption if "Complete list of distinct values" in c.value]               # nothing yet, before suggesting
    next(b for b in at.button if b.label == "Suggest Cleaning Rules").click()
    at.run()
assert not at.exception, [e.value for e in at.exception]
notice = [c.value for c in at.caption if "Complete list of distinct values" in c.value]
print(notice)
assert len(notice) == 1 and "gender, order_status" in notice[0] and "customer_name" not in notice[0] and "order_id" not in notice[0]
assert "up to 8 random sample values" in notice[0]
sent = sent_prompts[0].split("Column profiles:")[1]
assert '"m"' in sent and '"MALE"' in sent                                                           # the rare variants really were sent
assert sent.count('"Person ') <= 8
assert any("complete list" in c.value.lower() for c in at.caption if "AI never sees your full file" in c.value)
print("PASSED")

print("TEST: no column qualifies -> the panel says only samples were sent")
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake", "AI_PROVIDER": "groq"}), mock.patch.object(ai_rules, "_call_groq", spy):
    at2 = new_app(pd.DataFrame({"customer_name": [f"P{i}" for i in range(30)], "order_id": [f"O{i}" for i in range(30)]}))
    next(b for b in at2.button if b.label == "Suggest Cleaning Rules").click()
    at2.run()
assert any("No column qualified for a full value list" in c.value for c in at2.caption)
print("PASSED")

print("TEST: masking on -> the lists the AI gets are masked")
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake", "AI_PROVIDER": "groq"}), mock.patch.object(ai_rules, "_call_groq", spy):
    at3 = new_app(small)
    next(c for c in at3.checkbox if c.label.startswith("Mask sample values")).check()
    at3.run()
    sent_prompts.clear()
    next(b for b in at3.button if b.label == "Suggest Cleaning Rules").click()
    at3.run()
masked_sent = sent_prompts[0].split("Column profiles:")[1]
assert '"Delivered"' not in masked_sent and '"Xxxxxxxxx"' in masked_sent and '"MALE"' not in masked_sent
assert any("gender" in c.value for c in at3.caption if "Complete list of distinct values" in c.value)      # still tells the user a (masked) list was sent
print("PASSED")

print("TEST: a loaded suggestions file does not claim anything about what was sent")
saved = {"recipe_name": "x", "locale": "PK", "provider": "groq", "model": "m", "date": "2026-01-01", "rules": []}
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake"}):
    at4 = new_app(small)
    next(f for f in at4.file_uploader if f.key == "recipe_uploader").upload("s.json", json.dumps(saved).encode(), "application/json")
    at4.run()
    next(b for b in at4.button if b.label == "Load Recipe").click()
    at4.run()
assert not at4.exception and not [c for c in at4.caption if "Complete list of distinct values" in c.value or "No column qualified" in c.value]
print("PASSED")

print("TEST: the real retail file (skipped if absent)")
retail = _Path.home() / "Downloads" / "archive" / "retail_sales_dataset.csv"
if retail.exists():
    original = load_sheets(io.BytesIO(retail.read_bytes()), is_csv=True)["Data"]
    cleaned, _ = cleaning.clean_dataframe_with_steps(original.copy(), {"headers": True, "trim": True, "duplicates": True, "empty": True,
                                                                        "placeholders": True, "numeric": True, "fills": False})
    prof = {p["column"]: p for p in ai_rules.build_column_profile(cleaned, mask=False)}
    got = ai_rules.full_value_columns(list(prof.values()))
    print("full lists:", got)
    print("samples   :", [c for c in prof if c not in got])
    assert set(got) == {"gender", "region", "city", "product_category", "payment_method", "return_flag", "order_status"}
    assert dict(map(tuple, prof["gender"]["distinct_values"])) == cleaned["gender"].value_counts().to_dict()
    assert len(prof["gender"]["distinct_values"]) == 11 and {"m", "MALE", "FEMALE"} <= {v for v, _ in prof["gender"]["distinct_values"]}
    for column in ("order_id", "customer_id", "customer_name", "product_name", "order_date"):
        assert "sample_values" in prof[column], column
    print("PASSED")
else:
    print("skipped")

print("\nALL VALUE LIST TESTS PASSED")
