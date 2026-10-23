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

from changes import REASON_LABELS, ChangeLog
from cleaning import clean_dataframe_with_steps
from derive import detect_relationships
from io_utils import load_sheets, convert_to_download_bytes
from xlsx_export import export_workbook
from report import (
    CHANGES_ROWS, build_report_text, changes_dataframe, legend_html, removed_rows_dataframe, render_metric_cards,
    render_quality_panel, render_summary_panel, styled_preview, summary_metrics, totals_by_reason,
)
from locales import LOCALES, DEFAULT_LOCALE
from quality import (
    DEFAULT_PLACEHOLDERS, casefolded_values, columns_with_inconsistent_casing, parse_placeholder_text, profile_dataframe,
)
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

st.set_page_config(page_title="Excel Cleaner", page_icon="🧹", layout="wide")

# --- Header ---
st.title("🧹 Excel & CSV Cleaner")
st.caption("Upload a messy spreadsheet, see exactly what changed, and download a clean, color-coded file.")

# --- Upload ---
uploaded_file = st.file_uploader("Upload your .xlsx or .csv file", type=["xlsx", "csv"])

# "session_state" is Streamlit's way of remembering values between clicks.
# Without it, the cleaned file (and any AI suggestions) would disappear as
# soon as you clicked anything else.
SESSION_KEYS = (
    "cleaned_sheets", "sheet_stats", "report_text", "original_filename", "is_csv",
    "suggested_rules_by_sheet", "rule_outcomes_by_sheet", "suggestion_info", "placeholder_list", "quality_before",
    "change_logs", "original_rows", "data_version", "records_cache",
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


def bump_data_version() -> None:
    """Call whenever the cleaned data changes, so cached change records are rebuilt."""
    st.session_state.data_version = (st.session_state.data_version or 0) + 1


def removed_rows_by_sheet(change_logs) -> dict:
    """{sheet: removed rows (original row number, reason, original content)} for the .txt report and the Changes tab."""
    return {name: log.finalize_removed() for name, log in (change_logs or {}).items() if log.removed}


def get_change_records() -> dict:
    """The final per-sheet change records for the data as it is right now
    (cached until the data changes - finalizing them on every click is wasteful)."""
    version = st.session_state.data_version or 0
    cache = st.session_state.records_cache
    if not cache or cache["version"] != version:
        cleaned = st.session_state.cleaned_sheets
        cache = {
            "version": version,
            "records": {name: log.finalize(cleaned[name]) for name, log in (st.session_state.change_logs or {}).items()
                        if name in cleaned},
        }
        st.session_state.records_cache = cache
    return cache["records"]


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

    # The preview of the raw file sits here but is filled in after the "Clean File" click is handled
    # (below), so it collapses immediately once the file has been cleaned.
    preview_area = st.container()

    # --- Cleaning options ---
    with st.container(border=True):
        st.subheader("Cleaning options")

        # Sheet selection (only shown for multi-sheet Excel files)
        sheets_to_clean = sheet_names  # default: every sheet gets cleaned
        if not is_csv and len(sheet_names) > 1:
            choice = st.radio("Which sheet(s) do you want to clean?", options=["All sheets"] + sheet_names,
                              index=0, horizontal=True)
            sheets_to_clean = sheet_names if choice == "All sheets" else [choice]
            st.caption("Sheets you don't select are kept in the downloaded file exactly as they are.")

        # Cleaning step toggles (all on by default)
        steps_column, advanced_column = st.columns(2)
        with steps_column:
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

        with advanced_column:
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

        # Fill missing values from other columns (relationships found automatically)
        approved_relationships = {name: [] for name in sheets_to_clean}
        detected = {name: detect_for_sheet(sheets[name], steps, tuple(placeholder_list)) for name in sheets_to_clean}
        if any(found["products"] or found["lookups"] for found in detected.values()):
            st.markdown("**Fill missing values from other columns**")
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

        clean_clicked = st.button("Clean File", type="primary")

    # --- "Clean File" button ---
    if clean_clicked:
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
            removed_by_sheet=removed_rows_by_sheet(change_logs),
        )
        # A fresh clean invalidates any AI suggestions from a previous run.
        st.session_state.suggested_rules_by_sheet = None
        st.session_state.suggestion_info = None
        st.session_state.rule_outcomes_by_sheet = None
        bump_data_version()
        st.success("File cleaned!")

    # --- Preview of the raw uploaded data (tucked away once the file has been cleaned) ---
    with preview_area:
        with st.expander("Preview of your uploaded file", expanded=st.session_state.cleaned_sheets is None):
            if len(sheet_names) > 1:
                for name in sheet_names:
                    st.write(f"**Sheet: {name}**")
                    st.dataframe(sheets[name].head(), hide_index=True)
            else:
                st.dataframe(sheets[sheet_names[0]].head(), hide_index=True)

# --- Results: metric cards, tabs, AI rules, and downloads ---
if st.session_state.cleaned_sheets is not None:
    cleaned_sheets = st.session_state.cleaned_sheets
    is_csv = st.session_state.is_csv
    base_name = st.session_state.original_filename.rsplit(".", 1)[0]

    # Placeholder text (ERROR, UNKNOWN ...) is junk, not data: the AI never sees it as a
    # sample, and rules never touch it - whether or not the conversion step was on.
    placeholders_in_effect = (
        list(DEFAULT_PLACEHOLDERS) if st.session_state.placeholder_list is None else st.session_state.placeholder_list
    )

    st.divider()
    st.subheader("Results")
    # These two areas sit at the top of the results but are filled in AFTER the AI section below has run,
    # so they always show the data as it is now (including any AI rules that were just applied).
    metrics_area = st.container()
    tabs_area = st.container()

    # ------------------------------------------------------------------
    # AI-SUGGESTED CLEANING RULES (optional second pass)
    # ------------------------------------------------------------------
    with st.expander("AI-suggested cleaning rules (optional)", expanded=bool(st.session_state.suggested_rules_by_sheet)):
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
                 "shape of the data, never the real content. (With masking on, the AI cannot suggest value-mapping "
                 "rules, because it never sees the real values.)",
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
                            known_values={column: casefolded_values(df[column], placeholders_in_effect)
                                          for column in df.columns if df[column].dtype == object},
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
                    if show_sheet_names_for_rules:
                        st.write(f"**Sheet: {sheet_name}**")
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
                            known = ({rule["column"]: casefolded_values(df_after_rules[rule["column"]], placeholders_in_effect)}
                                     if rule["rule_type"] == "map_values" and rule["column"] in df_after_rules.columns else None)
                            error = rule_engine.validate_rule(rule, list(df_after_rules.columns), known)
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
                        removed_rows_by_sheet(st.session_state.change_logs),
                    )
                    bump_data_version()
                    st.success("Approved rules applied!")

    # "Apply Approved Rules" above may have just replaced st.session_state.
    # cleaned_sheets - re-read it so everything below (the cards, tabs and
    # downloads) reflects the rule-applied data instead of the stale copy
    # fetched at the top of this block.
    cleaned_sheets = st.session_state.cleaned_sheets
    records_by_sheet = get_change_records()
    quality_by_sheet = build_quality(
        cleaned_sheets, st.session_state.sheet_stats, st.session_state.quality_before, placeholders_in_effect,
    )

    # --- Metric cards ---
    with metrics_area:
        render_metric_cards(summary_metrics(
            st.session_state.sheet_stats, cleaned_sheets, st.session_state.original_rows, quality_by_sheet, records_by_sheet,
        ))

    # --- Tabs: Preview / Data Quality / Changes ---
    with tabs_area:
        preview_tab, quality_tab, changes_tab = st.tabs(["Preview", "Data Quality", "Changes"])

        with preview_tab:
            st.markdown(legend_html(), unsafe_allow_html=True)
            only_changed = st.checkbox("Show only rows with changes", value=False, key="preview_only_changed")
            for name, df in cleaned_sheets.items():
                if len(cleaned_sheets) > 1:
                    st.write(f"**Sheet: {name}**")
                styler, shown, available = styled_preview(df, records_by_sheet.get(name, []), only_changed)
                st.dataframe(styler, hide_index=True)
                st.caption(f"Showing {shown:,} of {available:,} {'changed ' if only_changed else ''}rows")

        with quality_tab:
            st.subheader("Data quality: before vs after")
            render_quality_panel(quality_by_sheet)

        with changes_tab:
            st.subheader("Cleaning Summary")
            render_summary_panel(st.session_state.sheet_stats, st.session_state.rule_outcomes_by_sheet)

            removed_rows = removed_rows_by_sheet(st.session_state.change_logs)
            if removed_rows:
                st.subheader("Removed rows")
                st.caption("Rows taken out of the cleaned file, with their full original content. The Excel download "
                           "has them on a \"Removed Rows\" sheet.")
                for sheet_name, rows in removed_rows.items():
                    if len(removed_rows) > 1:
                        st.write(f"**Sheet: {sheet_name}**")
                    table = removed_rows_dataframe(st.session_state.change_logs[sheet_name].original_columns, rows)
                    st.dataframe(table.head(CHANGES_ROWS), hide_index=True)
                    duplicates = sum(1 for row in rows if row["kind"] == "duplicate")
                    st.caption(f"{duplicates:,} duplicate and {len(rows) - duplicates:,} fully empty rows removed"
                               + (f" (showing the first {CHANGES_ROWS:,})" if len(rows) > CHANGES_ROWS else ""))

            st.subheader("Every changed cell")
            table = changes_dataframe(records_by_sheet)
            if table.empty:
                st.info("No cells were changed.")
            else:
                totals_column, filter_column = st.columns([1, 2])
                with totals_column:
                    st.dataframe(totals_by_reason(records_by_sheet), hide_index=True)
                with filter_column:
                    label_to_kind = {label: kind for kind, label in REASON_LABELS.items()}
                    chosen = st.multiselect("Show these change types", options=list(label_to_kind),
                                            default=list(label_to_kind), key="changes_filter")
                visible = table[table["_kind"].isin([label_to_kind[label] for label in chosen])]
                columns_shown = ["Row", "Column", "Old value", "New value", "Reason"]
                if len(records_by_sheet) > 1:
                    columns_shown.insert(0, "Sheet")
                st.dataframe(visible[columns_shown].head(CHANGES_ROWS), hide_index=True)
                st.caption(f"Showing {min(len(visible), CHANGES_ROWS):,} of {len(visible):,} changes")

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

    # --- Downloads ---
    st.divider()
    st.subheader("Download")
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

    first_download, second_download, third_download = st.columns(3)
    if is_csv:
        with first_download:
            st.download_button(
                label="Download Cleaned File",
                data=convert_to_download_bytes(cleaned_sheets, is_csv),
                file_name=base_name + "_cleaned.csv",
                mime="text/csv",
                type="primary",
            )
        with second_download:
            st.download_button(
                label="Download as Excel (with highlights)",
                data=excel_data,
                file_name=base_name + "_cleaned_highlighted.xlsx",
                mime=XLSX_MIME,
                help="CSV files can't store colors or comments, so this Excel copy highlights every cell that was "
                     "changed (with a note saying what it was and why). Your plain CSV download is unchanged.",
            )
    else:
        with first_download:
            st.download_button(
                label="Download Cleaned File",
                data=excel_data,
                file_name=base_name + "_cleaned.xlsx",
                mime=XLSX_MIME,
                help="An Excel file with every changed cell highlighted and annotated.",
                type="primary",
            )

    with third_download:
        st.download_button(
            label="Download Cleaning Report (.txt)",
            data=st.session_state.report_text,
            file_name=base_name + "_report.txt",
            mime="text/plain",
        )
