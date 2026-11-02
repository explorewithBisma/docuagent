"""
toolkit/ui.py
--------------
The screen parts every tool shares, so they all look and behave the same: the "What I found" card (facts with their source and
how sure the tool is), the "Please check" list (most important first), and the reset when a new file is uploaded.
"""

import streamlit as st

from theme import pill

BADGE = {"high": "🟢 sure", "medium": "🟡 probably", "low": "🔴 guess"}                  # the words and emoji; shown as coloured pills (assets/theme.css)
BADGE_KIND = {"high": "sure", "medium": "probably", "low": "guess"}
SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "🔵"}
SEVERITY_PILL = {"high": "High", "medium": "Medium", "low": "Low"}
SEVERITY_WORD = {"high": "serious", "medium": "worth checking", "low": "small"}
SHOWN = 8


def confidence_pill(confidence: str) -> str:
    """HTML for 'sure / probably / guess' as a coloured pill ('' for an unknown level)."""
    return pill(BADGE[confidence], BADGE_KIND[confidence]) if confidence in BADGE else ""


def severity_pill(severity: str) -> str:
    """HTML for 'High / Medium / Low' as a coloured pill (red / amber / blue)."""
    return pill(SEVERITY_PILL[severity], severity)


def reset_on_new_file(file_id: str, keys) -> bool:
    """Forget the state of the previous file when another one is uploaded. True when the file changed."""
    if st.session_state.get("_file_id") != file_id:
        for key in keys:
            st.session_state[key] = None
        st.session_state["_file_id"] = file_id
        return True
    return False


def render_card(report, title: str = "What I found", summary: str = None):
    """The 'What I found' card: one line per fact with its source and a confidence badge, then the notes."""
    st.header(title)
    with st.container(border=True):
        for fact in report.facts:
            left, middle = st.columns([2, 5])
            left.markdown(f"**{fact.label}**")
            middle.markdown(f"{fact.value} &nbsp; {confidence_pill(fact.confidence)}", unsafe_allow_html=True)
            middle.caption(f"from {fact.source}")
        counts = report.counts()
        if report.issues:
            st.markdown(f"**{len(report.issues)} thing(s) to check:** " + ", ".join(
                f"{counts[s]} {SEVERITY_WORD[s]}" for s in ("high", "medium", "low") if counts[s]))
        elif summary:
            st.info(summary)
        else:
            st.success("No problems were found.")
        for note in report.notes:
            st.caption("ℹ " + note)


def _issue(issue):
    st.markdown(f"{severity_pill(issue.severity)} &nbsp; **{issue.title}**", unsafe_allow_html=True)
    st.caption(issue.detail)
    if issue.where:
        st.caption(f"Where: {issue.where}")
    if issue.evidence:
        st.code(issue.evidence, language=None)
    if issue.fix:
        st.caption(f"💡 {issue.fix}")


def render_please_check(issues, limit: int = SHOWN):
    """The 'Please check' list, most important first; the rest are in an expander (nothing is hidden)."""
    if not issues:
        return
    st.header("Please check")
    for issue in issues[:limit]:
        with st.container(border=True):
            _issue(issue)
    if len(issues) > limit:
        with st.expander(f"{len(issues) - limit} more"):
            for issue in issues[limit:]:
                _issue(issue)
                st.divider()
