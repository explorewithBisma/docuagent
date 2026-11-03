"""Helpers for the tests that drive the 'Clean my file' page: the finding tick boxes are the only on/off control of the cleaning steps."""


def box(at, step, sheet=None):
    """The tick box of a finding: step is headers | trim | placeholders | numeric | empty | duplicates | fills | flags | near."""
    wanted = "fill" if step == "fills" else step
    found = [c for c in at.checkbox if c.key and c.key.startswith("fix|") and c.key.split("|")[3].split(":")[0] == wanted
             and (sheet is None or c.key.split("|")[2] == sheet)]
    assert found, f"no finding tick box for '{step}' (boxes: {[c.key for c in at.checkbox]})"
    return found[0]


def boxes(at, step, sheet=None):
    wanted = "fill" if step == "fills" else step
    return [c for c in at.checkbox if c.key and c.key.startswith("fix|") and c.key.split("|")[3].split(":")[0] == wanted
            and (sheet is None or c.key.split("|")[2] == sheet)]


def include_box(at, sheet):
    """The 'Clean this sheet' / 'Include this sheet anyway' tick box of a sheet."""
    return next(c for c in at.checkbox if c.key and c.key.startswith("include|") and c.key.split("|")[2] == sheet)
