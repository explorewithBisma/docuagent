from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Build my budget from a bank statement, driven through Streamlit's AppTest: upload, the card, the closing-balance check, one-click build."""
import io
import os
import sys
from datetime import date
from unittest import mock

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import openpyxl
import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

import statement_fixtures as sf
from budget import autopilot

_original = st.download_button
captured = {}


def spy(label, data=None, file_name=None, mime=None, key=None, **kwargs):
    captured[label] = {"data": data, "file_name": file_name}
    return _original(label, data=data, file_name=file_name, mime=mime, key=key, **kwargs)


def run(at):
    with mock.patch.object(st, "download_button", spy):
        at.run()
    assert not at.exception, [e.value for e in at.exception]


def new_app(name, data):
    at = AppTest.from_file(str(ROOT / "budget_app.py"), default_timeout=240)
    at.run()
    at.file_uploader[0].upload(name, data, "text/csv")
    run(at)
    return at


def card_text(at):
    return " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption)


env = {k: v for k, v in os.environ.items() if k not in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "AI_PROVIDER")}
with mock.patch.dict(os.environ, env, clear=True):
    print("TEST: upload a bank statement -> the card names the statement, the balance-based opening (sure) and a passing closing-balance check")
    at = new_app("statement.csv", sf.statement_bytes("debit_credit"))
    text = card_text(at)
    for expected in ("**Bank statement**", "28 transactions", "running balance &#39;Balance&#39;".replace("&#39;", "'"), "5,000.00", "May 2026", "🟢 sure",
                     "Closing balance check", "4,644.35", "difference 0.00", "card bill payment / own-account transfer"):
        assert expected in text, expected
    assert any(c.value.startswith("✔ Closing balance check") for c in at.caption)
    assert not any("Closing balance check" in w.value for w in at.warning)
    assert "IBFT transfer Ali Khan" in text and "Card bill payment" in text                      # the one unclear transfer is in Please check
    print("PASSED")

    print("TEST: 'Looks good, build my sheet' -> a workbook whose Transactions sheet has credits as Income, debits as Expense, card bills and transfers as Card bill payment")
    captured.clear()
    next(b for b in at.button if b.key == "bd_looks_good").click()
    run(at)
    book = openpyxl.load_workbook(io.BytesIO(captured["Download budget workbook (.xlsx)"]["data"]))
    assert captured["Download budget workbook (.xlsx)"]["file_name"] == "Budget_2026-05.xlsx"
    assert book["Settings"]["B5"].value == 5000 and book["Settings"]["B3"].value.date() == date(2026, 5, 1)
    ws = book["Transactions"]
    rows = [[c.value for c in ws[r]] for r in range(2, 1002) if any(c.value is not None for c in ws[r])]
    kinds = [r[2] for r in rows]
    assert len(rows) == 28 and kinds.count("Income") == 3 and kinds.count("Card bill payment") == 4 and kinds.count("Loan payment") == 1 and kinds.count("Expense") == 20
    by_description = {r[1]: r for r in rows}
    assert by_description["Salary May 2026"][2] == "Income" and by_description["Salary May 2026"][4] == 8000
    assert by_description["Transfer to own account - savings"][2] == "Card bill payment" and by_description["Resturant Al Mallah"][3] == "Food"
    notes = " ".join(str(c.value) for row in book["Import Notes"].iter_rows() for c in row if c.value is not None)
    assert "Closing balance check: the statement ends the month at" in notes and "difference 0.00" in notes
    print("PASSED")

    print("TEST: a statement with a missing transaction shows the difference as a warning on the card")
    frame = sf.statement_frame("debit_credit")
    index = next(i for i, r in enumerate(frame) if r[1] == "Cafe breakfast")
    at = new_app("broken.csv", pd.DataFrame(frame[:index] + frame[index + 1:]).to_csv(index=False, header=False).encode())
    assert any("Closing balance check" in w.value and "difference 42.00" in w.value for w in at.warning), [w.value for w in at.warning]
    assert "Closing balance check" in card_text(at) and "difference 42.00" in card_text(at)
    print("PASSED")

    print("TEST: a statement over two months uses the main month, and the opening balance is the balance before THAT month's first transaction")
    april = [(date(2026, 4, 28), "Carrefour Supermarket", -100.0), (date(2026, 4, 30), "Uber trip", -20.0)]
    balance, body = 5120.0, []
    for when, text_, signed in april:
        balance = round(balance + signed, 2)
        body.append([when.strftime("%d/%m/%Y"), text_, f"{-signed:,.2f}", None, f"{balance:,.2f}"])
    frame = sf.statement_frame("debit_credit")
    header_at = next(i for i, r in enumerate(frame) if r[0] == "Date")
    two_months = pd.DataFrame(frame[:header_at + 1] + body + frame[header_at + 1:]).to_csv(index=False, header=False).encode()
    analysis = autopilot.analyze(two_months, is_csv=True)
    print("  ", analysis.month.value, analysis.opening.value, "|", round(analysis.statement_check["difference"], 2))
    assert analysis.month.value == date(2026, 5, 1) and analysis.opening.value == 5000.0 and analysis.statement is not None
    assert abs(analysis.statement_check["difference"]) < 0.005 and len(analysis.rows) == 30
    print("PASSED")

print("TEST: blank lines inside a statement do not shift the row numbers shown (rows keep the number they have in the user's file)")
frame = sf.statement_frame("debit_credit")
frame = frame[:8] + [[None] * 5] + frame[8:]
analysis = autopilot.analyze(pd.DataFrame(frame).to_csv(index=False, header=False).encode(), is_csv=True)
assert analysis.statement is not None and len(analysis.rows) == 28
salary = analysis.rows[analysis.rows["Description"] == "Salary May 2026"].iloc[0]
assert int(salary["Source row"]) == 5                                                              # 3 title lines + header + the first transaction
last = analysis.rows[analysis.rows["Description"] == "Spinneys grocery"].iloc[-1]
assert int(last["Source row"]) == 5 + 27 + 1                                                       # 28 transactions and one blank line between them
assert abs(analysis.statement_check["difference"]) < 0.005
print("PASSED")
