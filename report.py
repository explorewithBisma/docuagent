"""
report.py
----------
Builds the human-readable cleaning summary: both the plain-text version
(for the downloadable .txt report) and the Streamlit panel version shown
on the page.
"""

import pandas as pd
import streamlit as st


def format_stat(value) -> str:
    """Turn a stat value into readable text: None means the step was skipped."""
    return "skipped (step turned off)" if value is None else str(value)


def with_missing_total(profile: pd.DataFrame) -> pd.DataFrame:
    """Add a 'Missing total' column (blank cells + placeholder cells) to a
    quality profile, so before/after are easy to compare at a glance."""
    shown = profile.copy()
    shown.insert(4, "Missing total", shown["Blank"] + shown["Placeholders"])
    return shown


def _quality_lines(quality: dict) -> list:
    """Report lines for the before/after data-quality tables of one sheet."""
    before, after = with_missing_total(quality["before"]), with_missing_total(quality["after"])
    lines = ["", "Data quality - before cleaning:"]
    lines += ["  " + line for line in before.to_string(index=False).splitlines()]
    lines += ["", "Data quality - after cleaning:"]
    lines += ["  " + line for line in after.to_string(index=False).splitlines()]
    lines.append(f"Missing cells: {int(before['Missing total'].sum())} before -> {int(after['Missing total'].sum())} after")
    return lines


def _placeholder_lines(stats: dict) -> list:
    """Report lines for the placeholder step (empty list if the step was not
    part of this run at all, e.g. stats from before the step existed)."""
    if "placeholders" not in stats:
        return []
    info = stats["placeholders"]
    if info is None:
        return ["Placeholders converted to missing: skipped (step turned off)"]

    lines = [f"Placeholders converted to missing: {info['total']}"]
    for column, counts in info["by_column"].items():
        lines.append(f"  {column}: " + ", ".join(f"{name} {count}" for name, count in counts.items()))
    return lines


def _numeric_lines(stats: dict) -> list:
    """Report lines for the numbers-stored-as-text step."""
    if "numeric" not in stats:
        return []
    info = stats["numeric"]
    if info is None:
        return ["Columns converted to numbers: skipped (step turned off)"]

    if info["converted"]:
        names = ", ".join(f"{item['column']} ({item['kind']})" for item in info["converted"])
        lines = [f"Columns converted to numbers: {len(info['converted'])} - {names}"]
    else:
        lines = ["Columns converted to numbers: none"]
    for item in info["converted"]:
        if item["invalid"]:
            examples = ", ".join(f"'{example}'" for example in item["examples"])
            lines.append(f"  {item['column']}: {item['invalid']} value(s) are not numbers and were kept as text (e.g. {examples})")
    for column, reason in info["skipped"].items():
        lines.append(f"  {column}: left as text ({reason})")
    return lines


def _fill_lines(stats: dict) -> list:
    """Report lines for values filled in from other columns."""
    if "fills" not in stats:
        return []
    info = stats["fills"]
    if info is None:
        return ["Values filled from other columns: skipped (step turned off)"]

    calculated, looked_up = info["calculated"], info["lookup"]
    if not calculated and not looked_up:
        return ["Values filled from other columns: none"]

    lines = [f"Values filled from other columns: {sum(calculated.values()) + sum(looked_up.values())}"]
    if calculated:
        lines.append("  Calculated exactly: " + ", ".join(f"{column} {count}" for column, count in calculated.items()))
    if looked_up:
        lines.append("  Inferred from a lookup in this file: " + ", ".join(f"{column} {count}" for column, count in looked_up.items()))
    return lines


def _format_rule_outcome_line(outcome: dict) -> str:
    """One line describing what an approved AI-suggested rule actually did."""
    rule = outcome["rule"]
    if "error" in outcome:
        return f'  - "{rule["description"]}" -> SKIPPED ({outcome["error"]})'
    stats = outcome["stats"]
    return f'  - "{rule["description"]}" -> converted {stats["converted"]}, failed {stats["failed"]}'


def build_report_text(file_name: str, sheet_stats: dict, rule_outcomes_by_sheet: dict = None,
                      quality_by_sheet: dict = None) -> str:
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
        lines.extend(_placeholder_lines(stats))
        lines.extend(_numeric_lines(stats))
        lines.extend(_fill_lines(stats))

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

        quality = (quality_by_sheet or {}).get(sheet_name)
        if quality:
            lines.extend(_quality_lines(quality))

    return "\n".join(lines)


def render_quality_panel(quality_by_sheet: dict):
    """Show the before/after data-quality tables on the page, side by side."""
    show_sheet_names = len(quality_by_sheet) > 1

    for sheet_name, quality in quality_by_sheet.items():
        before, after = with_missing_total(quality["before"]), with_missing_total(quality["after"])
        section = st.expander(f"Sheet: {sheet_name}", expanded=True) if show_sheet_names else st.container()
        with section:
            st.caption(
                f"Missing cells (blank + placeholder): {int(before['Missing total'].sum())} before → "
                f"{int(after['Missing total'].sum())} after"
            )
            left, right = st.columns(2)
            with left:
                st.write("**Before cleaning**")
                st.dataframe(before, hide_index=True, use_container_width=True)
            with right:
                st.write("**After cleaning**")
                st.dataframe(after, hide_index=True, use_container_width=True)


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
            for line in _placeholder_lines(stats) + _numeric_lines(stats) + _fill_lines(stats):
                st.write(("- " + line) if not line.startswith("  ") else ("&nbsp;&nbsp;&nbsp;&nbsp;" + line.strip()),
                         unsafe_allow_html=True)

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
