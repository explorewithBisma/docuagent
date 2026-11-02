"""
dedupe_panel.py
----------------
The "Look for near-duplicates" section of "Clean my file". app.py calls render() once; everything else lives here.

Nothing is removed until the user decides: every group starts as "Keep all", and "Accept all high-confidence groups" only
sets the choices - the one-line summary of what will happen is shown before "Apply" does it.
"""

import pandas as pd
import streamlit as st

from dedupe import apply as ap, columns as cols, normalize as nz
from dedupe.analyze import DedupeOptions, KIND_TEXT, find_near_duplicates
from locales import LOCALES
from quality import is_blank
from toolkit.ui import render_card

PAGE = 10
ACTIONS = {"Keep all": ap.KEEP_ALL, "Keep one": ap.KEEP_ONE, "Merge (fill blanks)": ap.MERGE}
ROLE_CHOICES = ["(automatic)", "name", "email", "phone", "id", "company", "address", "dob", "place", "ignore"]
KIND_CHOICES = {"Detect automatically": "auto", "A list of people or companies": "contacts", "A list of transactions": "transactions"}
STRICTNESS = {"Careful - fewer, surer matches": "strict", "Normal": "normal", "Loose - also lone typos": "loose"}
DIFF_COLOR = "background-color: #FFE699"
FILL_COLOR = "background-color: #E4DFEC"


def _sheets_available(cleaned_sheets: dict, change_logs: dict) -> list:
    return [name for name, df in cleaned_sheets.items()
            if len(df) > 1 and (change_logs or {}).get(name) is not None and change_logs[name].original_df is not None]


def _options(sheet: str, df: pd.DataFrame, default_locale: str) -> DedupeOptions:
    """Reads the Edit details widgets (defaults on the first run)."""
    state = st.session_state
    overrides = {}
    for column in df.columns:
        choice = state.get(f"dd_role_{sheet}_{column}", "(automatic)")
        if choice != "(automatic)":
            overrides[column] = choice
    variants_text = state.get("dd_variants")
    return DedupeOptions(
        strictness=STRICTNESS[state.get("dd_strictness", "Normal")],
        locale=state.get("dd_locale", default_locale),
        gmail_rule=bool(state.get("dd_gmail", False)),
        mode=KIND_CHOICES[state.get("dd_kind", "Detect automatically")],
        overrides=overrides,
        variants=nz.parse_variants_text(variants_text) if variants_text is not None else None,
    )


def _edit_details(sheet: str, df: pd.DataFrame, default_locale: str) -> None:
    with st.expander("Edit details"):
        left, middle, right = st.columns(3)
        left.selectbox("How sure must a match be?", list(STRICTNESS), index=1, key="dd_strictness")
        codes = list(LOCALES)
        middle.selectbox("Phone numbers are from", codes, index=codes.index(default_locale) if default_locale in codes else 0,
                         format_func=lambda c: f"{LOCALES[c]['label']} ({c})", key="dd_locale")
        right.checkbox("Treat gmail dots and +tags as the same address", key="dd_gmail")
        st.markdown("**Which columns identify a record?**")
        options = _options(sheet, df, default_locale)
        profile = cols.build_profile(df, options.overrides, options.mode)
        for role in profile.roles:
            name_col, pick_col, why_col = st.columns([2, 2, 5])
            name_col.write(role.column)
            current = options.overrides.get(role.column)
            pick_col.selectbox(role.column, ROLE_CHOICES, index=ROLE_CHOICES.index(current) if current in ROLE_CHOICES else 0,
                               key=f"dd_role_{sheet}_{role.column}", label_visibility="collapsed")
            shown = role.role if role.role != "ignore" else "not used"
            why_col.caption(f"{'(' + shown + ')'} {role.why}" if role.used or role.role != "ignore" else f"not used - {role.why}")
        if "dd_variants" not in st.session_state:
            st.session_state["dd_variants"] = nz.variants_to_text(nz.load_variant_groups())
        st.text_area("Names that count as the same name (one group per line, separated by commas)", key="dd_variants", height=160)


def _row_table(df: pd.DataFrame, group, keep: int, action: str):
    """The group's rows side by side: cells that differ are yellow, blanks a merge would fill are purple."""
    frame = df.iloc[group.rows].copy()
    frame.insert(0, "Row in your file", [int(label) + 2 for label in frame.index])
    frame.insert(1, "Keep?", ["keep" if r == keep else "" for r in group.rows])
    frame = frame.reset_index(drop=True).astype(object)
    fillable = ap.fills_for(df, keep, [r for r in group.rows if r != keep]) if action == ap.MERGE else {}
    keep_at = group.rows.index(keep)

    def colour(column):
        if column.name in ("Row in your file", "Keep?"):
            return [""] * len(column)
        values = {str(v).strip().casefold() for v in column if not is_blank(v)}
        styles = [DIFF_COLOR if len(values) > 1 and not is_blank(v) else "" for v in column]
        if column.name in fillable:
            styles[keep_at] = FILL_COLOR
        return styles

    return frame.style.apply(colour, axis=0)


def _choices() -> dict:
    """{group id: (action name, position of the row to keep)} - kept apart from the widgets, so choices on other pages survive."""
    return st.session_state.setdefault("dd_choices", {})


def _decisions(groups: list) -> dict:
    out = {}
    for g in groups:
        action, keep = _choices().get(g.id, ("Keep all", g.keep))
        out[g.id] = ap.Decision(ACTIONS[action], keep if keep in g.rows else g.keep)
    return out


def _accept_all(groups: list) -> None:
    for g in groups:
        if g.level == "high":
            _choices()[g.id] = ("Merge (fill blanks)", g.keep)
    st.session_state["dd_gen"] = st.session_state.get("dd_gen", 0) + 1      # new widget keys: the widgets pick the new choices up


def _reset_choices() -> None:
    _choices().clear()
    st.session_state["dd_gen"] = st.session_state.get("dd_gen", 0) + 1


def _group_ui(df: pd.DataFrame, group) -> None:
    badge = "🟢 High" if group.level == "high" else "🟡 Maybe"
    with st.container(border=True):
        st.markdown(f"**Group {group.id}** · {badge} · {len(group.rows)} rows")
        for row, why in group.reasons.items():
            st.caption(f"Row {int(df.index[row]) + 2}: {why}")
        for warning in group.warnings:
            st.warning(warning)
        gen = st.session_state.get("dd_gen", 0)
        action_now, keep_now = _choices().get(group.id, ("Keep all", group.keep))
        left, right = st.columns([3, 2])
        action_name = left.radio("What to do with this group", list(ACTIONS), index=list(ACTIONS).index(action_now),
                                 key=f"dd_a_{gen}_{group.id}", horizontal=True)
        position = {int(df.index[r]) + 2: r for r in group.rows}
        keep_row = right.selectbox("Row to keep", list(position), index=list(position.values()).index(keep_now),
                                   key=f"dd_k_{gen}_{group.id}", disabled=ACTIONS[action_name] == ap.KEEP_ALL,
                                   help="The most complete row is chosen first.")
        _choices()[group.id] = (action_name, position[keep_row])
        st.dataframe(_row_table(df, group, position[keep_row], ACTIONS[action_name]), hide_index=True)


def render(cleaned_sheets: dict, change_logs: dict, default_locale: str, after_apply) -> None:
    """The whole section. `after_apply()` is called once the cleaned data has changed (rebuild the report, bump the data version)."""
    sheets = _sheets_available(cleaned_sheets, change_logs)
    st.header("Near-duplicates")
    st.caption("Rows that are probably the same person or record even though they are not identical. Nothing is removed unless you say so.")
    if not sheets:
        st.info("There is nothing to compare yet - clean a file with at least two rows first.")
        return
    message = st.session_state.pop("dd_message", None)
    if message:
        st.success(message)

    sheet = st.selectbox("Sheet", sheets, key="dd_sheet") if len(sheets) > 1 else sheets[0]
    df = cleaned_sheets[sheet]
    version = st.session_state.get("data_version") or 0
    started = st.session_state.get("dd_started") == (sheet, version)
    if st.button("Look again" if started else "Look for near-duplicates", key="dd_go"):
        st.session_state["dd_started"] = (sheet, version)
        _reset_choices()
        started = True
    _edit_details(sheet, df, default_locale)
    if not started:
        return

    options = _options(sheet, df, default_locale)
    cache_key = (sheet, version, options.key())
    cache = st.session_state.get("dd_cache")
    if not cache or cache["key"] != cache_key:
        with st.spinner("Comparing rows..."):
            result = find_near_duplicates(df, options)
        st.session_state["dd_cache"] = {"key": cache_key, "result": result}
        _reset_choices()
    result = st.session_state["dd_cache"]["result"]

    render_card(result.report, "What I found", summary=result.summary)
    unsure = result.profile.kind_confidence == "low"
    st.radio("Is this a list of people or a list of transactions?" + (" (not sure - please check)" if unsure else ""), list(KIND_CHOICES),
             key="dd_kind", horizontal=True,
             help="A list of people: the same person on two rows is a duplicate. A list of transactions: a customer legitimately appears "
                  "on many rows, so whole records are compared. Changing this looks again straight away.")
    groups = result.groups
    if not groups:
        return

    top, _ = st.columns([3, 2])
    top.button("Accept all high-confidence groups", on_click=_accept_all, args=(groups,), key="dd_accept_all",
               help="Keeps the most complete row of each high-confidence group, fills its blanks from the others (a value is never "
                    "overwritten) and removes the rest - after you check the summary below and press Apply.")
    pages = (len(groups) - 1) // PAGE + 1
    page = st.number_input("Page", 1, pages, 1, key="dd_page") if pages > 1 else 1
    for group in groups[(page - 1) * PAGE:page * PAGE]:
        _group_ui(df, group)
    decisions = _decisions(groups)
    effect = ap.preview(df, groups, decisions)
    st.divider()
    if effect["groups"] == 0:
        st.caption("Choose what to do with a group (or press \"Accept all high-confidence groups\"), then apply.")
        return
    st.info(f"{effect['rows_removed']:,} rows will be removed, {effect['cells_filled']:,} blank cells filled "
            f"({effect['groups']:,} group{'s' if effect['groups'] != 1 else ''}). The removed rows go to the Removed Rows sheet.")
    if st.button(f"Apply to {effect['groups']:,} group{'s' if effect['groups'] != 1 else ''}", type="primary", key="dd_apply"):
        new_df = ap.apply_decisions(df, change_logs[sheet], groups, decisions)
        st.session_state.cleaned_sheets = {**cleaned_sheets, sheet: new_df}
        st.session_state.pop("dd_cache", None)
        st.session_state.pop("dd_started", None)
        _reset_choices()
        st.session_state["dd_message"] = (f"Done: {effect['rows_removed']:,} rows removed and {effect['cells_filled']:,} blank cells "
                                          "filled. See the Changes tab and the Removed Rows sheet.")
        after_apply()
        st.rerun()
