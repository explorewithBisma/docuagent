"""
main.py
--------
The app's entry point: `streamlit run main.py`.

A start screen lets the user choose between the two modes:
  A. Clean my Excel/CSV   - the existing cleaner (app.py, unchanged)
  B. Build my budget sheet - the budget workbook builder (budget_app.py)
Nothing is stored on the server in either mode: a file goes in, a better file comes out.
"""

import streamlit as st

st.set_page_config(page_title="Spreadsheet Helper", page_icon="📊", layout="wide")


def start_screen():
    st.title("📊 Spreadsheet Helper")
    st.write("What would you like to do?")
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.subheader("🧹 Clean my Excel/CSV")
        st.write("Upload a messy file: remove duplicates and empty rows, trim spaces, fix placeholders, "
                 "standardize values (with optional AI-suggested rules), and download the clean file.")
        if st.button("Clean my Excel/CSV", key="start_cleaner", type="primary"):
            st.switch_page(cleaner_page)
    with right, st.container(border=True):
        st.subheader("💰 Build my budget sheet")
        st.write("Turn a messy personal budget (or a blank start) into a tidy Excel workbook with dropdowns, "
                 "a formula-driven dashboard and charts.")
        if st.button("Build my budget sheet", key="start_budget", type="primary"):
            st.switch_page(budget_page)


start_page = st.Page(start_screen, title="Start", icon="🏠", default=True, url_path="start")
cleaner_page = st.Page("app.py", title="Clean my Excel/CSV", icon="🧹", url_path="cleaner")
budget_page = st.Page("budget_app.py", title="Build my budget sheet", icon="💰", url_path="budget")

st.navigation([start_page, cleaner_page, budget_page], position="hidden").run()
