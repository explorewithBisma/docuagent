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
import theme

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
GRID = [(2, 1), (1, 1, 1)]          # the bento grid: a wide card and a narrow one, then three equal ones (the pattern repeats)
STEPS = [("Upload", "your Excel or CSV file"), ("Check", "I find the columns and problems"), ("Review", "a short list of what to look at"), ("Download", "the result - your file is never changed")]

HERO = """
<div class="hero">
  <span class="eyebrow">Five simple tools &middot; nothing stored</span>
  <h1>Fix your spreadsheets &mdash; faster and <span class="hl">safer</span></h1>
  <p>Clean, check, match and budget your Excel and CSV files. Everything is explained in plain words, and nothing is changed behind your back.</p>
</div>
"""


def hero():
    st.markdown(HERO, unsafe_allow_html=True)
    first, second, _ = st.columns([1.3, 1.3, 5])
    if first.button("Clean a file", key="hero_clean", type="primary"):
        st.switch_page(PAGES["cleaner"])
    if second.button("Match two files", key="hero_match"):
        st.switch_page(PAGES["match"])


def tool_card(key, icon, title, text):
    with st.container(border=True, key=f"tool_{key}"):
        st.subheader(f"{icon} {title}")
        st.write(text)
        page = PAGES.get(key)
        if page is not None:
            if st.button("Open", key=KEYS[key], type="primary"):
                st.switch_page(page)
        else:
            st.button("Coming soon", key=KEYS[key], disabled=True)


def start_screen():
    hero()
    visible = [t for t in TOOLS if PAGES.get(t[0]) is not None or config.SHOW_UNFINISHED_FEATURES]
    position = rows_done = 0
    while position < len(visible):
        shape = GRID[rows_done % len(GRID)]
        row = visible[position:position + len(shape)]
        for column, tool in zip(st.columns(shape[:len(row)]), row):
            with column:
                tool_card(*tool)
        position, rows_done = position + len(shape), rows_done + 1
    steps = "".join(f'<div class="step"><div class="n">{n}</div><b>{title}</b><span>{text}</span></div>' for n, (title, text) in enumerate(STEPS, start=1))
    st.markdown(f'<h3>4 simple steps</h3><div class="steps">{steps}</div>', unsafe_allow_html=True)
    st.caption("🔒 Privacy: Files are processed in memory and not stored. Nothing is kept on the server after you close the page.")


start_page = st.Page(start_screen, title="Start", icon="🏠", default=True, url_path="start")
cleaner_page = st.Page("app.py", title="Clean my file", icon="🧹", url_path="cleaner")
check_page = st.Page("doctor_app.py", title="Check my file", icon="🩺", url_path="check")
match_page = st.Page("match_app.py", title="Match two files", icon="🔗", url_path="match")
monthly_page = st.Page("monthly_app.py", title="Check every month", icon="✅", url_path="monthly")
budget_page = st.Page("budget_app.py", title="Build my budget", icon="💰", url_path="budget")
PAGES = {"cleaner": cleaner_page, "check": check_page, "match": match_page, "budget": budget_page, "monthly": monthly_page}

st.session_state["_started_from_main"] = True      # the pages show "Back to start" only when they run inside this app
theme.inject()          # the stylesheet, once per run (every page runs inside this script)
theme.top_bar()
st.navigation([start_page, cleaner_page, check_page, match_page, budget_page, monthly_page], position="hidden").run()
