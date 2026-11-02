from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""'Similar' names (several typos in long names): only ever a Maybe, and only next to other evidence."""
import sys

sys.path.insert(0, str(ROOT))

import pandas as pd

from dedupe import normalize as nz
from dedupe.analyze import DedupeOptions, find_near_duplicates
from dedupe.score import classify

V = nz.variant_map(nz.load_variant_groups())
key = lambda text: nz.name_key(text, V)

print("TEST: compare_names - 'almost' is unchanged; several typos in long names are 'similar'; different names stay different")
assert nz.compare_names(key("isabella lodder"), key("isablela loddwr"))[0] == "similar"
assert nz.compare_names(key("alexander richardson"), key("alexandar richardsen"))[0] == "similar"
assert nz.compare_names(key("hassan"), key("hussain"))[0] == "similar"
assert nz.compare_names(key("Sara Khan"), key("Sana Khan"))[0] == "almost"                 # one typo: as before
assert nz.compare_names(key("Hassan Mailk"), key("Hassan Malik"))[0] == "almost"
assert nz.compare_names(key("ali khan"), key("omar farooq"))[0] == "different"
assert nz.compare_names(key("luke purdon"), key("mia purdon"))[0] == "different"          # a different first name is not a typo
assert nz.compare_names(key("ali"), key("alo"))[0] == "different"                          # short names get no extra typos
assert nz.lenient_limit(3) == 0 and nz.lenient_limit(5) == 1 and nz.lenient_limit(8) == 2 and nz.lenient_limit(12) == 3
print("PASSED")

print("TEST: classification - 'similar' makes a Maybe only next to a strong match, a birthday or two supports; never High; vetoes stay")
E = lambda **kw: {k: (v, "") for k, v in kw.items()}
table = [
    (E(name="similar", phone="exact"), "maybe"),
    (E(name="similar", email="exact", address="different"), "maybe"),
    (E(name="similar", dob="exact"), "maybe"),
    (E(name="similar", address="exact", company="exact"), "maybe"),
    (E(name="similar"), None),                                      # a similar name alone is nothing
    (E(name="similar", address="exact"), None),
    (E(name="similar", phone="exact", id="different"), None),       # the different-id veto stays
    (E(name="similar", phone="exact", email="different"), None),
    (E(name="similar", phone="exact", dob="different"), None),
    (E(name="similar", phone="exact", email="exact", id="exact"), "maybe"),   # even three strong matches: the name keeps it at Maybe
]
for ev, expected in table:
    assert classify(ev, "normal") == expected, (ev, expected, classify(ev, "normal"))
    assert classify(ev, "loose") != "high" and classify(ev, "strict") is None
print("PASSED")

print("TEST: through the finder - a Maybe next to a shared phone; nothing without it")
rows = pd.DataFrame({
    "Name": ["Isabella Lodderman", "Isablela Loddermen", "Alexander Richardson", "Alexandar Richardsen", "Christopher Thompson", "Christofer Thompsen",
             "Jonathan Livingston", "Jonathon Livingstone"],
    "Phone": ["0300-1111111", "03001111111", "0300-2222222", "0300-3333333", "0300-4444444", "03004444444", "0300-5555555", "0300-6666666"],
    "Email": ["", "", "", "", "ct@x.com", "other@y.com", "", ""],
})
result = find_near_duplicates(rows, DedupeOptions(locale="PK"))
print("  ", result.summary, [(g.level, g.rows) for g in result.groups])
assert [(g.level, g.rows) for g in result.groups] == [("maybe", [0, 1])]
assert "name similar" in next(iter(result.groups[0].reasons.values()))
print("PASSED")
