"""
doctor/rootcause.py
--------------------
One cause, one finding. When a summary cell is wrong only because it adds up cells that come from another flagged cell (a Dashboard
total that sums figures read from the Monthly Summary), the user does not need two findings: they need one that says where the gap
starts and where else the same number shows up.

A finding is joined to an upstream one when
  * both are "total differs from the transaction list" findings against the same list,
  * they show the same number, and
  * the later cell's formulas lead (through any chain of references) to the sheet of the earlier one, but not the other way round.
"""

from doctor import formulas as fm
from doctor.context import money

MAX_CELLS_VISITED = 5000


def precedent_sheets(ctx, sheet_name: str, row: int, col: int) -> set:
    """The sheets a cell's formula depends on, following references through other formulas (including its own sheet)."""
    seen, sheets, stack = set(), {sheet_name}, [(sheet_name, row, col)]
    while stack and len(seen) < MAX_CELLS_VISITED:
        name, r, c = stack.pop()
        if (name, r, c) in seen:
            continue
        seen.add((name, r, c))
        sheet = ctx.wb.sheets.get(name)
        cell = sheet.get(r, c) if sheet else None
        if cell is None or not cell.is_formula:
            continue
        tree = ctx.tree(cell)
        if tree is None:
            continue
        for ref in fm.refs(tree):
            target = ref.sheet or name
            if target not in ctx.wb.sheets:
                continue
            sheets.add(target)
            last = ref.r2 if ref.r2 is not None else ctx.wb.sheets[target].max_row
            if ref.kind in ("cell", "range", "col"):
                for rr in range(ref.r1, last + 1):
                    for cc in range(ref.c1, ref.c2 + 1):
                        stack.append((target, rr, cc))
    return sheets


def merge_same_root_cause(ctx) -> int:
    """Join 'total differs' findings that share a cause; returns how many findings were absorbed into another."""
    candidates = [i for i in ctx.issues if i.kind == "cross_total" and i.facts.get("against") is not None and len(i.locations) == 1]
    absorbed = 0
    for upstream in list(candidates):
        if upstream not in ctx.issues:
            continue
        for other in list(candidates):
            if other is upstream or other not in ctx.issues:
                continue
            if not ctx.close(upstream.facts["value"], other.facts["value"]) or not ctx.close(upstream.facts["against"], other.facts["against"]):
                continue
            (up_sheet, up_coord), (down_sheet, down_coord) = upstream.locations[0], other.locations[0]
            if up_sheet == down_sheet:
                continue
            up_cell, down_cell = ctx.wb.sheets[up_sheet].cells.get(_rc(up_coord)), ctx.wb.sheets[down_sheet].cells.get(_rc(down_coord))
            if up_cell is None or down_cell is None:
                continue
            down_depends = up_sheet in precedent_sheets(ctx, down_sheet, down_cell.row, down_cell.col)
            up_depends = down_sheet in precedent_sheets(ctx, up_sheet, up_cell.row, up_cell.col)
            if down_depends and not up_depends:
                _join(ctx, upstream, other)
                absorbed += 1
    return absorbed


def _rc(coord: str):
    from toolkit.highlight import _split_coord
    return _split_coord(coord)


def _join(ctx, upstream, downstream):
    value, against = upstream.facts["value"], upstream.facts["against"]
    gap = abs(value - against)
    word = "short by" if value < against else "over by"
    cells = upstream.facts.setdefault("also", [])
    cells.append(f"{downstream.locations[0][0]}!{downstream.locations[0][1]}")
    first = f"{upstream.locations[0][0]}!{upstream.locations[0][1]}"
    others = ", ".join(cells)
    upstream.title = (f"{first} is {word} {money(gap)}; {others} " + ("shows" if len(cells) == 1 else "show") + " the same number")
    upstream.detail = (f"{first} adds up to {money(value)}, but the transaction list ('{upstream.facts['list']}') adds up to {money(against)}, "
                       f"a difference of {money(gap)}. {others} just reads it, so it is {word} the same amount - fixing {first} fixes "
                       "both. Some spending is missing from the summary (for example because a category or date is written differently in the list).")
    upstream.evidence += f";  {others} = {money(downstream.facts['value'])}"
    upstream.locations = upstream.locations + downstream.locations
    ctx.reported.update(downstream.locations)
    ctx.issues.remove(downstream)
