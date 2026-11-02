"""
monthly_app.py
---------------
"Check every month": set a few simple rules ONCE from a clean sample file, then every month upload the rules and the new file and get a
pass / fail card, a "Please check" list (worst first) and a highlighted copy of the file with a "Rule report" sheet.
Your data is never changed, and nothing is stored on the server.

  1. Make rules   - upload a clean sample; the rules are suggested per column (required, allowed values, number range with a margin,
                    date span, email / phone, unique id, expected columns); tick and edit them; download the rules file (.json)
  2. Check a file - upload the rules file and the new file
"""

import pandas as pd
import streamlit as st

from budget import importer
from datarules import check as ck, model, output, suggest
from locales import DEFAULT_LOCALE, LOCALES
from reconcile import columns as rc
from toolkit import ui
from toolkit.highlight import PatchError
from toolkit.report import Fact, Report

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAKE, CHECK = "Make rules from a clean sample file", "Check this month's file"
SHOWN_RULES = 400


@st.cache_data(show_spinner="Reading the file ...")
def read_sheets(file_bytes: bytes, is_csv: bool) -> dict:
    return importer.read_raw_sheets(file_bytes, is_csv)


def pick_table(label: str, key: str, types=("csv", "xlsx", "xlsm")):
    """Upload + sheet picker -> (name, bytes, is_csv, sheet, table, file id) or None."""
    uploaded = st.file_uploader(label, type=list(types), key=f"monthly_upload_{key}")
    if uploaded is None:
        return None
    data, is_csv = uploaded.getvalue(), uploaded.name.lower().endswith(".csv")
    try:
        sheets = read_sheets(data, is_csv)
    except Exception as error:
        st.error(f"I couldn't read {uploaded.name} ({type(error).__name__}). Please check that it is a normal CSV or .xlsx file.")
        return None
    names = [n for n, frame in sheets.items() if len(frame) > 1] or list(sheets)
    sheet = names[0] if len(names) == 1 else st.selectbox(f"Sheet of {uploaded.name}", names, index=names.index(max(names, key=lambda n: len(sheets[n]))),
                                                          key=f"monthly_sheet_{key}")
    table, header_row = rc.table_from_raw(sheets[sheet])
    if table.empty:
        st.error(f"{uploaded.name} has no data rows under its header.")
        return None
    return uploaded.name, data, is_csv, sheet, table, f"{key}:{uploaded.name}:{uploaded.size}:{sheet}"


# ---------------------------------------------------------------------------------------------------------------------------
# 1. MAKE RULES
# ---------------------------------------------------------------------------------------------------------------------------
def rules_table(ruleset: model.RuleSet) -> pd.DataFrame:
    return pd.DataFrame([{"Use": r.enabled, "Column": r.column or "(whole file)", "Rule": r.label, "Setting": model.setting_text(r), "Why": r.why} for r in ruleset.rules])


def make_rules_page():
    picked = pick_table("A clean sample file (a normal month that you are happy with)", "sample")
    if picked is None:
        st.info("Upload a clean sample file. I look at every column and suggest simple rules - which columns must be filled, which values are allowed, "
                "sensible number and date ranges, valid emails and phones, unique ids. You tick and edit them, then download the rules file.")
        return
    name, _, _, sheet, table, file_id = picked
    with st.expander("Edit details"):
        region = st.selectbox("Country (phone numbers and date order)", list(LOCALES), index=list(LOCALES).index(DEFAULT_LOCALE),
                              format_func=lambda c: f"{LOCALES[c]['label']} ({c})", key="monthly_region")
    suggested = suggest.suggest(table, name, region)
    counts = {}
    for rule in suggested.rules:
        counts[rule.label] = counts.get(rule.label, 0) + 1
    without = [c for c in table.columns if not any(r.column == str(c) for r in suggested.rules)]
    ui.render_card(Report("Rules", facts=[
        Fact("Sample file", f"{len(table):,} rows, {len(table.columns)} columns" + (f" (sheet '{sheet}')" if sheet else ""), name, "high"),
        Fact("Rules suggested", f"{len(suggested.rules)}: " + ", ".join(f"{n} × {label.lower()}" for label, n in counts.items()), "looked at every column of the sample", "high"),
        Fact("No rule suggested for", ", ".join(map(str, without)) if without else "every column has at least one rule",
             "empty columns, or nothing a normal month must follow (free text, mostly blank)", "medium")]),
        title="What I found", summary="Untick what you do not want, change a Setting if you like, then download the rules file.")

    st.header("Your rules")
    st.caption("Use = tick to keep the rule. Setting is editable: allowed values are separated by |, a range is written 0 .. 250, dates as 'within 40 days' "
               "or 2026-05-01 .. 2026-05-31. Every rule says why it was suggested.")
    shown = suggested.rules[:SHOWN_RULES]
    frame = rules_table(model.RuleSet(rules=shown))
    edited = st.data_editor(frame, hide_index=True, key=f"monthly_rules_{file_id}_{region}", disabled=["Column", "Rule", "Why"], width="stretch",
                            column_config={"Use": st.column_config.CheckboxColumn("Use", width="small"), "Setting": st.column_config.TextColumn("Setting", width="large")})
    final, problems = [], []
    for rule, (_, row) in zip(shown, edited.iterrows()):
        params = rule.params
        if str(row["Setting"]).strip() != model.setting_text(rule):
            try:
                params = model.parse_setting(rule.type, str(row["Setting"]))
            except ValueError as error:
                problems.append(f"{rule.column or 'Expected columns'} - {rule.label}: {error}")
        final.append(model.Rule(rule.id, rule.column, rule.type, params, bool(row["Use"]), rule.why))
    for problem in problems:
        st.error(problem)
    ruleset = model.RuleSet(st.text_input("Name of this set of rules", value=suggested.name, key=f"monthly_name_{file_id}") or suggested.name, name, suggested.created, region,
                            suggested.columns, final + suggested.rules[SHOWN_RULES:])
    on = sum(1 for r in ruleset.rules if r.enabled)
    st.download_button(f"Download the rules file (.json) - {on} rules on", model.to_json(ruleset), f"{ruleset.name.strip().replace(' ', '_') or 'rules'}.json", "application/json",
                       key="monthly_download_rules", type="primary", disabled=bool(problems) or on == 0,
                       help="Keep this file. Every month, upload it together with the new file in 'Check this month's file'.")
    if not on:
        st.caption("Tick at least one rule.")


# ---------------------------------------------------------------------------------------------------------------------------
# 2. CHECK A FILE
# ---------------------------------------------------------------------------------------------------------------------------
@st.cache_data(show_spinner="Checking every rule ...")
def run_check(rules_bytes: bytes, file_bytes: bytes, is_csv: bool, sheet: str, name: str, allow_similar: bool):
    """(result, highlighted copy or None, why there is none). Cached so a click elsewhere does not redo the work."""
    ruleset = model.from_json(rules_bytes)
    sheets = read_sheets(file_bytes, is_csv)
    raw = sheets[sheet]
    table, _ = rc.table_from_raw(raw)
    shown_sheet = "Data" if is_csv else sheet
    result = ck.check(table, ruleset, name, shown_sheet, allow_similar)
    try:
        original = output.workbook_from_raw(raw, shown_sheet) if is_csv else file_bytes
        return result, output.make_highlighted(original, result, shown_sheet), None
    except PatchError as error:
        return result, None, str(error)


def check_page():
    left, right = st.columns(2)
    with left:
        rules_file = st.file_uploader("The rules file (.json)", type=["json"], key="monthly_upload_rules")
    with right:
        picked = pick_table("This month's file", "month")
    if rules_file is None or picked is None:
        st.info("Upload the rules file you made earlier and this month's file. I check every rule, tell you pass or fail, and give you a copy of the file with "
                "the problems highlighted. Your file is never changed.")
        return
    try:
        ruleset = model.from_json(rules_file.getvalue())
    except ValueError as error:
        st.error(str(error))
        return
    name, data, is_csv, sheet, table, file_id = picked
    with st.expander("Edit details"):
        allow_similar = st.checkbox("Accept columns whose name is nearly the same (for example 'Amount' and 'Amount (AED)')", value=True, key="monthly_similar",
                                    help="Renamed columns are always reported. Switch this off to treat a renamed column as missing.")
        st.caption(f"Rules file: {ruleset.name} - {len([r for r in ruleset.rules if r.enabled])} of {len(ruleset.rules)} rules on, made from "
                   f"'{ruleset.source or 'a sample file'}' on {ruleset.created or 'an unknown date'}. Phone numbers are read for {LOCALES.get(ruleset.region, {}).get('label', ruleset.region)}.")
    result, copy, problem = run_check(rules_file.getvalue(), data, is_csv, sheet, name, allow_similar)
    (st.success if result.status == "PASS" else st.error)(
        f"{'✅ PASS' if result.status == 'PASS' else '❌ FAIL'} - " + result.report.facts[0].value.split(" - ", 1)[1])
    ui.render_card(result.report, title="What I found", summary="Every rule passed.")
    for note in result.notes:
        st.caption("ℹ " + note)
    ui.render_please_check(result.report.issues)

    st.header("Download")
    base = name.rsplit(".", 1)[0]
    if copy is not None:
        st.download_button("Download the highlighted copy (.xlsx)", copy.data, f"{base}_checked.xlsx", XLSX, key="monthly_download_copy", type="primary",
                           help="Your file with the cells that broke a rule coloured, a note on each, and a 'Rule report' sheet at the end.")
        st.caption(f"{copy.highlighted} cell(s) coloured, {copy.notes} note(s) added. No value was changed." + ("" if len(result.cells) <= output.MAX_NOTES else
                   f" (Notes were limited to the first {output.MAX_NOTES}; the report sheet lists everything.)"))
        for line in copy.skipped:
            st.caption("ℹ " + line)
    elif problem:
        st.warning(f"I couldn't make a highlighted copy that I'm sure is safe, so I'm not offering one. {problem} The report above and the text download have everything.")
    st.download_button("Download the report (.txt)", output.report_text(result, name), f"{base}_rule_report.txt", "text/plain", key="monthly_download_text")


# ---------------------------------------------------------------------------------------------------------------------------
if st.button("← Back to start", key="back_to_start"):
    st.switch_page("main.py")
st.title("✅ Check every month")
st.write("Set a few simple rules once from a clean sample file. Every month, upload the rules and the new file: you get pass or fail, a list of what to look at "
         "(worst first) and a highlighted copy of the file. **Your data is never changed**, and nothing is stored on the server.")
mode = st.radio("What do you want to do?", [MAKE, CHECK], horizontal=True, key="monthly_mode")
make_rules_page() if mode == MAKE else check_page()
