"""
dedupe/normalize.py
--------------------
Turns messy values into comparable ones, and compares two names the way a person would:

  norm_text      "Ali  KHAN." / "ali khan"            -> "ali khan"        (case, accents, punctuation, spaces)
  name_key       "Khan, Mohammad" / "Dr. M. Khan"     -> tokens, with spelling variants treated as one name
  compare_names  -> ("exact" | "almost" | "different" | "blank", why)
  norm_phone     "0300-1234567" / "+92 300 1234567"   -> the same E.164 number (with the locale's country)
  norm_email     "Ali@Gmail.com "                     -> "ali@gmail.com"
  norm_id, norm_company, norm_address

Nothing here guesses: a value that cannot be understood is simply left as plain normalised text.
"""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import phonenumbers

from locales import LOCALES
from quality import is_blank

VARIANTS_PATH = Path(__file__).with_name("name_variants.json")
TITLES = {"mr", "mrs", "ms", "miss", "dr", "prof", "engr"}
COMPANY_SUFFIXES = {"ltd", "limited", "llc", "inc", "co", "corp", "corporation", "company", "pvt", "private", "fze", "fzc", "llp", "plc",
                    "gmbh", "sa", "pty", "the"}
ADDRESS_WORDS = {"street": "st", "str": "st", "road": "rd", "avenue": "ave", "av": "ave", "apartment": "apt", "flat": "apt", "floor": "fl",
                 "building": "bldg", "block": "blk", "boulevard": "blvd", "lane": "ln", "house": "hse", "suite": "ste", "po": "pobox"}
_VOWELS = set("aeiouy")


# ---------------------------------------------------------------------------------------------------------------
# TEXT
# ---------------------------------------------------------------------------------------------------------------
def norm_text(value) -> str:
    """Lower case, accents removed, every run of punctuation / spaces becomes one space."""
    if is_blank(value):
        return ""
    text = unicodedata.normalize("NFKD", str(value).casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", " ", text).strip()


def osa_distance(a: str, b: str, limit: int) -> int:
    """Edit distance counting a swap of two neighbouring letters as ONE edit; stops early (returns limit + 1) when it exceeds `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous2, previous = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if previous2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                current[j] = min(current[j], previous2[j - 2] + 1)
        if min(current) > limit:
            return limit + 1
        previous2, previous = previous, current
    return previous[-1]


def typo_limit(length: int) -> int:
    """How many letter edits still count as a typo of a word this long."""
    return 0 if length <= 3 else 1 if length <= 7 else 2


def skeleton(token: str) -> str:
    """A spelling-tolerant key: the first letter, then the consonants with repeats collapsed (muhammad, mohammad, mohamed -> mhmd)."""
    if not token:
        return ""
    out = [token[0]]
    for ch in token[1:]:
        if ch not in _VOWELS and ch.isalpha() and ch != out[-1]:
            out.append(ch)
        elif not ch.isalpha() and ch != out[-1]:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------------------------------------------------------
# NAMES
# ---------------------------------------------------------------------------------------------------------------
def load_variant_groups() -> list:
    return json.loads(VARIANTS_PATH.read_text(encoding="utf-8"))["groups"]


def variants_to_text(groups) -> str:
    """One group per line, comma separated - for the editable text box."""
    return "\n".join(", ".join(group) for group in groups)


def parse_variants_text(text: str) -> list:
    groups = []
    for line in str(text).splitlines():
        words = [norm_text(w) for w in line.split(",")]
        words = [w for w in words if w]
        if len(words) >= 2:
            groups.append(words)
    return groups


def variant_map(groups) -> dict:
    """{spelling: the group's first spelling}."""
    mapping = {}
    for group in groups:
        for word in group:
            mapping.setdefault(norm_text(word), norm_text(group[0]))
    return mapping


@dataclass
class NameKey:
    text: str                                   # normalised, in the order written (after "Khan, Ali" -> "ali khan")
    tokens: list = field(default_factory=list)  # tokens with spelling variants merged into one
    raw_tokens: list = field(default_factory=list)


def name_key(value, variants: dict) -> NameKey:
    if is_blank(value):
        return NameKey("")
    raw = str(value)
    if raw.count(",") == 1:                                       # "Khan, Ali" -> "Ali Khan"
        last, first = raw.split(",")
        raw = f"{first} {last}"
    raw_tokens = [t for t in norm_text(raw).split() if t not in TITLES]
    return NameKey(" ".join(raw_tokens), [variants.get(t, t) for t in raw_tokens], raw_tokens)


def _token_pair(a: str, b: str):
    """How two name words relate: 'same', 'initial', 'typo' or None."""
    if a == b:
        return "same", ""
    if len(a) == 1 or len(b) == 1:
        if a[0] == b[0]:
            return "initial", f"'{a}' for '{b}'" if len(a) == 1 else f"'{b}' for '{a}'"
        return None, ""
    if min(len(a), len(b)) >= 4 and osa_distance(a, b, typo_limit(min(len(a), len(b)))) <= typo_limit(min(len(a), len(b))):
        return "typo", f"'{a}' / '{b}'"
    return None, ""


def compare_names(a: NameKey, b: NameKey) -> tuple:
    """-> (verdict, why): 'exact' (the same once case, spaces and punctuation are ignored), 'almost' (spelling variant, typo, initial,
    word order, a missing middle name), 'different', or 'blank' when either name is missing."""
    if not a.tokens or not b.tokens:
        return "blank", ""
    if a.text == b.text:
        return "exact", ""
    if a.tokens == b.tokens:
        return "almost", "spelling variant"
    if sorted(a.tokens) == sorted(b.tokens):
        return "almost", "words in a different order"
    short, long = (a.tokens, b.tokens) if len(a.tokens) <= len(b.tokens) else (b.tokens, a.tokens)
    if len(short) == 1 and len(long) == 1:
        relation, why = _token_pair(short[0], long[0])
        return ("almost", f"typo {why}") if relation == "typo" else ("different", "")
    if len(short) < 2 or len(long) - len(short) > 1:
        return "different", ""
    aligned = list(zip(short, long)) if len(short) == len(long) else [(short[0], long[0]), (short[-1], long[-1])]
    whys, odd = [], 0
    for x, y in aligned:
        relation, why = _token_pair(x, y)
        if relation is None:
            return "different", ""
        if relation != "same":
            odd += 1
            whys.append(("initial " if relation == "initial" else "typo ") + why)
    if odd > (1 if len(aligned) == 2 else 2):
        return "different", ""
    if len(short) != len(long):
        whys.append("a middle name is missing")
    return "almost", ", ".join(whys) or "a middle name is missing"


def name_block_keys(key: NameKey) -> list:
    """Keys under which rows meet to be compared: spelling-tolerant, order-tolerant, and initial-tolerant."""
    tokens = key.tokens
    if not tokens:
        return []
    if len(tokens) == 1:
        return [("n1", skeleton(tokens[0]))]
    first, last = skeleton(tokens[0]), skeleton(tokens[-1])
    return [("n2", f"{first}|{last}"), ("n2", "|".join(sorted((first, last)))), ("n2i", f"{tokens[0][0]}|{last}"),
            ("n2i", f"{tokens[-1][0]}|{first}")]


# ---------------------------------------------------------------------------------------------------------------
# PHONES, EMAILS, IDS, COMPANIES, ADDRESSES
# ---------------------------------------------------------------------------------------------------------------
def _digits(value) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\D", "", str(value))


@lru_cache(maxsize=200_000)
def norm_phone(value, region: str = "PK") -> tuple:
    """-> (E.164 text or '', last 9 digits or ''). The E.164 form needs the locale's country; the last nine digits still meet a number
    written without it."""
    if is_blank(value):
        return "", ""
    text = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value).strip()
    digits = _digits(text)
    if len(digits) < 7:
        return "", ""
    e164 = ""
    try:
        parsed = phonenumbers.parse(text, LOCALES.get(region, LOCALES["PK"])["phone_region"])
        if phonenumbers.is_possible_number(parsed):
            e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    return e164, digits[-9:]


def phones_match(a: tuple, b: tuple) -> bool:
    return bool((a[0] and a[0] == b[0]) or (a[1] and a[1] == b[1]))


def norm_email(value, gmail_rule: bool = False) -> str:
    if is_blank(value):
        return ""
    text = str(value).strip().lower()
    if "@" not in text or " " in text:
        return ""
    if gmail_rule:
        local, _, domain = text.partition("@")
        if domain in ("gmail.com", "googlemail.com"):
            text = local.split("+")[0].replace(".", "") + "@gmail.com"
    return text


def norm_id(value) -> str:
    if is_blank(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"[^0-9A-Za-z]", "", str(value)).upper()


def norm_company(value) -> str:
    words = [w for w in norm_text(value).split() if w not in COMPANY_SUFFIXES]
    return " ".join(words)


def norm_address(value) -> tuple:
    """Address words with the usual abbreviations unified, as a sorted tuple (order of words does not matter)."""
    words = [ADDRESS_WORDS.get(w, w) for w in norm_text(value).split()]
    return tuple(sorted(set(words)))


def compare_sets(a: tuple, b: tuple, near: float = 0.75) -> str:
    """exact / almost / different / blank for two word sets (addresses)."""
    if not a or not b:
        return "blank"
    if a == b:
        return "exact"
    overlap = len(set(a) & set(b)) / len(set(a) | set(b))
    return "almost" if overlap >= near else "different"


def compare_texts(a: str, b: str) -> str:
    """exact / almost / different / blank for two normalised strings (companies)."""
    if not a or not b:
        return "blank"
    if a == b:
        return "exact"
    limit = typo_limit(min(len(a), len(b)))
    return "almost" if limit and osa_distance(a, b, limit) <= limit else "different"
