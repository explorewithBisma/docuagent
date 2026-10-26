from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Upload and go: summary card, Please-check list, one-click AI and payment switch, build in one click (AppTest)."""
import io
import json
import os
import sys
from datetime import date
from unittest import mock

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import openpyxl
import streamlit as st
from streamlit.testing.v1 import AppTest

import ai_rules
from budget_fixtures import personal_style_bytes

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DATA = personal_style_bytes()
_original = st.download_button
captured = {}
prompts = []


def spy(label, data=None, file_name=None, mime=None, key=None, **kwargs):
    captured[label] = {"data": data, "file_name": file_name}
    return _original(label, data=data, file_name=file_name, mime=mime, key=key, **kwargs)


def run(at):
    with mock.patch.object(st, "download_button", spy):
        at.run()
    assert not at.exception, [e.value for e in at.exception]


def new_app(env=None):
    at = AppTest.from_file(str(ROOT / "budget_app.py"), default_timeout=240)
    at.run()
    at.file_uploader[0].upload("tracker.xlsx", DATA, XLSX)
    run(at)
    return at


def clean_env(**extra):
    base = {k: v for k, v in os.environ.items() if k not in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "AI_PROVIDER")}
    return mock.patch.dict(os.environ, {**base, **extra}, clear=True)


def button(at, key):
    return next(b for b in at.button if b.key == key)


def card_text(at):
    return " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption)


def workbook(at):
    return openpyxl.load_workbook(io.BytesIO(captured["Download budget workbook (.xlsx)"]["data"]))


def transactions(book):
    ws = book["Transactions"]
    return [[c.value for c in ws[r]] for r in range(2, 1002) if any(c.value is not None for c in ws[r])]


with clean_env():
    print("TEST: the summary card shows what was found, with source and confidence, the sanity check and the Please-check list")
    at = new_app()
    assert any(h.value == "What I found" for h in at.header)
    text = card_text(at)
    for expected in ("**Currency**", "AED", "header &#39;Amount (AED)&#39;".replace("&#39;", "'"), "June 2026", "11,236.00",
                     "AED 7,225.00", "8 rows, AED 2,105.50", "Loan / card payments", "🟢 sure"):
        assert expected in text, expected
    assert "Sanity check: your file's own estimated closing balance is 1,463.29" in text and "15,355.00" in text
    assert "Please check" in text and "recorded as Loan payment" in text and "Card bill payment = paying your credit card bill" in text
    assert {b.label for b in at.button} >= {"Looks good, build my sheet", "Edit details", "Switch to Card bill payment",
                                              "Use AI to suggest categories"}
    print("PASSED")

    print("TEST: one obvious AI click on the card - with no API key it is disabled and says why (no error)")
    ai_button = button(at, "bd_use_ai")
    assert ai_button.proto.disabled is True
    assert any("2 description(s) (AED 63.50 total) couldn't be categorised" in w.value and "Only the description text is sent" in w.value
               for w in at.warning), [w.value for w in at.warning]
    assert any("No API key is set" in c.value for c in at.caption)
    print("PASSED")

    print("TEST: 'Looks good, build my sheet' builds in one click with everything detected")
    captured.clear()
    button(at, "bd_looks_good").click()
    run(at)
    book = workbook(at)
    assert captured["Download budget workbook (.xlsx)"]["file_name"] == "Budget_2026-06.xlsx"
    settings = book["Settings"]
    assert settings["B3"].value.date() == date(2026, 6, 1) and settings["B5"].value == 11236 and settings["B6"].value == "AED"
    assert book["Transactions"]["E2"].number_format == '"AED" #,##0.00'
    categories = {settings.cell(row=3 + i, column=7).value: settings.cell(row=3 + i, column=8).value for i in range(30)}
    assert categories["Housing"] == 2000 and categories["Food"] == 1000 and categories["Health"] == 500 and categories["Transport"] == 0
    rows = transactions(book)
    kinds = [r[2] for r in rows]
    assert kinds.count("Expense") == 8 and kinds.count("Income") == 1 and kinds.count("Loan payment") == 2 and len(rows) == 11
    by_description = {r[1]: r for r in rows}
    assert by_description["Grocery"][3] == "Food" and by_description["Primary Salary"][4] == 7225
    notes = book["Import Notes"]
    text = " ".join(str(c.value) for row in notes.iter_rows() for c in row if c.value is not None)
    assert "Merged your categories into ours: Food/Grocery → Food" in text and "Sanity check" in text and "category guessed" in text
    print("PASSED")

    print("TEST: switching the loan-log payments to Card bill payment (one click) changes the types and the file")
    at = new_app()
    button(at, "bd_switch_payments_to_card").click()
    run(at)
    assert any("Payments switched." in s.value for s in at.success)
    assert any("are recorded as Card bill payment" in m.value for m in at.markdown)
    assert button(at, "bd_switch_payments_to_loan").label == "Switch back to Loan payment"
    captured.clear()
    button(at, "bd_looks_good").click()
    run(at)
    kinds = [r[2] for r in transactions(workbook(at))]
    assert kinds.count("Card bill payment") == 2 and kinds.count("Loan payment") == 0
    print("PASSED")

    print("TEST: 'Edit details' opens the existing sections (nothing is hidden)")
    at = new_app()
    assert next(e for e in at.expander if e.label.startswith("Edit details")).proto.expanded is False
    button(at, "bd_edit_details").click()
    run(at)
    assert next(e for e in at.expander if e.label.startswith("Edit details")).proto.expanded is True
    assert next(n for n in at.number_input if n.label == "Opening balance").value == 11236
    assert next(s for s in at.selectbox if s.label == "Currency").value == "AED"
    assert any(h.value == "4. Review your transactions" for h in at.header)
    print("PASSED")

    print("TEST: edits in the opened sections win over the detected values when building")
    next(n for n in at.number_input if n.label == "Opening balance").set_value(500.0)
    run(at)
    captured.clear()
    button(at, "bd_looks_good").click()
    run(at)
    assert workbook(at)["Settings"]["B5"].value == 500
    print("PASSED")

    print("TEST: the summary card refreshes after edits in 'Edit details' (opening balance, currency, month)")
    at = new_app()
    assert "AED 11,236.00" in card_text(at)
    button(at, "bd_edit_details").click()
    run(at)
    next(n for n in at.number_input if n.label == "Opening balance").set_value(500.0)
    run(at)
    text = card_text(at)
    assert "AED 500.00" in text and "AED 11,236.00" not in text and "from edited by you" in text
    assert "the one calculated here is 4,619.00" in text, "the sanity check follows: 500 + 7,225 - 2,105.50 - 1,000.50"
    assert not any("Opening balance" in m.value and "Account Balance" in m.value for m in at.markdown)
    next(s for s in at.selectbox if s.label == "Currency").select("$")
    run(at)
    text = card_text(at)
    assert "$ 500.00" in text and "$ 7,225.00" in text and "AED 500.00" not in text
    next(s for s in at.selectbox if s.label == "Month").select(7)
    run(at)
    assert "July 2026" in card_text(at) and "June 2026" not in card_text(at).split("Please check")[0]
    print("PASSED")

    print("TEST: 'Let me choose the columns myself' switches to the manual column picker")
    at = new_app()
    next(c for c in at.checkbox if c.label == "Let me choose the columns myself").check()
    run(at)
    assert not any(h.value == "What I found" for h in at.header) and any(e.label.startswith("Columns found") for e in at.expander)
    print("PASSED")

AI_ANSWER = json.dumps({"categories": [{"description": "malabar", "category": "Food", "confidence": "medium"},
                                        {"description": "Aster", "category": "Health", "confidence": "high"}]})


def fake_groq(prompt):
    prompts.append(prompt)
    return AI_ANSWER


with clean_env(GROQ_API_KEY="gsk_fake", AI_PROVIDER="groq"), mock.patch.object(ai_rules, "_call_groq", fake_groq):
    print("TEST: with a key, one click asks the AI - only descriptions are sent - rows are marked 'AI suggested' everywhere")
    at = new_app()
    assert button(at, "bd_use_ai").proto.disabled is False
    button(at, "bd_use_ai").click()
    run(at)
    assert len(prompts) == 1
    sent = prompts[0]
    assert '"malabar"' in sent and '"Aster"' in sent
    listed = sent.split("Descriptions:")[1].split("Respond with")[0]
    assert json.loads(listed.strip()) == ["Aster", "malabar"] or json.loads(listed.strip()) == ["malabar", "Aster"], listed
    assert not any(token in sent for token in ("23.5", "2026-06", "Debit", "7225", "11236"))
    assert any("AI suggested a category for 2 row(s)" in s.value for s in at.success)
    assert not any("couldn't be categorised" in w.value for w in at.warning)
    assert any("(AI suggested (medium))" in m.value or "AI suggested (medium)" in m.value for m in at.markdown)
    captured.clear()
    button(at, "bd_looks_good").click()
    run(at)
    book = workbook(at)
    by_description = {r[1]: r for r in transactions(book)}
    assert by_description["malabar"][3] == "Food" and by_description["Aster"][3] == "Health"
    notes = book["Import Notes"]
    text = " ".join(str(c.value) for row in notes.iter_rows() for c in row if c.value is not None)
    assert "AI suggested (medium)" in text and "AI suggested (high)" not in text
    assert "2 categories were suggested by AI (groq" in text and "only the description texts were sent" in text
    print("PASSED")

    print("TEST: an AI failure is shown as a message, the page keeps working")
    def failing(prompt):
        raise ai_rules.AISuggestionError("Groq's rate limit was reached. Wait a minute and try again.")
    with mock.patch.object(ai_rules, "_call_groq", failing):
        at = new_app()
        button(at, "bd_use_ai").click()
        run(at)
        assert any("rate limit" in e.value for e in at.error)
        assert any(h.value == "What I found" for h in at.header)
    print("PASSED")
