from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""dedupe/normalize.py: the normalisers and the explainable name comparison."""
import sys

sys.path.insert(0, str(ROOT))

import pandas as pd

from dedupe import normalize as nz

V = nz.variant_map(nz.load_variant_groups())


def cmp(a, b):
    return nz.compare_names(nz.name_key(a, V), nz.name_key(b, V))


print("TEST: norm_text ignores case, accents, punctuation and spaces; blanks are empty")
assert nz.norm_text("Ali  KHAN.") == nz.norm_text("ali khan") == "ali khan"
assert nz.norm_text("José  O'Brien-Smith") == "jose o brien smith" and nz.norm_text(None) == "" and nz.norm_text(float("nan")) == ""
assert nz.norm_text("  ") == "" and nz.norm_text("علي  خان") == "علي خان"
print("PASSED")

print("TEST: edit distance counts a swap of two neighbouring letters as one edit and stops early")
assert nz.osa_distance("khan", "khna", 2) == 1 and nz.osa_distance("khan", "kahn", 2) == 1 and nz.osa_distance("khan", "khan", 2) == 0
assert nz.osa_distance("khan", "khon", 2) == 1 and nz.osa_distance("abcdef", "uvwxyz", 1) == 2 and nz.osa_distance("a", "abcdef", 1) == 2
assert [nz.typo_limit(n) for n in (2, 3, 4, 7, 8, 12)] == [0, 0, 1, 1, 2, 2]
print("PASSED")

print("TEST: names - exact, case/space/punctuation, comma order, variants, typo, initial, word order, middle name")
assert cmp("Ali Khan", "ali  khan.") == ("exact", "")
assert cmp("Khan, Ali", "Ali Khan")[0] == "exact" and cmp("Dr. Ali Khan", "Ali Khan")[0] == "exact"
assert cmp("Muhammad Khan", "Mohammad Khan") == ("almost", "spelling variant")
assert cmp("Mohamed Al Rashid", "Muhammed Al Rashid")[0] == "almost" and cmp("Fatima Noor", "Fatimah Noor")[0] == "almost"
assert cmp("Yousef Khalid", "Yusuf Khaled")[0] == "almost" and cmp("Abdullah Omar", "Abdallah Umar")[0] == "almost"
assert cmp("Ali Khan", "Ali Khna")[0] == "almost" and "typo" in cmp("Ali Khan", "Ali Khna")[1]
assert cmp("M. Khan", "Muhammad Khan")[0] == "almost" and "initial" in cmp("M. Khan", "Muhammad Khan")[1]
assert cmp("Khan Ali", "Ali Khan") == ("almost", "words in a different order")
assert cmp("Ali Raza Khan", "Ali Khan") == ("almost", "a middle name is missing")
assert cmp("Bob Smith", "Robert Smith")[0] == "almost" and cmp("Liz Taylor", "Elizabeth Taylor")[0] == "almost"
print("PASSED")

print("TEST: different people stay different - other first name, other surname, two typos, short words, blanks")
for a, b in (("Ali Khan", "Sara Khan"), ("Ali Khan", "Ali Raza"), ("Ali Khan", "Alia Khan"), ("Ali Khan", "Ala Khin"), ("Ahmed Ali", "Ahmed Raza"),
             ("M. Khan", "Sara Khan"), ("Ali", "Ala"), ("Ali Khan", "Khan")):
    assert cmp(a, b)[0] == "different", (a, b, cmp(a, b))
assert cmp("Ali Khan", "")[0] == "blank" and cmp(None, "Ali Khan")[0] == "blank"
assert cmp("Ali", "Ali")[0] == "exact"
assert cmp("Amir Shah", "Amar Shah")[0] == "almost", "one letter in a four-letter word is a typo as far as names go - never enough on its own"
print("PASSED")

print("TEST: name variants - the list is editable (text round trip) and can be extended")
text = nz.variants_to_text(nz.load_variant_groups())
assert "muhammad, mohammad, mohammed, mohamed, muhammed" in text and "abdullah, abdallah" in text and "yousef, yusuf, yousuf" in text
assert "khalid, khaled" in text and "fatima, fatimah" in text and "bob" in text
assert nz.parse_variants_text("Foo, Fu\nsingle\n\nBar, Baz, Bazz") == [["foo", "fu"], ["bar", "baz", "bazz"]]
custom = nz.variant_map(nz.parse_variants_text("nadeem, nadim"))
assert nz.compare_names(nz.name_key("Nadim Shah", custom), nz.name_key("Nadeem Shah", custom)) == ("almost", "spelling variant")
print("PASSED")

print("TEST: block keys let spelling variants, typos, swaps and initials meet")
def keys(name):
    return set(nz.name_block_keys(nz.name_key(name, V)))
assert keys("Muhammad Khan") & keys("Mohammad Khan") and keys("Ali Khan") & keys("Ali Khna") and keys("Ali Khan") & keys("Khan Ali")
assert keys("M. Khan") & keys("Muhammad Khan") and not (keys("Ali Khan") & keys("Sara Raza")) and nz.name_block_keys(nz.name_key("", V)) == []
print("PASSED")

print("TEST: phones - every common way of writing a number gives the same key (with the locale's country)")
a, b, c, d = (nz.norm_phone(x, "PK") for x in ("0300-1234567", "+92 300 1234567", "(0300) 123 4567", 3001234567))
assert a[0] == b[0] == c[0] == d[0] == "+923001234567" and nz.phones_match(a, b)
assert nz.norm_phone(3001234567.0, "PK")[0] == "+923001234567", "an Excel number that lost its leading zero"
assert nz.norm_phone("050 123 4567", "AE")[0] == "+971501234567" and nz.norm_phone("+971 50 123 4567", "AE")[0] == "+971501234567"
assert nz.norm_phone("0300-7654321", "PK")[0] != a[0] and not nz.phones_match(a, nz.norm_phone("0300-7654321", "PK"))
assert nz.norm_phone("12", "PK") == ("", "") and nz.norm_phone(None) == ("", "") and nz.norm_phone("n/a") == ("", "")
assert nz.phones_match(nz.norm_phone("300 1234567", "US"), nz.norm_phone("+92 300 1234567", "PK")), "same last nine digits"
print("PASSED")

print("TEST: emails (lower case, trimmed, invalid ones ignored, the optional Gmail rule), ids, companies, addresses")
assert nz.norm_email(" Ali@Gmail.com ") == "ali@gmail.com" and nz.norm_email("not an email") == "" and nz.norm_email(None) == ""
assert nz.norm_email("A.li+news@Gmail.com") == "a.li+news@gmail.com" and nz.norm_email("A.li+news@Gmail.com", gmail_rule=True) == "ali@gmail.com"
assert nz.norm_email("a.b@work.com", gmail_rule=True) == "a.b@work.com"
assert nz.norm_id("35202-1234567-1") == "3520212345671" and nz.norm_id(1234.0) == "1234" and nz.norm_id("ab 12") == "AB12"
assert nz.norm_company("Acme Ltd") == nz.norm_company("ACME Limited") == "acme"
assert nz.norm_address("12 Main Street, Flat 3") == nz.norm_address("flat 3, 12 main st.")
assert nz.compare_sets(nz.norm_address("12 Main Street"), nz.norm_address("12 main st")) == "exact"
assert nz.compare_sets(nz.norm_address("12 Main Street Dubai"), nz.norm_address("14 Other Road Dubai")) == "different"
assert nz.compare_sets((), ("a",)) == "blank" and nz.compare_texts("acme", "acmee") == "almost" and nz.compare_texts("acme", "zeta") == "different"
print("PASSED")
