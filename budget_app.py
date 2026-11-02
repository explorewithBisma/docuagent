"""
budget_app.py
--------------
Mode B of the app: "Build my budget sheet". A file goes in (a messy budget workbook, a workbook this app made last
month, or nothing at all) and a better file out. Nothing is stored on the server.

Upload and go: for a messy workbook everything is detected automatically (budget/autopilot.py) and shown on one summary
card - what was found, where it came from, how sure the app is, and a short "Please check" list. Then either
"Looks good, build my sheet", or "Edit details", which opens the sections below (nothing is hidden, everything is editable):

  2. Basics    - currency, month, opening balance, categories and monthly budgets, payment methods
  3. Keywords  - the keyword map that guesses categories, and the descriptions it could not place
  4. Review    - the transactions, editable; rows that were dropped can be restored
  5. Build     - checks, then the .xlsx download

All the work is done by the budget/ package; this file only draws the screens.
"""

import json
from datetime import date

import pandas as pd
import streamlit as st

import ai_rules
from budget import ai_categories, autopilot, carryover, categorize, importer, prepare
from budget.model import MAX_CATEGORIES, TX_COLUMNS, TYPES, BudgetConfig
from budget.workbook import build_workbook
from locales import DEFAULT_LOCALE, LOCALES

CURRENCIES = ["$", "€", "£", "AED", "Rs", "₹", "SAR", "Other..."]
ROLE_LABELS = {"date": "Date", "description": "Description", "amount": "Amount", "category": "Category",
               "type": "Type", "payment": "Payment method", "debit": "Debit (money out)", "credit": "Credit (money in)"}
BADGE = {"high": "🟢 sure", "medium": "🟡 probably", "low": "🔴 guess"}
CHECKS_SHOWN = 8
STATE_KEYS = ("bd_sid", "bd_import_key", "bd_rows", "bd_dropped", "bd_kmap", "bd_ver", "bd_output", "bd_output_name",
              "bd_message", "bd_analysis", "bd_edit_open", "bd_build_now")

for key in STATE_KEYS:
    if key not in st.session_state:
        st.session_state[key] = None


@st.cache_data(show_spinner="Reading your file ...")
def read_sheets(file_bytes: bytes, is_csv: bool) -> dict:
    return importer.read_raw_sheets(file_bytes, is_csv)


@st.cache_data(show_spinner="Looking through your workbook ...")
def run_analysis(file_bytes: bytes, is_csv: bool, keyword_json: str):
    return autopilot.analyze(file_bytes, is_csv, json.loads(keyword_json))


@st.cache_data(show_spinner=False)
def run_import(raw, layout_json: str, keyword_json: str, locale_code: str, month_iso: str):
    return importer.import_transactions(raw, json.loads(layout_json), json.loads(keyword_json), locale_code,
                                        date.fromisoformat(month_iso) if month_iso else None)


def month_widgets(sid: str, default: date):
    left, right = st.columns(2)
    month_names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                   "November", "December"]
    month = left.selectbox("Month", range(1, 13), index=default.month - 1, format_func=lambda m: month_names[m - 1],
                           key=f"bd_month_{sid}")
    year = right.number_input("Year", min_value=2000, max_value=2100, value=default.year, step=1, key=f"bd_year_{sid}")
    return date(int(year), int(month), 1)


def adopt_analysis(analysis, message: str = None):
    """Make `analysis` the page's working data (rows, dropped rows) and refresh the editable tables."""
    st.session_state.bd_analysis = analysis
    st.session_state.bd_rows = analysis.rows.copy()
    st.session_state.bd_dropped = analysis.dropped
    st.session_state.bd_ver = (st.session_state.bd_ver or 0) + 1
    if message:
        st.session_state.bd_message = message


# ---------------------------------------------------------------------------------------------
if st.button("← Back to start", key="back_to_start"):
    st.switch_page("main.py")
st.title("💰 Build my budget sheet")
st.write("Upload a bank statement, a messy personal budget, last month's workbook from this app, or start blank. You get back a tidy Excel "
         "workbook with dropdowns and a dashboard made only of formulas. Nothing is stored on the server.")

if st.session_state.bd_message:
    st.success(st.session_state.bd_message)
    st.session_state.bd_message = None

# --- 1. Your file -----------------------------------------------------------------------------
st.header("1. Your file")
uploaded = st.file_uploader("Your budget workbook or bank statement (optional)", type=["xlsx", "xlsm", "csv"], key="bd_upload",
                            help="A bank statement (CSV or Excel: credits become Income, debits become Expenses), a messy daily expense log, "
                                 "or a workbook this app made for an earlier month. Leave empty to start blank.")
sid = f"{uploaded.name}-{uploaded.size}" if uploaded else "blank"
if st.session_state.bd_sid != sid:
    for key in STATE_KEYS:
        st.session_state[key] = None
    st.session_state.bd_sid = sid
    st.session_state.bd_ver = 0

kind, generated, raw, layout, analysis = "blank", None, None, None, None
file_bytes = uploaded.getvalue() if uploaded else None
is_csv = bool(uploaded) and uploaded.name.lower().endswith(".csv")
if uploaded:
    if not is_csv and carryover.is_generated_workbook(file_bytes):
        try:
            generated = carryover.read_generated_workbook(file_bytes)
            kind = "next"
        except ValueError as error:
            st.error(str(error))
    if kind == "blank":
        kind = "import"

manual = False
if kind == "import":
    manual = st.checkbox("Let me choose the columns myself", key=f"bd_manual_{sid}",
                         help="Normally the header row, columns, currency, month and balances are found automatically.")
    if not manual:
        if st.session_state.bd_kmap is None:
            st.session_state.bd_kmap = categorize.default_keyword_map()
        if st.session_state.bd_analysis is None:
            try:
                adopt_analysis(run_analysis(file_bytes, is_csv, json.dumps(st.session_state.bd_kmap)))
            except ValueError as error:
                st.info(f"{error} Choose the columns below.")
                manual = True
        analysis = st.session_state.bd_analysis

default_locale = DEFAULT_LOCALE
if kind == "import" and manual:
    sheets = read_sheets(file_bytes, is_csv)
    names = list(sheets)
    best_name, _ = importer.best_sheet(sheets)
    sheet_name = st.selectbox("Sheet to import", names, index=names.index(best_name) if best_name in names else 0,
                              key=f"bd_sheet_{sid}") if len(names) > 1 else names[0]
    raw = sheets[sheet_name]
    default_locale = st.selectbox(
        "If a date is unclear (like 03/04/2026), assume the format used in", list(LOCALES),
        index=list(LOCALES).index(DEFAULT_LOCALE), key="bd_locale",
        format_func=lambda code: f"{LOCALES[code]['label']} ({'day first' if LOCALES[code]['day_first'] else 'month first'})")
    detected = importer.detect_layout(raw)
    with st.expander("Columns found in your sheet" if detected else "⚠ Choose your columns", expanded=detected is None):
        header_row = st.number_input("Header row (the row with the column names)", 1, max(len(raw), 1),
                                     (detected["header_row"] + 1) if detected else 1, key=f"bd_header_{sid}") - 1
        guess = importer.guess_columns(raw, header_row) if not detected or header_row != detected["header_row"] \
            else detected["columns"]
        header_cells = [("" if pd.isna(v) else str(v)) for v in raw.iloc[header_row]] if len(raw) else []
        options = ["(none)"] + [f"{i + 1}: {text or '(blank)'}" for i, text in enumerate(header_cells)]
        columns = {}
        grid = st.columns(4)
        for number, (role, label) in enumerate(ROLE_LABELS.items()):
            choice = grid[number % 4].selectbox(label, options, index=(guess[role] + 1) if role in guess else 0,
                                                key=f"bd_col_{role}_{header_row}_{sid}")
            if choice != "(none)":
                columns[role] = options.index(choice) - 1
        if detected and detected["ignored"] and header_row == detected["header_row"]:
            st.caption("Not imported: " + ", ".join(detected["ignored"]))
    if not (("date" in columns or "description" in columns) and {"amount", "debit", "credit"} & set(columns)):
        st.warning("Choose at least a Date or Description column and an Amount (or Debit/Credit) column to continue.")
        st.stop()
    layout = {"header_row": header_row, "columns": columns, "ignored": []}

if kind == "next":
    previous = generated.config
    st.info(
        f"Found a workbook made by this app for **{previous.month_start:%B %Y}**. Its closing balance "
        f"(**{generated.closing_balance:,.2f}**) becomes the opening balance of the next month. Your "
        f"{len(previous.categories)} categories with budgets, {len(previous.payment_methods)} payment methods and the "
        "keyword map are kept; the Transactions sheet starts empty.")
    for note in generated.notes:
        st.warning(note)
    start_config = carryover.next_month_config(generated)
elif analysis is not None:
    start_config = analysis.config
else:
    start_config = BudgetConfig()

if st.session_state.bd_kmap is None:
    st.session_state.bd_kmap = generated.keyword_map if generated else categorize.default_keyword_map()

# --- the summary card (upload and go) ---------------------------------------------------------
def render_card(current) -> bool:
    """Draw the 'What I found' card from `current` (the detected values with the user's edits applied). True when
    'Looks good, build my sheet' was clicked."""
    build_now = False
    st.header("What I found")
    with st.container(border=True):
        for label, value, source, confidence in autopilot.summary_lines(current):
            left, middle = st.columns([2, 5])
            left.markdown(f"**{label}**")
            middle.markdown(f"{value} &nbsp; {BADGE[confidence]}")
            middle.caption(f"from {source}")
        difference = current.closing_difference
        if current.statement is not None and current.statement_check is not None:
            check = current.statement_check
            (st.caption if abs(check["difference"]) <= 0.01 else st.warning)(
                ("✔ " if abs(check["difference"]) <= 0.01 else "") + autopilot.statement_reader.check_text(check, str(current.currency.value)))
        elif difference:
            theirs, ours, gap = difference
            st.caption(f"Sanity check: your file's own estimated closing balance is {theirs:,.2f}; the one calculated here is "
                       f"{ours:,.2f} (difference {gap:,.2f}). For information only - the two can differ when the file counts "
                       "things differently.")
        for note in current.notes:
            st.caption("ℹ " + note)

        unmatched = current.unmatched
        if len(unmatched):
            available, reason = ai_categories.ai_available()
            st.warning(f"**{len(unmatched)} description(s) ({current.currency.value} {unmatched['Total'].sum():,.2f} total) "
                       "couldn't be categorised.** Use AI to suggest categories? Only the description text is sent - "
                       "never amounts, dates or anything else.")
            if st.button("Use AI to suggest categories", key="bd_use_ai", disabled=not available):
                try:
                    suggestion = ai_categories.suggest_categories(list(unmatched["Description"]), list(current.categories))
                    updated = autopilot.apply_ai(current, suggestion["assignments"],
                                                 {"provider": suggestion["provider"], "model": suggestion["model"]})
                    adopt_analysis(updated, f"AI suggested a category for {updated.ai_assigned} row(s) "
                                            f"({ai_rules.PROVIDER_LABELS[suggestion['provider']]}, {suggestion['model']}). "
                                            "They are marked 'AI suggested' - please check them.")
                    st.rerun()
                except ai_rules.AISuggestionError as error:
                    st.error(str(error))
            if not available:
                st.caption(reason)
        elif current.ai_assigned:
            st.caption(f"{current.ai_assigned} categories were suggested by AI "
                       f"({current.ai_info['provider']}, {current.ai_info['model']}).")

        if current.checks:
            st.markdown("**Please check**")
            for item in current.checks[:CHECKS_SHOWN]:
                st.markdown(f"- {item.text}")
                if item.action in ("switch_payments_to_card", "switch_payments_to_loan"):
                    to_card = item.action == "switch_payments_to_card"
                    if st.button("Switch to Card bill payment" if to_card else "Switch back to Loan payment",
                                 key=f"bd_{item.action}"):
                        adopt_analysis(autopilot.switch_payments(current, "Card bill payment" if to_card else "Loan payment"),
                                       "Payments switched.")
                        st.rerun()
            if len(current.checks) > CHECKS_SHOWN:
                st.caption(f"... and {len(current.checks) - CHECKS_SHOWN} more in the review table below.")
        else:
            st.success("Nothing looks uncertain.")

        actions = st.columns([2, 2, 4])
        if actions[0].button("Looks good, build my sheet", type="primary", key="bd_looks_good"):
            build_now = True
        if actions[1].button("Edit details", key="bd_edit_details"):
            st.session_state.bd_edit_open = True
            st.rerun()                      # the sections above this card were already drawn closed: draw them again, open
    return build_now


card_slot = st.container() if analysis is not None else None      # filled in after the sections, so it shows their edits
output_slot = st.container() if analysis is not None else None      # the finished file appears right under the card

# --- the editable sections ------------------------------------------------------------------------
if kind == "import" and analysis is not None:
    editing = bool(st.session_state.bd_edit_open)
    sections = st.expander("Edit details" + ("​" if editing else ""), expanded=editing)
else:
    sections = st.container()

with sections:
    st.header("2. Basics")
    if kind == "import" and analysis is None:
        first_import = run_import(raw, json.dumps(layout), json.dumps(st.session_state.bd_kmap), default_locale, "")
        detected_month = importer.most_common_month(first_import.rows)
    else:
        detected_month = None
    if kind == "next":
        default_month = start_config.month_start
    elif analysis is not None:
        default_month = analysis.month.value
    else:
        default_month = detected_month or date.today().replace(day=1)

    left, right = st.columns(2)
    currency_default = str(start_config.currency) if (kind == "next" or analysis is not None) else "$"
    with left:
        currency_choice = st.selectbox(
            "Currency", CURRENCIES, index=CURRENCIES.index(currency_default) if currency_default in CURRENCIES
            else CURRENCIES.index("Other..."), key=f"bd_currency_{sid}")
        currency = st.text_input("Currency symbol or code", value=currency_default if currency_choice == "Other..." else "",
                                 key=f"bd_currency_other_{sid}") if currency_choice == "Other..." else currency_choice
    with right:
        opening = st.number_input("Opening balance", value=float(start_config.opening_balance), step=10.0, format="%.2f",
                                  key=f"bd_opening_{sid}",
                                  help="The money you start the month with. For next month this is last month's closing balance.")
    month_start = month_widgets(sid, default_month)
    if kind == "import" and analysis is None and detected_month and detected_month != month_start:
        st.caption(f"Most of your dates are in {detected_month:%B %Y}. Rows in other months stay in the sheet, greyed out, "
                   "and are ignored by the Dashboard.")

    result = None
    if kind == "import" and analysis is None:
        result = run_import(raw, json.dumps(layout), json.dumps(st.session_state.bd_kmap), default_locale, month_start.isoformat())
        import_key = (sid, json.dumps(layout, sort_keys=True), default_locale, month_start.isoformat())
        if st.session_state.bd_import_key != import_key:
            st.session_state.bd_import_key = import_key
            st.session_state.bd_rows = importer.flag_outside_month(result.rows, month_start)
            st.session_state.bd_dropped = result.dropped
            st.session_state.bd_ver = (st.session_state.bd_ver or 0) + 1
    elif analysis is not None and st.session_state.bd_rows is not None:
        st.session_state.bd_rows = importer.flag_outside_month(st.session_state.bd_rows, month_start)

    if analysis is not None:
        categories_start = dict(analysis.categories)
    else:
        existing = start_config.categories if kind == "next" else None
        categories_start = prepare.starting_categories(st.session_state.bd_kmap, st.session_state.bd_rows, existing)
    st.write("**Categories and monthly budgets**")
    category_table = st.data_editor(
        pd.DataFrame({"Category": list(categories_start), "Monthly budget": [float(v) for v in categories_start.values()]}),
        num_rows="dynamic", hide_index=True, key=f"bd_cats_{sid}_{st.session_state.bd_ver}",
        column_config={"Monthly budget": st.column_config.NumberColumn(min_value=0.0, format="%.2f")})
    st.caption(f"Up to {MAX_CATEGORIES} categories. A budget of 0 means \"no budget set\".")
    methods_default = "\n".join(start_config.payment_methods)
    methods_text = st.text_area("Payment methods (one per line)", value=methods_default, height=130, key=f"bd_methods_{sid}")

    categories = {}
    for _, entry in category_table.iterrows():
        name = "" if pd.isna(entry["Category"]) else str(entry["Category"]).strip()
        if name:
            categories[name] = 0.0 if pd.isna(entry["Monthly budget"]) else float(entry["Monthly budget"])
    config = BudgetConfig(currency=currency, month_start=month_start, opening_balance=float(opening), categories=categories,
                          payment_methods=[line.strip() for line in methods_text.splitlines() if line.strip()])

    # --- 3. Keywords ---------------------------------------------------------------------------
    st.header("3. Keywords")
    st.write("A keyword is a word in a description that tells the app the category (for example *taxi* or *metro* → Transport). "
             "Edit the table to teach it your own shops and habits.")
    kmap = st.session_state.bd_kmap
    with st.expander("Keyword map (editable)", expanded=kind == "import" and analysis is None):
        edited_map = st.data_editor(pd.DataFrame(categorize.keyword_map_to_rows(kmap)), num_rows="dynamic", hide_index=True,
                                    key=f"bd_kmap_{sid}_{st.session_state.bd_ver}", width="stretch")
        buttons = st.columns(3)
        if buttons[0].button("Apply keyword changes", key="bd_apply_kmap"):
            try:
                new_map = categorize.keyword_map_from_rows(edited_map.to_dict("records"), kmap.get("type_keywords", {}))
                st.session_state.bd_kmap = new_map
                if st.session_state.bd_rows is not None:
                    st.session_state.bd_rows = importer.categorize_rows(st.session_state.bd_rows, new_map)
                st.session_state.bd_ver += 1
                st.session_state.bd_message = "Keyword changes applied."
                st.rerun()
            except ValueError as error:
                st.error(str(error))
        buttons[1].download_button("Download keyword map (.json)", categorize.keyword_map_bytes(kmap), "keyword_map.json",
                                   "application/json", key="bd_dl_kmap")
        uploaded_map = st.file_uploader("Load a keyword map (.json)", type=["json"], key="bd_kmap_upload")
        if uploaded_map is not None and st.button("Use this keyword map", key="bd_use_kmap"):
            try:
                st.session_state.bd_kmap = categorize.load_keyword_map(uploaded_map.getvalue())
                if st.session_state.bd_rows is not None:
                    st.session_state.bd_rows = importer.categorize_rows(st.session_state.bd_rows, st.session_state.bd_kmap)
                st.session_state.bd_ver += 1
                st.session_state.bd_message = "Keyword map loaded."
                st.rerun()
            except ValueError as error:
                st.error(str(error))

    if kind == "import" and st.session_state.bd_rows is not None:
        unknown = importer.unknown_descriptions(st.session_state.bd_rows)
        if unknown.empty:
            st.success("Every expense has a category.")
        else:
            st.warning(f"{len(unknown)} description(s) did not match any keyword. Pick a category for each, and tick "
                       "*Remember* to teach the keyword map.")
            names = list(categories) or list(kmap["categories"])
            table = unknown.assign(Category=None, Remember=False)
            picks = st.data_editor(table, hide_index=True, disabled=["Description", "Count", "Total"],
                                   key=f"bd_unknown_{sid}_{st.session_state.bd_ver}",
                                   column_config={"Category": st.column_config.SelectboxColumn(options=names),
                                                  "Total": st.column_config.NumberColumn(format="%.2f")})
            if st.button("Apply these categories", key="bd_apply_unknown"):
                rows_now, map_now = st.session_state.bd_rows, st.session_state.bd_kmap
                for _, pick in picks.iterrows():
                    if isinstance(pick["Category"], str) and pick["Category"]:
                        rows_now = importer.assign_category(rows_now, pick["Description"], pick["Category"])
                        if pick["Remember"]:
                            map_now = categorize.add_keyword(map_now, pick["Category"], str(pick["Description"]).strip().lower())
                st.session_state.bd_rows, st.session_state.bd_kmap = rows_now, map_now
                st.session_state.bd_ver += 1
                st.session_state.bd_message = "Categories applied."
                st.rerun()

    # --- 4. Review rows ------------------------------------------------------------------------
    final_rows = pd.DataFrame(columns=list(TX_COLUMNS))
    if kind == "import" and st.session_state.bd_rows is not None:
        st.header("4. Review your transactions")
        source_stats = (analysis.import_result if analysis is not None else result).stats
        shown = st.session_state.bd_rows
        flag_text = shown["Flags"].astype(str)
        cards = st.columns(4)
        cards[0].metric("Rows imported", len(shown))
        cards[1].metric("Dates filled down", int(flag_text.str.contains("date filled down").sum()),
                        help="Rows with no date copy the date of the row above; they are yellow in the final sheet.")
        cards[2].metric("Rows dropped", source_stats["dropped"], help="Totals, notes and lines with no amount.")
        cards[3].metric("Still without category", int(((shown["Type"] == "Expense") & shown["Category"].isna()).sum()))
        for note in (analysis.import_result if analysis is not None else result).notes:
            st.caption("ℹ " + note)
        working = shown.copy()
        working["Date"] = pd.to_datetime(working["Date"])
        edited = st.data_editor(
            working[[*TX_COLUMNS, "Flags", "Source row"]], num_rows="dynamic", hide_index=True, width="stretch",
            key=f"bd_rows_{sid}_{st.session_state.bd_ver}", disabled=["Flags"],
            column_config={
                "Date": st.column_config.DateColumn(format="DD MMM YYYY"),
                "Type": st.column_config.SelectboxColumn(options=list(TYPES)),
                "Category": st.column_config.SelectboxColumn(options=list(categories)),
                "Amount": st.column_config.NumberColumn(min_value=0.0, format="%.2f"),
                "Source row": None})
        edited["Date"] = edited["Date"].map(lambda value: None if pd.isna(value) else pd.Timestamp(value).date())
        final_rows = edited

        dropped = st.session_state.bd_dropped
        if dropped is not None and len(dropped):
            with st.expander(f"{len(dropped)} row(s) were dropped - check them"):
                st.dataframe(dropped, hide_index=True, width="stretch")
                chosen = st.multiselect("Restore these source rows", list(dropped["Source row"]), key=f"bd_restore_{sid}")
                if chosen and st.button("Restore selected rows", key="bd_restore_btn"):
                    st.session_state.bd_rows = prepare.restore_dropped(edited, dropped, chosen, kmap)
                    st.session_state.bd_dropped = dropped[~dropped["Source row"].isin(chosen)]
                    st.session_state.bd_ver += 1
                    st.session_state.bd_message = f"Restored {len(chosen)} row(s)."
                    st.rerun()
        st.caption("Yellow dates, a *Flags* text and the Import Notes sheet show everything the app changed or guessed.")

# --- the summary card, drawn last so it reflects every edit made above -----------------------------
build_now = False
if analysis is not None:
    with card_slot:
        build_now = render_card(autopilot.with_edits(analysis, config, final_rows))

# --- 5. Build ---------------------------------------------------------------------------------
st.header(f"{'5' if kind == 'import' else '4'}. Build your workbook")
rows_ready, config_ready, problems, notes = prepare.finalize(final_rows, config)
for problem in problems:
    st.error(problem)
for note in notes:
    st.caption("ℹ " + note)

if (st.button("Build my budget sheet", type="primary", disabled=bool(problems), key="bd_build") or build_now) and not problems:
    try:
        info = None
        if analysis is not None:
            info = autopilot.import_info(analysis, final_rows)
        elif kind == "import":
            info = result
        st.session_state.bd_output = build_workbook(config_ready, st.session_state.bd_kmap, rows_ready if len(rows_ready) else None, info)
        st.session_state.bd_output_name = f"Budget_{month_start:%Y-%m}.xlsx"
    except ValueError as error:
        st.session_state.bd_output = None
        st.error(str(error))
elif build_now and problems:
    st.error("Please fix the problems above first (open *Edit details*).")

if st.session_state.bd_output:
    with (output_slot if output_slot is not None else st.container()):
        st.success(f"Your workbook is ready: {len(rows_ready)} transaction(s), {len(config_ready.categories)} categories, "
                   f"{month_start:%B %Y}.")
        st.download_button("Download budget workbook (.xlsx)", st.session_state.bd_output,
                           st.session_state.get("bd_output_name", "Budget.xlsx"),
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="bd_download", type="primary")
        st.caption("Open it in Excel: the first sheet, *How to use*, explains everything in eight short steps. "
                   "Changed something above? Click *Build* again.")
