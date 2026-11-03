"""
findings_ui.py
---------------
The screen parts of "Here's what I found" (step 2 of the Clean my file page): one tab per sheet with the findings as tick boxes, a preview with the problem
cells marked, and the helpers that turn the ticks into cleaning steps and into a saved recipe. The ticks are the ONLY on/off control of the cleaning steps.

Every tick box has a key  fix|<file id>|<sheet>|<finding key>  (and include|<file id>|<sheet> for "clean this sheet"), so they reset for a new file.
"""

import streamlit as st

from report import display_frame, styled_findings_preview

SEP = "|"
STEP_KEYS = ("headers", "trim", "placeholders", "numeric", "empty", "duplicates", "fills", "flags")
LEGEND = ("Marked in the preview: yellow = extra spaces · grey = placeholder · green = can be calculated · red border = unusual value · "
          "crossed-out row = will be removed.")


def box_key(file_id: str, sheet: str, finding_key: str) -> str:
    return SEP.join(("fix", file_id, sheet, finding_key))


def include_key(file_id: str, sheet: str) -> str:
    return SEP.join(("include", file_id, sheet))


def render_sheet(file_id: str, name: str, df, found, multi_sheet: bool, registry: dict) -> dict:
    """Draw one sheet's tab. Returns {"clean": bool, "steps": {step: bool}, "fill_labels": [...], "review": bool}.
    `registry` collects {step: [tick box keys]} so a recipe can set / read them."""
    st.caption(f"{found.rows:,} rows · {found.columns} columns")
    if multi_sheet and not found.is_data:
        st.info(f"**Not a data sheet - kept exactly as is.** ({found.why[0].upper() + found.why[1:]}.)")
        include = st.checkbox("Include this sheet in the cleaning anyway", value=False, key=include_key(file_id, name))
    elif multi_sheet:
        include = st.checkbox("Clean this sheet", value=True, key=include_key(file_id, name))
    else:
        include = True
    if not include:
        st.dataframe(display_frame(df.head(10)), hide_index=True)
        st.caption("This sheet is kept exactly as you uploaded it.")
        return {"clean": False, "steps": {}, "fill_labels": [], "review": False}

    steps = {step: True for step in STEP_KEYS}                     # a step with nothing found stays on (it has nothing to do)
    fill_labels, review = [], False
    flag_boxes = []
    if not found.findings:
        st.success("Nothing to fix here - this sheet already looks clean.")
    for finding in found.findings:
        key = box_key(file_id, name, finding.key)
        start = {} if key in st.session_state else {"value": finding.default_on}          # (a recipe may have set it already)
        ticked = st.checkbox(finding.text, key=key, help=finding.help or None, **start)
        registry.setdefault(finding.step, []).append(key)
        if finding.step == "fills":
            if ticked:
                fill_labels.append(finding.label)
            continue
        if finding.step == "near":
            review = review or ticked
            continue
        steps[finding.step] = ticked
    styler, shown, total = styled_findings_preview(df, found.findings)
    st.dataframe(styler, hide_index=True)
    st.caption(f"Showing {shown:,} of {total:,} rows (rows with a problem first). " + LEGEND)
    return {"clean": True, "steps": steps, "fill_labels": fill_labels, "review": review}


def recipe_steps(choices: dict, registry: dict) -> dict:
    """The cleaning settings as they are ticked now, for saving in a recipe: a step is on unless a tick box for it is off on some sheet."""
    out = {}
    for step in STEP_KEYS:
        keys = registry.get(step, [])
        out[step] = all(st.session_state.get(key, True) for key in keys) if keys else True
    return out


def apply_recipe_steps(steps: dict, registry: dict) -> None:
    """Set the tick boxes from a recipe's settings. Used as a button callback, so it runs before the boxes are drawn."""
    for step, keys in registry.items():
        if step in steps and step != "near":
            for key in keys:
                st.session_state[key] = bool(steps[step])
