"""
main.py
--------
The app's entry point: `streamlit run main.py`.

The start screen offers five simple tasks. Each one is a file in, a better file (or a report) out; nothing is stored on the server.

  Clean my file        - the cleaner (app.py)
  Check my file        - Excel Doctor (doctor_app.py)
  Match two files      - reconciliation of two files (match_app.py)
  Build my budget      - the budget builder (budget_app.py)
  Check every month    - data rules checked on every month's file (monthly_app.py)

Tools that are not built yet are shown greyed out with "Coming soon". To hide them (for example before sharing the public link) set
SHOW_UNFINISHED_FEATURES = False in config.py.
"""

import streamlit as st

import config

st.set_page_config(page_title="Spreadsheet Helper", page_icon="📊", layout="wide")

# key, icon, title, one-line description, page (None = not built yet)
TOOLS = [
    ("cleaner", "🧹", "Clean my file", "Remove duplicates, empty rows and stray spaces, fix placeholders, and standardize values."),
    ("check", "🩺", "Check my file", "Find typed-in totals, broken formulas and numbers that disagree between sheets."),
    ("match", "🔗", "Match two files", "Compare a bank statement with your records, or stock with sales."),
    ("budget", "💰", "Build my budget", "Turn a messy budget or a bank statement into a clean budget workbook."),
    ("monthly", "✅", "Check every month", "Set simple rules once, then get a pass/fail report on every month's file."),
]
KEYS = {"cleaner": "start_cleaner", "check": "start_check", "match": "start_match", "budget": "start_budget", "monthly": "start_monthly"}


def start_screen():
    st.title("📊 Spreadsheet Helper")
    st.write("What would you like to do?")
    st.caption("🔒 Privacy: Files are processed in memory and not stored. Nothing is kept on the server after you close the page.")
    visible = [t for t in TOOLS if PAGES.get(t[0]) is not None or config.SHOW_UNFINISHED_FEATURES]
    for start in range(0, len(visible), 3):
        row = visible[start:start + 3]
        for column, (key, icon, title, text) in zip(st.columns(3), row):
            with column, st.container(border=True):
                st.subheader(f"{icon} {title}")
                st.write(text)
                page = PAGES.get(key)
                if page is not None:
                    if st.button(title, key=KEYS[key], type="primary"):
                        st.switch_page(page)
                else:
                    st.button("Coming soon", key=KEYS[key], disabled=True)


start_page = st.Page(start_screen, title="Start", icon="🏠", default=True, url_path="start")
cleaner_page = st.Page("app.py", title="Clean my file", icon="🧹", url_path="cleaner")
check_page = st.Page("doctor_app.py", title="Check my file", icon="🩺", url_path="check")
match_page = st.Page("match_app.py", title="Match two files", icon="🔗", url_path="match")
monthly_page = st.Page("monthly_app.py", title="Check every month", icon="✅", url_path="monthly")
budget_page = st.Page("budget_app.py", title="Build my budget", icon="💰", url_path="budget")
PAGES = {"cleaner": cleaner_page, "check": check_page, "match": match_page, "budget": budget_page, "monthly": monthly_page}

st.navigation([start_page, cleaner_page, check_page, match_page, budget_page, monthly_page], position="hidden").run()
