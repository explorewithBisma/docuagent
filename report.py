"""
report.py
----------
Builds the human-readable cleaning summary: both the plain-text version
(for the downloadable .txt report) and the Streamlit panel version shown
on the page.
"""

import streamlit as st


def format_stat(value) -> str:
    """Turn a stat value into readable text: None means the step was skipped."""
    return "skipped (step turned off)" if value is None else str(value)


def _format_rule_outcome_line(outcome: dict) -> str:
    """One line describing what an approved AI-suggested rule actually did."""
    rule = outcome["rule"]
    if "error" in outcome:
        return f'  - "{rule["description"]}" -> SKIPPED ({outcome["error"]})'
    stats = outcome["stats"]
    return f'  - "{rule["description"]}" -> converted {stats["converted"]}, failed {stats["failed"]}'


def build_report_text(file_name: str, sheet_stats: dict, rule_outcomes_by_sheet: dict = None) -> str:
    """Build the plain-text version of the cleaning report (used for the
    downloadable .txt file). `rule_outcomes_by_sheet` is optional - when not
    given (or empty for a sheet), the report looks exactly like it did before
    AI-suggested rules existed."""
    lines = [f"Cleaning Report for: {file_name}", "=" * 50]
    show_sheet_names = len(sheet_stats) > 1  # only label sheets if there's more than one

    for sheet_name, info in sheet_stats.items():
        if show_sheet_names:
            lines.append("")
            lines.append(f"Sheet: {sheet_name}")
            lines.append("-" * 50)

        if not info["cleaned"]:
            lines.append("Not cleaned - this sheet was left exactly as uploaded.")
            continue

        stats = info["stats"]
        lines.append(f"Duplicate rows removed: {format_stat(stats['duplicates_removed'])}")
        lines.append(f"Fully empty rows removed: {format_stat(stats['empty_rows_removed'])}")
        lines.append(f"Fully empty columns removed: {format_stat(stats['empty_columns_removed'])}")
        lines.append(f"Text cells trimmed: {format_stat(stats['cells_trimmed'])}")

        if stats["header_renames"]:
            lines.append("Column headers renamed:")
            for old_name, new_name in stats["header_renames"]:
                lines.append(f'  "{old_name}" -> "{new_name}"')
        else:
            lines.append("Column headers renamed: none")

        rule_outcomes = (rule_outcomes_by_sheet or {}).get(sheet_name)
        if rule_outcomes:
            lines.append("AI-suggested rules applied:")
            for outcome in rule_outcomes:
                lines.append(_format_rule_outcome_line(outcome))

    return "\n".join(lines)


def render_summary_panel(sheet_stats: dict, rule_outcomes_by_sheet: dict = None):
    """Show the same information as build_report_text(), but as Streamlit
    widgets on the page instead of plain text. `rule_outcomes_by_sheet` is
    optional - when not given, this looks exactly like it did before
    AI-suggested rules existed."""
    show_sheet_names = len(sheet_stats) > 1

    for sheet_name, info in sheet_stats.items():
        section = st.expander(f"Sheet: {sheet_name}", expanded=True) if show_sheet_names else st.container()

        with section:
            if not info["cleaned"]:
                st.write("Not cleaned - this sheet was left exactly as uploaded.")
                continue

            stats = info["stats"]
            st.write(f"- Duplicate rows removed: {format_stat(stats['duplicates_removed'])}")
            st.write(f"- Fully empty rows removed: {format_stat(stats['empty_rows_removed'])}")
            st.write(f"- Fully empty columns removed: {format_stat(stats['empty_columns_removed'])}")
            st.write(f"- Text cells trimmed: {format_stat(stats['cells_trimmed'])}")

            if stats["header_renames"]:
                st.write("- Column headers renamed:")
                for old_name, new_name in stats["header_renames"]:
                    st.write(f'  &nbsp;&nbsp;"{old_name}" → "{new_name}"', unsafe_allow_html=True)
            else:
                st.write("- Column headers renamed: none")

            rule_outcomes = (rule_outcomes_by_sheet or {}).get(sheet_name)
            if rule_outcomes:
                st.write("- AI-suggested rules applied:")
                for outcome in rule_outcomes:
                    rule = outcome["rule"]
                    if "error" in outcome:
                        st.write(f'  &nbsp;&nbsp;"{rule["description"]}" → skipped ({outcome["error"]})',
                                 unsafe_allow_html=True)
                    else:
                        rule_stats = outcome["stats"]
                        st.write(
                            f'  &nbsp;&nbsp;"{rule["description"]}" → converted {rule_stats["converted"]}, '
                            f'failed {rule_stats["failed"]}',
                            unsafe_allow_html=True,
                        )
