"""
report.py
----------
Builds the human-readable cleaning summary: both the plain-text version
(for the downloadable .txt report) and the Streamlit panel version shown
on the page.
"""

import pandas as pd
import streamlit as st

from changes import COLOR_MEANINGS, REASON_COLORS, REASON_LABELS, reason_text, summarize
from xlsx_export import number_format_for

PREVIEW_ROWS = 200  # rows shown in the on-page preview
CHANGES_ROWS = 1000  # rows shown in the on-page change table

_PYTHON_FORMATS = {"0": "{:.0f}", "0.00": "{:.2f}", "#,##0.00": "{:,.2f}"}  # Excel number format -> display format


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


def _row_numbers(numbers: list, limit: int = 30) -> str:
    shown = ", ".join(str(number) for number in numbers[:limit])
    return shown + (f" ... and {len(numbers) - limit} more" if len(numbers) > limit else "")


def _removed_lines(rows: list) -> list:
    """Report lines naming the rows that were removed (row numbers as in the uploaded file)."""
    lines = []
    for kind, label in (("duplicate", "Removed duplicate rows"), ("empty", "Removed fully empty rows")):
        numbers = [row["original_row"] for row in rows if row["kind"] == kind]
        if numbers:
            lines.append(f"{label} (row numbers in your file): {_row_numbers(numbers)}")
    if lines:
        lines.append("  Their full original content is on the 'Removed Rows' sheet of the Excel download.")
    return lines


def _outcome_stats_text(stats: dict) -> str:
    """'converted 5, failed 1' - or, for value mapping, how many values were not in the map."""
    if "unmapped" in stats:
        return f"converted {stats['converted']}, not in the map (left unchanged) {stats['unmapped']}"
    return f"converted {stats['converted']}, failed {stats['failed']}"


def _format_rule_outcome_line(outcome: dict) -> str:
    """One line describing what an approved AI-suggested rule actually did."""
    rule = outcome["rule"]
    if "error" in outcome:
        return f'  - "{rule["description"]}" -> SKIPPED ({outcome["error"]})'
    return f'  - "{rule["description"]}" -> {_outcome_stats_text(outcome["stats"])}'


def build_report_text(file_name: str, sheet_stats: dict, rule_outcomes_by_sheet: dict = None,
                      quality_by_sheet: dict = None, removed_by_sheet: dict = None) -> str:
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
        lines.extend(_removed_lines((removed_by_sheet or {}).get(sheet_name, [])))

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
                st.dataframe(before, hide_index=True)
            with right:
                st.write("**After cleaning**")
                st.dataframe(after, hide_index=True)


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
                        st.write(
                            f'  &nbsp;&nbsp;"{rule["description"]}" → {_outcome_stats_text(outcome["stats"])}',
                            unsafe_allow_html=True,
                        )


# ---------------------------------------------------------------------------
# METRIC CARDS, COLOR-CODED PREVIEW AND CHANGE TABLE (the on-page "what changed" view)
# ---------------------------------------------------------------------------

def summary_metrics(sheet_stats: dict, cleaned_sheets: dict, original_rows: dict, quality_by_sheet: dict,
                    records_by_sheet: dict) -> dict:
    """The headline numbers across every sheet that was cleaned."""
    cleaned = [name for name, info in sheet_stats.items() if info["cleaned"]]
    placeholders = filled_exact = filled_inferred = 0
    for name in cleaned:
        stats = sheet_stats[name]["stats"]
        placeholders += (stats.get("placeholders") or {}).get("total", 0)
        filled_exact += sum(((stats.get("fills") or {}).get("calculated") or {}).values())
        filled_inferred += sum(((stats.get("fills") or {}).get("lookup") or {}).values())

    missing_before = sum(int((q["before"]["Blank"] + q["before"]["Placeholders"]).sum()) for q in quality_by_sheet.values())
    missing_after = sum(int((q["after"]["Blank"] + q["after"]["Placeholders"]).sum()) for q in quality_by_sheet.values())
    return {
        "rows_before": sum(original_rows.get(name, 0) for name in cleaned),
        "rows_after": sum(len(cleaned_sheets[name]) for name in cleaned),
        "placeholders": placeholders,
        "filled": filled_exact + filled_inferred,
        "filled_exact": filled_exact,
        "filled_inferred": filled_inferred,
        "ai_changes": sum(summarize(records_by_sheet.get(name, []))["ai_rule"] for name in cleaned),
        "missing_before": missing_before,
        "missing_after": missing_after,
    }


def render_metric_cards(metrics: dict) -> None:
    """Four cards: rows, placeholders removed, cells filled, missing before -> after."""
    rows, placeholders, filled, missing = st.columns(4)
    removed = metrics["rows_before"] - metrics["rows_after"]
    rows.metric("Rows", f"{metrics['rows_after']:,}", delta=f"{removed:,} removed" if removed else None,
                delta_color="off", help="Rows in the cleaned file (duplicate and completely empty rows are removed).")
    placeholders.metric("Placeholders removed", f"{metrics['placeholders']:,}",
                        help="Cells that said ERROR, UNKNOWN, N/A ... and were cleared to a real blank.")
    filled.metric("Cells filled", f"{metrics['filled']:,}",
                  help=f"{metrics['filled_exact']:,} calculated exactly from other values in the row, "
                       f"{metrics['filled_inferred']:,} inferred from a lookup pattern in your file.")
    change = metrics["missing_after"] - metrics["missing_before"]
    missing.metric("Missing cells", f"{metrics['missing_before']:,} → {metrics['missing_after']:,}",
                   delta=f"{change:+,}" if change else None, delta_color="inverse",
                   help="Blank cells plus placeholder cells, before and after cleaning (fewer is better).")


def legend_html() -> str:
    """Colored chips explaining the highlight colors (same colors as the Excel download)."""
    chips = "".join(
        f'<span style="background:#{REASON_COLORS[reason]};color:#1f1f1f;padding:2px 10px;border-radius:4px;'
        f'margin-right:8px;font-size:0.85em;display:inline-block;margin-bottom:4px">{COLOR_MEANINGS[reason].split(" - ")[0]}: '
        f'{COLOR_MEANINGS[reason].split(" - ")[1]}</span>'
        for reason in REASON_COLORS
    )
    return chips


def styled_preview(df: pd.DataFrame, records: list, only_changed: bool = False, limit: int = PREVIEW_ROWS):
    """A pandas Styler of the first `limit` rows (or the first `limit` rows
    that contain a change) with changed cells in the same colors as the Excel
    download. Returns (styler, rows_shown, rows_available)."""
    changed_rows = sorted({record["position"] for record in records})
    available = changed_rows if only_changed else list(range(len(df)))
    positions = available[:limit]
    shown = df.iloc[positions]

    css = pd.DataFrame("", index=shown.index, columns=shown.columns)
    wanted = set(positions)
    for record in records:
        color = REASON_COLORS.get(record["reason"])
        if color and record["position"] in wanted:
            css.at[df.index[record["position"]], record["column"]] = f"background-color: #{color}; color: #1f1f1f"

    styler = shown.style.format(na_rep="").apply(lambda _: css, axis=None)
    for column in shown.columns:
        number_format = number_format_for(column, df[column])
        if number_format in _PYTHON_FORMATS:
            styler = styler.format(_PYTHON_FORMATS[number_format], subset=[column], na_rep="")
    return styler, len(positions), len(available)


def removed_rows_dataframe(columns: list, rows: list) -> pd.DataFrame:
    """The removed rows as a table: Original row, Reason, then every original column."""
    names = ["Original row", "Reason"] + [str(column) for column in columns]
    data = [[row["original_row"], row["reason"]] + ["" if (not isinstance(v, str) and pd.isna(v)) else v for v in row["values"]]
            for row in rows]
    return pd.DataFrame(data, columns=names)


def changes_dataframe(records_by_sheet: dict) -> pd.DataFrame:
    """Every changed cell as a table: Sheet, Row, Column, Old value, New value, Reason."""
    rows = []
    for sheet_name, records in records_by_sheet.items():
        for record in records:
            old, new = record["old"], record["new"]
            rows.append({
                "Sheet": sheet_name, "Row": record["row"], "Column": record["column"],
                "Old value": "(blank)" if (not isinstance(old, str) and pd.isna(old)) or old == "" else str(old),
                "New value": "(blank)" if (not isinstance(new, str) and pd.isna(new)) or new == "" else str(new),
                "Reason": reason_text(record), "_kind": record["reason"],
            })
    return pd.DataFrame(rows, columns=["Sheet", "Row", "Column", "Old value", "New value", "Reason", "_kind"])


def totals_by_reason(records_by_sheet: dict) -> pd.DataFrame:
    """A small table: change type and how many cells."""
    counts = {reason: 0 for reason in REASON_LABELS}
    for records in records_by_sheet.values():
        for reason, number in summarize(records).items():
            counts[reason] += number
    return pd.DataFrame({"Change type": [REASON_LABELS[r] for r in counts], "Cells": list(counts.values())})
