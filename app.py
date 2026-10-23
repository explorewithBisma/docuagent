"""
app.py
-------
A simple web page (built with Streamlit) that lets you:
1. Upload a messy .xlsx or .csv file
2. Choose which cleaning steps to run (and, for multi-sheet Excel files,
   which sheet(s) to clean)
3. Click "Clean File" to clean it
4. Optionally ask the AI to suggest extra cleaning rules (phone numbers,
   casing, dates, currency) based only on column names + a few sample
   values - review and approve/reject each one before anything runs
5. Review a summary of exactly what changed
6. Download the cleaned result, the summary as a text report, and/or a
   reusable "recipe" of the rules you approved

This file only contains the page itself (what the user sees and clicks).
The actual logic lives in dedicated modules:
    cleaning.py     - the 4 deterministic cleaning steps
    io_utils.py     - loading/saving files
    report.py       - building the summary report
    rule_engine.py  - the 4 AI-suggestable rule types, run locally with pandas
    ai_rules.py     - talks to the Anthropic API to SUGGEST rules (never runs them)
    locales.py      - country/locale defaults (phone region, date, currency)
    recipes.py      - saving/loading a reusable set of approved rules

Run it with:
    streamlit run app.py
"""

import copy
import json
from functools import partial

import streamlit as st  # the web UI framework

from changes import ChangeLog
from cleaning import clean_dataframe_with_steps
from derive import detect_relationships
from io_utils import load_sheets, convert_to_download_bytes
from xlsx_export import export_workbook
from report import build_report_text, render_quality_panel, render_summary_panel
from locales import LOCALES, DEFAULT_LOCALE
from quality import DEFAULT_PLACEHOLDERS, columns_with_inconsistent_casing, parse_placeholder_text, profile_dataframe
import ai_rules
import rule_engine
import recipes


@st.cache_data(show_spinner="Looking for relationships between columns ...")
def detect_for_sheet(df, steps, placeholders):
    """Clean a copy of one sheet (without the fill step) and look for columns
    that can be calculated from each other. Cached so it only re-runs when the
    data or the cleaning settings change."""
    prepared, _ = clean_dataframe_with_steps(df.copy(), {**steps, "fills": False}, {"placeholders": list(placeholders)})
    return detect_relationships(prepared)


@st.cache_data(show_spinner=False)
def cached_profile(df, placeholders):
    """Per-column data-quality profile (cached - recomputing it on every click would be wasteful)."""
    return profile_dataframe(df, list(placeholders))


def build_quality(cleaned_sheets: dict, sheet_stats: dict, quality_before: dict, placeholders) -> dict:
    """Before/after quality tables for every sheet that was cleaned. 'After' is
    measured on the data as it is right now, so it includes any AI rules applied."""
    return {
        name: {"before": quality_before[name], "after": cached_profile(cleaned_sheets[name], tuple(placeholders))}
        for name, info in sheet_stats.items() if info["cleaned"] and name in quality_before
    }


def collect_unique_approved_rules(suggested_rules_by_sheet: dict) -> list:
    """Flatten the approved rules across every sheet into one deduplicated
    list, so a saved recipe doesn't store the same rule once per sheet it
    happened to apply to. Two rules are "the same" if they target the same
    column with the same rule_type and params."""
    seen = set()
    unique_rules = []

    for rules in suggested_rules_by_sheet.values():
        for rule in rules:
            if not rule.get("approved"):
                continue
            dedup_key = (rule["column"], rule["rule_type"], json.dumps(rule["params"], sort_keys=True))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            unique_rules.append(rule)

    return unique_rules


# ---------------------------------------------------------------------------
# THE WEB PAGE ITSELF
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Excel Cleaner", page_icon="🧹")

# --- Title ---
st.title("🧹 Excel & CSV Cleaner")
st.write("Upload a messy Excel or CSV file, clean it with one click, and download the result.")

# --- Upload button ---
uploaded_file = st.file_uploader("Upload your .xlsx or .csv file", type=["xlsx", "csv"])

# "session_state" is Streamlit's way of remembering values between clicks.
# Without it, the cleaned file (and any AI suggestions) would disappear as
# soon as you clicked anything else.
SESSION_KEYS = (
    "cleaned_sheets", "sheet_stats", "report_text", "original_filename", "is_csv",
    "suggested_rules_by_sheet", "rule_outcomes_by_sheet", "suggestion_info", "placeholder_list", "quality_before",
    "change_logs", "original_rows",
)
for key in SESSION_KEYS:
    if key not in st.session_state:
        st.session_state[key] = None

# Bumped every time a fresh batch of suggested rules replaces the old one, so
# each batch's checkboxes get brand-new widget keys. Without this, a checkbox
# could silently inherit a *previous* batch's checked state just because a
# rule happened to reuse the same id (e.g. "rule_1") - which would mean a
# rule looks approved without the user actually reviewing this batch.
if "rules_batch_id" not in st.session_state:
    st.session_state.rules_batch_id = 0

if uploaded_file is not None:
    # If a *different* file gets uploaded, clear out any old cleaning results
    # (and AI suggestions) so they don't confusingly stick around next to the
    # new file's preview.
    if st.session_state.original_filename != uploaded_file.name:
        for key in SESSION_KEYS:
            if key not in ("original_filename",):
                st.session_state[key] = None

    is_csv = uploaded_file.name.lower().endswith(".csv")
    sheets = load_sheets(uploaded_file, is_csv)
    sheet_names = list(sheets.keys())

    # --- Preview of the raw uploaded data ---
    st.subheader("Preview of uploaded file")
    if len(sheet_names) > 1:
        for name in sheet_names:
            with st.expander(f"Sheet: {name}"):
                st.dataframe(sheets[name].head())
    else:
        st.dataframe(sheets[sheet_names[0]].head())

    # --- Sheet selection (only shown for multi-sheet Excel files) ---
    sheets_to_clean = sheet_names  # default: every sheet gets cleaned
    if not is_csv and len(sheet_names) > 1:
        st.subheader("Which sheet(s) do you want to clean?")
        choice = st.radio("Choose one:", options=["All sheets"] + sheet_names, index=0)
        sheets_to_clean = sheet_names if choice == "All sheets" else [choice]
        st.caption("Sheets you don't select are kept in the downloaded file exactly as they are.")

    # --- Cleaning step toggles (all on by default) ---
    st.subheader("Choose which cleaning steps to run")
    do_headers = st.checkbox(
        "Clean column headers",
        value=True,
        help="Removes extra spaces from column names and renames blank headers.",
    )
    do_trim = st.checkbox(
        "Trim extra spaces from text cells",
        value=True,
        help="Removes leading/trailing spaces inside each text cell.",
    )
    do_duplicates = st.checkbox(
        "Remove duplicate rows",
        value=True,
        help="Removes rows that are exact copies of another row. Turn this off if repeat "
             "entries (e.g. repeat orders) are legitimate in your data.",
    )
    do_empty = st.checkbox(
        "Remove completely empty rows and columns",
        value=True,
        help="Removes rows/columns where every single cell is empty.",
    )

    with st.expander("Advanced cleaning"):
        do_placeholders = st.checkbox(
            "Convert placeholder text (ERROR, UNKNOWN, N/A ...) to missing values",
            value=True,
            help="Cells that contain only a placeholder word become truly empty, so they are counted, "
                 "can be filled in later, and no longer pollute the data. Remove a word from the list "
                 "if it is a legitimate value in your data (e.g. 'None' or 'NA' as a category).",
        )
        do_numeric = st.checkbox(
            "Convert numbers stored as text into real numbers",
            value=True,
            help="A column becomes numeric when at least 90% of its values are plain numbers. IDs, codes, "
                 "zip codes and phone numbers (including anything with leading zeros) are never converted.",
        )
        placeholder_text = st.text_area(
            "Placeholder values (one per line or comma-separated, case is ignored)",
            value="\n".join(DEFAULT_PLACEHOLDERS),
            height=190,
            help="A cell is converted only if the whole cell equals one of these.",
        )

    steps = {
        "headers": do_headers, "trim": do_trim, "duplicates": do_duplicates, "empty": do_empty,
        "placeholders": do_placeholders, "numeric": do_numeric,
    }
    steps["fills"] = True  # which relationships get used is decided by the tick boxes below
    placeholder_list = parse_placeholder_text(placeholder_text)
    options = {"placeholders": placeholder_list}

    # --- Fill missing values from other columns (relationships found automatically) ---
    approved_relationships = {name: [] for name in sheets_to_clean}
    detected = {name: detect_for_sheet(sheets[name], steps, tuple(placeholder_list)) for name in sheets_to_clean}
    if any(found["products"] or found["lookups"] for found in detected.values()):
        st.subheader("Fill missing values from other columns")
        st.caption(
            "Found automatically in your data. A missing value is only filled when it can be worked out "
            "from the other values in the same row, and existing values are never changed."
        )
        for name, found in detected.items():
            prefix = f"[{name}] " if len(sheets_to_clean) > 1 else ""
            for relationship in found["products"]:
                if st.checkbox(
                    prefix + relationship["label"], value=True, key=f"rel_{name}_{relationship['label']}",
                    help="Exact arithmetic: this holds on every row where all three values are present, so a "
                         "missing one can be calculated exactly.",
                ):
                    approved_relationships[name].append(relationship)
            for relationship in found["lookups"]:
                if st.checkbox(
                    prefix + relationship["label"], value=False, key=f"rel_{name}_{relationship['label']}",
                    help="Inferred from this file, not arithmetic: it held for every row here, but it is a "
                         "pattern, not a law. Off by default - tick it if you trust the pattern.",
                ):
                    approved_relationships[name].append(relationship)

    # --- "Clean File" button ---
    if st.button("Clean File"):
        cleaned_sheets = {}
        sheet_stats = {}
        change_logs = {}  # every changed cell, per sheet - kept in memory only

        for name, original_df in sheets.items():
            if name in sheets_to_clean:
                change_logs[name] = ChangeLog()
                sheet_options = {**options, "relationships": approved_relationships.get(name, []),
                                 "changes": change_logs[name]}
                cleaned_df, stats = clean_dataframe_with_steps(original_df.copy(), steps, sheet_options)
                sheet_stats[name] = {"cleaned": True, "stats": stats}
            else:
                # Not selected for cleaning - keep this sheet exactly as uploaded.
                cleaned_df = original_df.copy()
                sheet_stats[name] = {"cleaned": False, "stats": None}
            cleaned_sheets[name] = cleaned_df

        st.session_state.cleaned_sheets = cleaned_sheets
        st.session_state.sheet_stats = sheet_stats
        st.session_state.change_logs = change_logs
        st.session_state.original_rows = {name: len(sheets[name]) for name in sheets_to_clean}
        st.session_state.is_csv = is_csv
        st.session_state.original_filename = uploaded_file.name
        st.session_state.placeholder_list = placeholder_list  # also used to keep placeholders out of the AI samples
        st.session_state.quality_before = {
            name: cached_profile(sheets[name], tuple(placeholder_list)) for name in sheets_to_clean
        }
        st.session_state.report_text = build_report_text(
            uploaded_file.name, sheet_stats,
            quality_by_sheet=build_quality(cleaned_sheets, sheet_stats, st.session_state.quality_before, placeholder_list),
        )
        # A fresh clean invalidates any AI suggestions from a previous run.
        st.session_state.suggested_rules_by_sheet = None
        st.session_state.suggestion_info = None
        st.session_state.rule_outcomes_by_sheet = None
        st.success("File cleaned!")

# --- Show cleaning summary, cleaned preview, AI rules, and download buttons ---
if st.session_state.cleaned_sheets is not None:
    cleaned_sheets = st.session_state.cleaned_sheets
    is_csv = st.session_state.is_csv
    base_name = st.session_state.original_filename.rsplit(".", 1)[0]

    st.subheader("Preview of cleaned file")
    if len(cleaned_sheets) > 1:
        for name, df in cleaned_sheets.items():
            with st.expander(f"Sheet: {name}"):
                st.dataframe(df.head())
    else:
        st.dataframe(next(iter(cleaned_sheets.values())).head())

    # ------------------------------------------------------------------
    # AI-SUGGESTED CLEANING RULES (optional second pass)
    # ------------------------------------------------------------------
    st.subheader("AI-Suggested Cleaning Rules (optional)")
    st.caption(
        "The AI only ever sees column names, data types, and a few sample values - "
        "never your full file. Every suggested rule runs locally with pandas, and only "
        "after you approve it below."
    )

    locale_codes = list(LOCALES.keys())
    locale_code = st.selectbox(
        "Country / locale",
        options=locale_codes,
        index=locale_codes.index(DEFAULT_LOCALE),
        format_func=lambda code: f"{LOCALES[code]['label']} ({code})",
        help="Default assumption for phone region, date format, and currency symbol. "
             "The AI can still override this per column if the data clearly shows otherwise.",
    )
    mask_samples = st.checkbox(
        "Mask sample values before sending to the AI",
        value=False,
        help="Replaces every digit with '9', uppercase letters with 'X' and lowercase with 'x' before sending sample "
             "values (e.g. \"0300-1234567\" becomes \"9999-9999999\"). The AI still sees the "
             "shape of the data, never the real content.",
    )

    with st.expander("Load a saved recipe instead of asking the AI"):
        uploaded_recipe = st.file_uploader("Recipe file (.json)", type=["json"], key="recipe_uploader")
        if uploaded_recipe is not None and st.button("Load Recipe"):
            try:
                recipe = recipes.load_recipe(uploaded_recipe)
                loaded_rules_by_sheet = {
                    sheet_name: [dict(copy.deepcopy(rule), approved=True) for rule in recipe["rules"]]
                    for sheet_name in cleaned_sheets.keys()
                }
                st.session_state.suggested_rules_by_sheet = loaded_rules_by_sheet
                st.session_state.suggestion_info = None
                st.session_state.rule_outcomes_by_sheet = None
                st.session_state.rules_batch_id += 1
                st.success(
                    f"Loaded recipe '{recipe.get('recipe_name', 'Untitled')}' "
                    f"with {len(recipe['rules'])} rule(s) - review them below before applying."
                )
            except ValueError as error:
                st.error(str(error))

    # Placeholder text (ERROR, UNKNOWN ...) is junk, not data: the AI never sees it as a
    # sample, and rules never touch it - whether or not the conversion step was on.
    placeholders_in_effect = (
        list(DEFAULT_PLACEHOLDERS) if st.session_state.placeholder_list is None else st.session_state.placeholder_list
    )
    if any(info["cleaned"] and info["stats"].get("placeholders", 0) is None
           for info in st.session_state.sheet_stats.values()):
        st.warning(
            "Placeholder conversion was turned off for this clean, so placeholder text is still in the data. "
            "It is hidden from the AI and skipped by every rule, but for best results turn the step back on "
            "and click Clean File again."
        )

    if st.button("Suggest Cleaning Rules"):
        suggested_rules_by_sheet = {}
        suggestion_info = None  # which provider/model answered, plus any discarded suggestions
        try:
            provider = ai_rules.select_provider()
        except ai_rules.AISuggestionError as error:
            st.error(str(error))
            provider = None

        for sheet_name, df in cleaned_sheets.items():
            rules = []
            if provider is not None:
                profiles = ai_rules.build_column_profile(df, mask=mask_samples, placeholders=placeholders_in_effect)
                try:
                    result = ai_rules.suggest_rules(
                        profiles, locale_code, provider,
                        inconsistent_casing_columns=columns_with_inconsistent_casing(df, placeholders_in_effect),
                    )
                    rules = result["rules"]  # already validated; start unchecked
                    if suggestion_info is None:
                        suggestion_info = {"provider": result["provider"], "model": result["model"], "dropped": []}
                    suggestion_info["dropped"] += [
                        {**item, "sheet": sheet_name} for item in result["dropped"]
                    ]
                except Exception as error:  # AISuggestionError carries a user-readable message
                    st.error(f"Could not get AI suggestions for sheet '{sheet_name}': {error}")
            suggested_rules_by_sheet[sheet_name] = rules
        st.session_state.suggested_rules_by_sheet = suggested_rules_by_sheet
        st.session_state.suggestion_info = suggestion_info
        st.session_state.rule_outcomes_by_sheet = None
        st.session_state.rules_batch_id += 1

    # --- Which provider/model answered, and anything that was discarded ---
    info = st.session_state.suggestion_info
    if info:
        st.info(f"Suggestions generated by **{ai_rules.PROVIDER_LABELS[info['provider']]}** · model `{info['model']}`")
        if info["dropped"]:
            with st.expander(f"{len(info['dropped'])} suggestion(s) were discarded because they were invalid"):
                for item in info["dropped"]:
                    st.write(f"- Sheet '{item['sheet']}': \"{item['description']}\" → {item['reason']}")

    # --- Review panel: approve/reject each suggested rule ---
    if st.session_state.suggested_rules_by_sheet:
        rules_by_sheet = st.session_state.suggested_rules_by_sheet
        any_rules_at_all = any(rules_by_sheet.values())

        if not any_rules_at_all:
            st.write("No cleaning rules were suggested for this file.")
        else:
            st.write("**Review suggested rules** (approved ones will run locally, nothing is sent anywhere else):")
            show_sheet_names_for_rules = len(rules_by_sheet) > 1

            for sheet_name, rules in rules_by_sheet.items():
                if not rules:
                    continue
                section = (
                    st.expander(f"Sheet: {sheet_name}", expanded=True)
                    if show_sheet_names_for_rules else st.container()
                )
                with section:
                    for rule in rules:
                        checkbox_key = f"rule_approve_{st.session_state.rules_batch_id}_{sheet_name}_{rule['id']}"
                        checked = st.checkbox(
                            rule["description"],
                            value=rule["approved"],
                            key=checkbox_key,
                            help=f"{rule['rule_type']} on column '{rule['column']}' "
                                 f"(confidence: {rule.get('confidence', 'n/a')}) - params: {rule['params']}",
                        )
                        rule["approved"] = checked

            if st.button("Apply Approved Rules"):
                updated_sheets = {}
                rule_outcomes_by_sheet = {}

                for sheet_name, df in cleaned_sheets.items():
                    df_after_rules = df.copy()
                    outcomes = []
                    for rule in rules_by_sheet.get(sheet_name, []):
                        if not rule["approved"]:
                            continue
                        error = rule_engine.validate_rule(rule, list(df_after_rules.columns))
                        if error:
                            outcomes.append({"rule": rule, "error": error})
                            continue
                        df_after_rules, stats = rule_engine.apply_rule(
                            df_after_rules, rule, skip_values=placeholders_in_effect,
                            log=(st.session_state.change_logs or {}).get(sheet_name),
                        )
                        outcomes.append({"rule": rule, "stats": stats})
                    updated_sheets[sheet_name] = df_after_rules
                    rule_outcomes_by_sheet[sheet_name] = outcomes

                st.session_state.cleaned_sheets = updated_sheets
                st.session_state.rule_outcomes_by_sheet = rule_outcomes_by_sheet
                st.session_state.report_text = build_report_text(
                    st.session_state.original_filename,
                    st.session_state.sheet_stats,
                    rule_outcomes_by_sheet,
                    build_quality(updated_sheets, st.session_state.sheet_stats, st.session_state.quality_before,
                                  placeholders_in_effect),
                )
                st.success("Approved rules applied!")

    # "Apply Approved Rules" above may have just replaced st.session_state.
    # cleaned_sheets - re-read it so the preview/download below (and not just
    # the report) reflect the rule-applied data instead of the stale copy
    # fetched at the top of this block.
    cleaned_sheets = st.session_state.cleaned_sheets

    # --- Cleaning summary (shown here, after the AI section, so it always
    # reflects this run's state - including rules just applied above - with
    # no page refresh needed) ---
    st.subheader("Cleaning Summary")
    render_summary_panel(st.session_state.sheet_stats, st.session_state.rule_outcomes_by_sheet)

    st.subheader("Data quality: before vs after")
    render_quality_panel(build_quality(
        cleaned_sheets, st.session_state.sheet_stats, st.session_state.quality_before, placeholders_in_effect,
    ))

    # --- Save approved rules as a reusable recipe ---
    if st.session_state.rule_outcomes_by_sheet:
        st.write("**Save these approved rules as a reusable recipe:**")
        recipe_name = st.text_input("Recipe name", value="My Cleaning Recipe")
        unique_rules = collect_unique_approved_rules(st.session_state.suggested_rules_by_sheet)
        if unique_rules:
            recipe = recipes.build_recipe(recipe_name, locale_code, unique_rules)
            recipe_bytes = recipes.save_recipe_bytes(recipe)
            st.download_button(
                label="Download Recipe (.json)",
                data=recipe_bytes,
                file_name=f"{recipe_name.strip().replace(' ', '_') or 'recipe'}_recipe.json",
                mime="application/json",
            )

    # --- Download buttons for the cleaned file and the report ---
    XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    include_report = st.checkbox(
        "Add a \"Cleaning Summary\" and a \"Change Log\" sheet to the Excel file",
        value=True,
        key="include_report_sheets",
        help="Two extra sheets are added at the END of the workbook (your own sheets are never changed or "
             "reordered): a one-page summary of what was cleaned, and a filterable list of every changed cell.",
    )
    # The highlighted workbook is built only when the download button is clicked
    # (newer Streamlit); older versions get it built up front.
    build_excel = partial(
        export_workbook, cleaned_sheets, st.session_state.change_logs, st.session_state.sheet_stats,
        st.session_state.quality_before, placeholders_in_effect, st.session_state.original_filename,
        st.session_state.original_rows, include_report,
    )
    excel_data = build_excel if "callable" in (st.download_button.__doc__ or "") else build_excel()

    if is_csv:
        st.download_button(
            label="Download Cleaned File",
            data=convert_to_download_bytes(cleaned_sheets, is_csv),
            file_name=base_name + "_cleaned.csv",
            mime="text/csv",
        )
        st.download_button(
            label="Download as Excel (with highlights)",
            data=excel_data,
            file_name=base_name + "_cleaned_highlighted.xlsx",
            mime=XLSX_MIME,
            help="CSV files can't store colors or comments, so this Excel copy highlights every cell that was "
                 "changed (with a note saying what it was and why). Your plain CSV download is unchanged.",
        )
    else:
        st.download_button(
            label="Download Cleaned File",
            data=excel_data,
            file_name=base_name + "_cleaned.xlsx",
            mime=XLSX_MIME,
            help="An Excel file with every changed cell highlighted and annotated.",
        )

    st.download_button(
        label="Download Cleaning Report (.txt)",
        data=st.session_state.report_text,
        file_name=base_name + "_report.txt",
        mime="text/plain",
    )
