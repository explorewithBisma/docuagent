"""
match_app.py
-------------
"Match two files": upload two files (a bank statement and your own records, stock and sales, payroll and attendance ...) and see what
matches and what does not. The columns are found automatically (change them under "Edit details"); rows are matched in rounds - the
same reference, the same amount and date, dates a few days apart, an amount that is nearly the same (always a "maybe" for you to
confirm). A row is never matched twice. Your files are never changed; the result is a new Excel workbook.
"""

import pandas as pd
import streamlit as st

from budget import importer
from locales import DEFAULT_LOCALE, LOCALES
from reconcile import columns as rc, output as ro, report as rr, views
from reconcile.pipeline import Settings, analyze, load_table
from toolkit import ui

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ROLE_LABELS = {"date": "Date", "amount": "Amount (one column, negative = money out)", "debit": "Money out (debit) column", "credit": "Money in (credit) column",
               "reference": "Reference / id", "description": "Description"}
NONE = "(none)"
SHOWN_ROWS = 500


@st.cache_data(show_spinner="Reading the file ...")
def read_sheets(file_bytes: bytes, is_csv: bool) -> dict:
    return importer.read_raw_sheets(file_bytes, is_csv)


@st.cache_data(show_spinner=False)
def read_table(file_bytes: bytes, is_csv: bool, sheet: str):
    return load_table(read_sheets(file_bytes, is_csv)[sheet])


def choose_file(label: str, key: str):
    """Upload + sheet picker. Returns (name, table, header_row, file_id) or None."""
    uploaded = st.file_uploader(label, type=["csv", "xlsx", "xlsm"], key=f"match_upload_{key}")
    if uploaded is None:
        return None
    data = uploaded.getvalue()
    is_csv = uploaded.name.lower().endswith(".csv")
    try:
        sheets = read_sheets(data, is_csv)
    except Exception as error:                                     # a damaged or unsupported file: say so, do not crash
        st.error(f"I couldn't read {uploaded.name} ({type(error).__name__}). Please check that it is a normal CSV or .xlsx file.")
        return None
    names = [n for n, frame in sheets.items() if len(frame) > 1] or list(sheets)
    sheet = names[0]
    if len(names) > 1:
        biggest = max(names, key=lambda n: len(sheets[n]))
        sheet = st.selectbox(f"Sheet of {uploaded.name}", names, index=names.index(biggest), key=f"match_sheet_{key}")
    table, header_row = read_table(data, is_csv, sheet)
    if table.empty:
        st.error(f"{uploaded.name} has no rows under its header.")
        return None
    return uploaded.name, table, header_row, f"{key}:{uploaded.name}:{uploaded.size}:{sheet}"


def role_pickers(key: str, file_id: str, table: pd.DataFrame, detected, default_day_first: bool) -> tuple:
    """The Edit-details pickers for one file -> (overrides {role: column or None}, date order 'auto' | 'day' | 'month')."""
    columns = [NONE] + [str(c) for c in table.columns]
    overrides = {}
    left, right = st.columns(2)
    for position, role in enumerate(rc.ROLES):
        current = detected.column(role) or NONE
        chosen = (left if position % 2 == 0 else right).selectbox(ROLE_LABELS[role], columns, index=columns.index(current) if current in columns else 0,
                                                                   key=f"match_{key}_{role}_{file_id}")
        if chosen != current:
            overrides[role] = None if chosen == NONE else chosen
    order = st.radio("Dates are written", ["Detect automatically", "Day first (31/12/2026)", "Month first (12/31/2026)"], key=f"match_{key}_order_{file_id}", horizontal=True,
                     help="Only matters for dates like 03/04/2026 that can be read either way."
                     + ("" if detected.day_first_certain else " (Not certain for this file: the default for your country is used.)"))
    return overrides, {"Detect automatically": "auto", "Day first (31/12/2026)": "day", "Month first (12/31/2026)": "month"}[order]


if st.button("← Back to start", key="back_to_start"):
    st.switch_page("main.py")
st.title("🔗 Match two files")
st.write("Upload two files - for example a bank statement and your own records. I find the date, amount, reference and description columns, "
         "match the rows, and show what matches and what does not. **Your files are never changed**, and nothing is stored on the server.")

column_a, column_b = st.columns(2)
with column_a:
    file_a = choose_file("File A (for example the bank statement)", "a")
with column_b:
    file_b = choose_file("File B (for example your own records)", "b")
if file_a is None or file_b is None:
    st.info("Upload both files to start. A row is matched to at most one row of the other file; what is left over is listed.")
    st.stop()
name_a, table_a, header_a, id_a = file_a
name_b, table_b, header_b, id_b = file_b

# --- Edit details (the defaults are fine) -------------------------------------------------------------------------------------
with st.expander("Edit details"):
    locale_code = st.selectbox("Country (only used for dates that could be read either way)", list(LOCALES), index=list(LOCALES).index(DEFAULT_LOCALE),
                               format_func=lambda c: f"{LOCALES[c]['label']} ({c})", key="match_locale")
    default_day_first = LOCALES[locale_code]["day_first"]
    first, second = st.columns(2)
    day_window = first.number_input("Dates may be this many days apart", 0, 31, 3, key="match_days",
                                    help="The same amount a few days apart is a 'close' match: banks often book a payment a day or two later.")
    tolerance = second.slider("A 'maybe' may differ in amount by up to (%)", 0.0, 5.0, 0.5, 0.1, key="match_tolerance",
                              help="Rounding and small fees. Anything matched this way is only a 'maybe' until you confirm it.") / 100
    sign = st.selectbox("Money in and out", ["Detect automatically", "Signs are the same in both files", "File B has the opposite signs", "Compare only the size of the amounts"],
                        key="match_sign")
    st.markdown(f"**File A: {name_a}**")
    detected_a = rc.detect_mapping(table_a, default_day_first)
    overrides_a, order_a = role_pickers("a", id_a, table_a, detected_a, default_day_first)
    st.markdown(f"**File B: {name_b}**")
    detected_b = rc.detect_mapping(table_b, default_day_first)
    overrides_b, order_b = role_pickers("b", id_b, table_b, detected_b, default_day_first)

settings = Settings(day_window=int(day_window), tolerance=tolerance,
                    sign={"Detect automatically": "auto", "Signs are the same in both files": "same", "File B has the opposite signs": "opposite",
                          "Compare only the size of the amounts": "absolute"}[sign],
                    overrides_a=overrides_a, overrides_b=overrides_b, day_first_a=order_a, day_first_b=order_b, default_day_first=default_day_first)

cache_key = (id_a, id_b, settings.key())
if st.session_state.get("match_cache", {}).get("key") != cache_key:
    with st.spinner("Matching the rows ..."):
        st.session_state["match_cache"] = {"key": cache_key, "analysis": analyze(name_a, table_a, header_a, name_b, table_b, header_b, settings)}
        st.session_state["match_accept_round"] = st.session_state.get("match_accept_round", 0) + 1
        st.session_state["match_accept_default"] = False
analysis = st.session_state["match_cache"]["analysis"]
result, A, B, map_a, map_b = analysis.result, analysis.A, analysis.B, analysis.map_a, analysis.map_b

# --- the maybes the user has confirmed (they are part of the card and of the Excel file) -------------------------------------------
maybes = result.bucket("maybe")
accepted = set(st.session_state.get("match_accepted", {}).get(cache_key, set())) if maybes else set()
report = rr.build_report(name_a, name_b, table_a, table_b, map_a, map_b, A, B, result, accepted) if accepted else analysis.report

ui.render_card(report, summary=analysis.summary)
totals = report.stats["totals"]
cards = st.columns(5)
matched = len(result.bucket("exact")) + len(result.bucket("close")) + len(accepted)
cards[0].metric("Matched", f"{matched:,}", help="Exact and close matches, plus the maybes you confirmed.")
cards[1].metric("To confirm", f"{len(maybes) - len(accepted):,}", help="Possible matches: nearly the same amount. Confirm them below.")
cards[2].metric("Only in A", f"{len(result.only_a):,}", help=f"Money: {totals['only_a']['a']:,.2f}")
cards[3].metric("Only in B", f"{len(result.only_b):,}", help=f"Money: {totals['only_b']['b']:,.2f}")
cards[4].metric("Amount mismatches", f"{len(result.mismatches):,}", help=f"Same reference, different amount. Net difference (B - A): {totals['mismatch']['difference']:,.2f}")
st.caption(f"Money: matched {totals['exact']['a'] + totals['close']['a']:,.2f} in A and {totals['exact']['b'] + totals['close']['b']:,.2f} in B · "
           f"only in A {totals['only_a']['a']:,.2f} · only in B {totals['only_b']['b']:,.2f} · to confirm {totals['maybe']['a']:,.2f} in A and {totals['maybe']['b']:,.2f} in B")
ui.render_please_check(report.issues)

# --- maybes: confirm or leave ---------------------------------------------------------------------------------------------------
if maybes:
    st.header("Possible matches to confirm")
    st.caption("The amount is nearly the same (rounding or a small fee) and the dates are close. Tick the ones that are the same payment; "
               "they move to Matched in the Excel file. Unticked ones stay under Maybe.")
    left, right, _ = st.columns([1, 1, 4])
    if left.button("Tick all", key="match_tick_all"):
        st.session_state["match_accept_default"], st.session_state["match_accept_round"] = True, st.session_state["match_accept_round"] + 1
        st.rerun()
    if right.button("Clear", key="match_tick_none"):
        st.session_state["match_accept_default"], st.session_state["match_accept_round"] = False, st.session_state["match_accept_round"] + 1
        st.rerun()
    start = set((p.a, p.b) for p in maybes) if st.session_state.get("match_accept_default") else set()
    table = views.pairs_frame(maybes, A, B, table_a, table_b, map_a, map_b, start)
    edited = st.data_editor(table.head(SHOWN_ROWS), hide_index=True, disabled=[c for c in table.columns if c != "Accept"],
                            key=f"match_maybe_editor_{st.session_state['match_accept_round']}")
    chosen = {(p.a, p.b) for p, ticked in zip(maybes, edited["Accept"].tolist()) if ticked}
    if len(maybes) > SHOWN_ROWS:
        st.caption(f"Showing the first {SHOWN_ROWS:,} of {len(maybes):,}. The rest stay under Maybe.")
    if chosen != accepted:
        st.session_state.setdefault("match_accepted", {})[cache_key] = chosen
        st.rerun()

# --- the buckets ----------------------------------------------------------------------------------------------------------------------
st.header("Everything, by group")
tabs = st.tabs(["Matched", "Only in A", "Only in B", "Mismatches"])
shown_pairs = [p for p in result.pairs if p.bucket in ("exact", "close") or (p.a, p.b) in accepted]
with tabs[0]:
    frame = views.pairs_frame(shown_pairs, A, B, table_a, table_b, map_a, map_b)
    st.dataframe(frame.head(SHOWN_ROWS), hide_index=True)
    st.caption(f"{len(frame):,} matched" + (f" (showing the first {SHOWN_ROWS:,})" if len(frame) > SHOWN_ROWS else ""))
with tabs[1]:
    st.dataframe(views.single_frame(result.only_a, A, table_a, map_a, "A").head(SHOWN_ROWS), hide_index=True)
    st.caption(f"{len(result.only_a):,} rows of file A have no partner in file B.")
with tabs[2]:
    st.dataframe(views.single_frame(result.only_b, B, table_b, map_b, "B").head(SHOWN_ROWS), hide_index=True)
    st.caption(f"{len(result.only_b):,} rows of file B have no partner in file A.")
with tabs[3]:
    st.dataframe(views.pairs_frame(result.mismatches, A, B, table_a, table_b, map_a, map_b).head(SHOWN_ROWS), hide_index=True)
    st.caption("Same reference in both files, but not the same amount.")

# --- download ----------------------------------------------------------------------------------------------------------------------------
st.header("Download")
base = f"{name_a.rsplit('.', 1)[0]}_vs_{name_b.rsplit('.', 1)[0]}"
build = lambda: ro.build_workbook(name_a, name_b, table_a, table_b, A, B, map_a, map_b, result, report, accepted)
data = build if "callable" in (st.download_button.__doc__ or "") else build()
st.download_button("Download the result (.xlsx)", data, f"{base}_matched.xlsx", XLSX, key="match_download", type="primary",
                   help="Sheets: Summary, Matched, Maybe, Only in A, Only in B, Mismatches. Your own files are not changed.")
st.caption("Matched rows are green, maybes yellow, rows only in A orange, rows only in B blue, amount mismatches red.")
