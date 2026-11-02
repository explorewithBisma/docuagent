from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Statement refinements: savings transfers are Saving, currency hints and the one-click currency choice, UAE / Pakistan merchants, bank prefixes."""
import io
import os
import sys
from unittest import mock

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import openpyxl
import streamlit as st
from streamlit.testing.v1 import AppTest

import statement_fixtures as sf
from budget import autopilot, categorize, detect, statement as statement_reader
from budget.model import BudgetConfig
from budget.workbook import build_workbook

KM = categorize.default_keyword_map()
d = sf.day


def analyse(rows, opening=10000.0, title=None):
    return autopilot.analyze(sf.raw_statement(rows, opening, title), is_csv=True)


def by_description(analysis):
    return {r["Description"]: r for _, r in analysis.rows.iterrows()}


print("TEST: transfers to the user's own savings / deposit / investment account are Saving; card bills and other own-account moves stay Card bill payment")
A = analyse([
    (d(1), "TRANSFER TO OWN ACCOUNT SAVINGS", -1000.0), (d(2), "Transfer to savings account", -500.0), (d(3), "Transfer to Fixed Deposit", -2000.0),
    (d(4), "INTERNAL TRANSFER INVESTMENT ACCOUNT", -750.0), (d(5), "Transfer to own account", -300.0), (d(6), "Credit card payment VISA", -1200.0),
    (d(7), "Transfer to deposit account", -400.0), (d(8), "SALARY", 9000.0),
    (d(9), "Own account transfer from savings", 600.0), (d(10), "Savings account interest", 12.5)])
rows = by_description(A)
for description, expected in {"TRANSFER TO OWN ACCOUNT SAVINGS": "Saving", "Transfer to savings account": "Saving", "Transfer to Fixed Deposit": "Saving",
                              "INTERNAL TRANSFER INVESTMENT ACCOUNT": "Saving", "Transfer to deposit account": "Saving", "Transfer to own account": "Card bill payment",
                              "Credit card payment VISA": "Card bill payment"}.items():
    assert rows[description]["Type"] == expected, (description, rows[description]["Type"])
print("PASSED")

print("TEST: money coming back FROM savings is not income and not spending (Card bill payment); the interest the savings account pays is income")
assert rows["Own account transfer from savings"]["Type"] == "Card bill payment" and "money in from savings" in rows["Own account transfer from savings"]["Flags"]
assert rows["Savings account interest"]["Type"] == "Income" and rows["SALARY"]["Type"] == "Income"
assert A.statement is not None and abs(A.statement_check["difference"]) < 0.005                       # the balance still adds up with Saving rows counted
print("PASSED")

print("TEST: currency from strong country hints (medium), never from one weak hint; the winner must clearly beat the other country")
uae = analyse([(d(1), "DEWA bill", -300.0), (d(2), "Etisalat mobile", -150.0), (d(3), "ADCB annual fee", -50.0), (d(4), "Salary", 9000.0)])
pak = analyse([(d(1), "K-Electric bill", -9000.0), (d(2), "LESCO", -4000.0), (d(3), "HBL ATM withdrawal", -5000.0), (d(4), "Salary", 90000.0)])
print("  ", uae.currency.value, uae.currency.confidence, "|", uae.currency.source, "||", pak.currency.value, pak.currency.confidence, "|", pak.currency.source)
assert (uae.currency.value, uae.currency.confidence) == ("AED", "medium") and "DEWA" in uae.currency.source
assert (pak.currency.value, pak.currency.confidence) == ("Rs", "medium") and "Pakistan" in pak.currency.source
weak = analyse([(d(1), "Etisalat mobile", -150.0), (d(2), "Coffee shop", -20.0), (d(3), "Salary", 9000.0)])
assert weak.currency.confidence == "low"
mixed = analyse([(d(1), "DEWA bill", -300.0), (d(2), "Etisalat", -150.0), (d(3), "K-Electric", -90.0), (d(4), "LESCO", -80.0), (d(5), "Salary", 9000.0)])
assert mixed.currency.confidence == "low"                                                                # two countries' names: no guess
header = analyse([(d(1), "K-Electric bill", -9000.0), (d(2), "LESCO", -4000.0), (d(3), "Salary", 90000.0)], title=["Account statement (AED)"])
assert (header.currency.value, header.currency.confidence) == ("AED", "medium") and "title line" in header.currency.source          # an explicit currency beats the merchants
print("PASSED")

print("TEST: a low-confidence currency is a Please-check item with a one-click choice")
item = next(c for c in weak.checks if c.kind == "currency")
assert item.action == "choose_currency" and "Which currency" in item.text
assert not any(c.kind == "currency" for c in uae.checks)                                                  # a medium guess is not a question
choices = autopilot.currency_choices(weak)
assert choices[0] == str(weak.currency.value) and "AED" in choices and len(choices) == len(set(choices)) <= 6
chosen = autopilot.set_currency(weak, "AED")
assert (chosen.currency.value, chosen.currency.confidence, chosen.currency.source) == ("AED", "high", "chosen by you") and not any(c.kind == "currency" for c in chosen.checks)
env = {k: v for k, v in os.environ.items() if k not in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "AI_PROVIDER")}
with mock.patch.dict(os.environ, env, clear=True):
    at = AppTest.from_file(str(ROOT / "budget_app.py"), default_timeout=240)
    at.run()
    at.file_uploader[0].upload("weak.csv", sf.raw_statement([(d(1), "Etisalat mobile", -150.0), (d(2), "Coffee shop", -20.0), (d(3), "Salary", 9000.0)]), "text/csv")
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    buttons = {b.key: b for b in at.button}
    assert "bd_pick_currency_AED" in buttons and "Which currency is this account in?" in " ".join(m.value for m in at.markdown)
    buttons["bd_pick_currency_AED"].click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    text = " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption)
    assert "from chosen by you" in text and "edited by you" not in text.split("**Month**")[0] and any(s.value == "Currency set to AED." for s in at.success) and not any(b.key.startswith("bd_pick_currency_") for b in at.button)
print("PASSED")

print("TEST: UAE and Pakistan merchants are categorised (longest keyword wins: 'metro cash' is Food, 'noon food' is Food, plain 'noon' is Shopping)")
expected = {
    "Nesto Hypermarket": "Food", "Lulu Hypermarket": "Food", "Spinneys": "Food", "Union Coop": "Food", "Viva supermarket": "Food", "Grandiose": "Food", "Talabat order": "Food",
    "Deliveroo": "Food", "Noon Food": "Food", "Careem Food": "Food", "Foodpanda": "Food", "KFC": "Food", "McDonald's": "Food", "McDonalds": "Food", "Imtiaz Super Market": "Food",
    "Chase Up": "Food", "Al-Fatah": "Food", "Metro Cash & Carry": "Food",
    "DEWA": "Bills", "SEWA": "Bills", "ADDC": "Bills", "FEWA": "Bills", "Etisalat": "Bills", "du": "Bills", "K-Electric": "Bills", "LESCO": "Bills", "IESCO": "Bills", "MEPCO": "Bills",
    "Sui Gas": "Bills", "SNGPL": "Bills", "PTCL": "Bills", "Jazz": "Bills", "Zong": "Bills", "Telenor": "Bills", "Ufone": "Bills", "Nayatel": "Bills",
    "Careem ride": "Transport", "Uber": "Transport", "RTA": "Transport", "Salik": "Transport", "NOL card": "Transport", "Bykea": "Transport", "inDrive": "Transport", "PSO": "Transport",
    "Shell": "Transport", "ADNOC": "Transport", "ENOC": "Transport",
    "Noon": "Shopping", "Amazon": "Shopping", "Namshi": "Shopping", "Daraz": "Shopping", "Sharaf DG": "Shopping", "Centrepoint": "Shopping", "Max Fashion": "Shopping",
    "Khaadi": "Shopping", "Gul Ahmed": "Shopping",
    "Account fee": "Bank & fees", "Service charge": "Bank & fees", "Annual fee": "Bank & fees", "VAT on fee": "Bank & fees",
}
wrong = {k: (categorize.match_category(k, KM), v) for k, v in expected.items() if categorize.match_category(k, KM) != v}
print("   wrong:", wrong)
assert not wrong
assert "Bank & fees" in KM["categories"] and "Bank & fees" in autopilot.analyze(sf.statement_bytes("signed"), is_csv=True).categories
assert categorize.match_category("duty free", KM) is None and categorize.match_category("module", KM) is None          # 'du' is a whole word only
print("PASSED")

print("TEST: bank prefixes (POS, ECOM, ATM, CHQ, IBFT) are stripped before matching, but only as prefixes")
assert categorize.strip_prefix("POS 4455 CARREFOUR") == "CARREFOUR" and categorize.strip_prefix("ECOM-AMAZON AE") == "AMAZON AE"
assert categorize.strip_prefix("ATM 0011 WITHDRAWAL") == "WITHDRAWAL" and categorize.strip_prefix("CHQ 000123") == "CHQ 000123"          # nothing left after the prefix: the text is kept as it is
assert categorize.strip_prefix("POSITIVE Vibes Cafe") == "POSITIVE Vibes Cafe" and categorize.strip_prefix("Atmosphere Cafe") == "Atmosphere Cafe"
assert categorize.match_category("POS 7788 SPINNEYS DUBAI", KM) == "Food" and categorize.match_category("ECOM NAMSHI", KM) == "Shopping"
print("PASSED")

print("TEST: payment method from the statement's prefix")
for text, method in {"POS 4455 CARREFOUR": "Card", "ECOM AMAZON AE": "Card", "ATM WITHDRAWAL DUBAI MALL": "Cash withdrawal", "CHQ 000123 RENT": "Cheque", "IBFT TO ALI KHAN": "Bank transfer",
                     "Transfer to own account": "Bank transfer", "Careem ride": "", "ATMOSPHERE CAFE": ""}.items():
    assert statement_reader.payment_method(text) == method, (text, statement_reader.payment_method(text))
P = analyse([(d(1), "POS 4455 CARREFOUR HYPERMARKET", -250.0), (d(2), "ECOM AMAZON AE", -120.0), (d(3), "ATM WITHDRAWAL", -500.0), (d(4), "CHQ 000123 RENT", -2000.0),
             (d(5), "IBFT TRANSFER ALI KHAN", -300.0), (d(6), "Careem ride", -30.0), (d(7), "Salary", 9000.0)])
methods = {r["Description"]: r["Payment method"] for _, r in P.rows.iterrows()}
print("  ", methods)
assert methods["POS 4455 CARREFOUR HYPERMARKET"] == "Card" and methods["ECOM AMAZON AE"] == "Card" and methods["ATM WITHDRAWAL"] == "Cash withdrawal"
assert methods["CHQ 000123 RENT"] == "Cheque" and methods["IBFT TRANSFER ALI KHAN"] == "Bank transfer" 
assert methods["Careem ride"] is None or methods["Careem ride"] != methods["Careem ride"]               # no prefix, no guess (None / NaN)
categories = dict(zip(P.rows["Description"], P.rows["Category"]))
assert categories["POS 4455 CARREFOUR HYPERMARKET"] == "Food" and categories["ECOM AMAZON AE"] == "Shopping" and categories["CHQ 000123 RENT"] == "Housing"
assert {"Cash withdrawal", "Cheque", "Card", "Bank transfer"} <= set(P.config.payment_methods)
book = openpyxl.load_workbook(io.BytesIO(build_workbook(P.config, KM, P.rows, autopilot.import_info(P, P.rows))))
settings = " ".join(str(c.value) for row in book["Settings"].iter_rows() for c in row if c.value is not None)
assert "Cash withdrawal" in settings and "Cheque" in settings
print("PASSED")
