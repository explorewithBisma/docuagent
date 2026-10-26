"""
doctor_app.py
--------------
"Check my file": upload a workbook, get a plain-language report of what each sheet does and what looks wrong - typed totals, broken
or short formulas, SUMIF criteria that match nothing, sheets that disagree - and a highlighted copy of the workbook with a note on
each problem cell. Your numbers are never changed, and nothing is stored on the server.
"""

import streamlit as st

from doctor.analyze import analyze
from doctor.context import Options
from doctor.output import make_highlighted, report_text
from toolkit import ui
from toolkit.highlight import PatchError

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@st.cache_data(show_spinner="Reading every sheet and formula ...")
def run_doctor(file_bytes: bytes, tolerance: float, include_low: bool):
    """The report, and the highlighted copy (or why there is none). Cached so a click elsewhere does not redo the work."""
    report, _ = analyze(file_bytes, Options(tolerance=tolerance, include_low=include_low))
    highlighted, problem = None, None
    if report.issues:
        try:
            highlighted = make_highlighted(file_bytes, report)
        except PatchError as error:
            problem = str(error)
    return report, highlighted, problem


if st.button("← Back to start", key="back_to_start"):
    st.switch_page("main.py")
st.title("🩺 Check my file")
st.write("Upload an Excel workbook. I read every sheet and formula and tell you, in plain words, what each sheet does and what looks "
         "wrong. **I never change your numbers**, and nothing is stored on the server.")

uploaded = st.file_uploader("Your workbook (.xlsx)", type=["xlsx", "xlsm"], key="doctor_upload")
if uploaded is None:
    st.info("Upload a workbook to start. I look for typed-in totals that don't match their rows, broken or short formulas, SUMIF "
            "criteria that find nothing, and sheets that disagree about the same figure.")
    st.stop()

file_bytes = uploaded.getvalue()

# --- Edit details (the settings; the defaults are fine) ----------------------------------------------------------------
with st.expander("Edit details"):
    tolerance_percent = st.slider("Treat two figures as equal when they differ by less than (%)", 0.0, 5.0, 0.1, 0.1, key="doctor_tolerance",
                                  help="Rounding: 0.1% means 1,000 and 1,001 count as the same.")
    include_low = st.checkbox("Also show small things (numbers stored as text, hidden data, results that were not recalculated)",
                              value=True, key="doctor_include_low")
    st.caption("Checked: error values and broken references - typed totals and numbers typed over formulas - ranges that stop short "
               "of the data - SUMIF/COUNTIF criteria that match nothing - figures that disagree between sheets.")

try:
    report, highlighted, problem = run_doctor(file_bytes, tolerance_percent / 100, include_low)
except ValueError as error:
    st.error(str(error))
    st.stop()

ui.render_card(report)
ui.render_please_check(report.issues)

# --- downloads -------------------------------------------------------------------------------------------------------------
st.header("Download")
base = uploaded.name.rsplit(".", 1)[0]
if highlighted is not None:
    st.download_button("Download highlighted copy (.xlsx)", highlighted.data, f"{base}_checked.xlsx", XLSX, key="doctor_download_copy",
                       type="primary", help="Your workbook with the problem cells coloured, a note on each, and a 'Doctor report' sheet at the end.")
    st.caption(f"{highlighted.highlighted} cell(s) coloured, {highlighted.notes} note(s) added. Charts and everything else are exactly as they were.")
    for line in highlighted.skipped:
        st.caption("ℹ " + line)
elif problem is not None:
    st.warning(f"I couldn't make a highlighted copy that I'm sure is safe, so I'm not offering one. {problem} "
               "The report above and the text download below have everything.")
elif not report.issues:
    st.caption("Nothing to highlight, so there is no highlighted copy.")
st.download_button("Download the report (.txt)", report_text(report, uploaded.name), f"{base}_report.txt", "text/plain", key="doctor_download_text")
