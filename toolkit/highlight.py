"""
toolkit/highlight.py
---------------------
Makes a highlighted COPY of a workbook for any tool that has findings: the cells with a finding get a colour (by how serious it
is) and a note with the reason, and a "Doctor report" sheet at the end lists every finding with a link to its cell.

The copy is made by patching the .xlsx package as text, not by loading and re-saving it, so everything we do not touch -
charts, images, macros, conditional formats, pivot tables, other sheets - stays BYTE-IDENTICAL. What changes:
  * styles.xml          three fills (and copies of the styles of the highlighted cells that use them), one bold font
  * the highlighted sheets: the style number (s="..") of each highlighted cell, and a link to their notes
  * new parts           the notes (commentsN.xml + a VML drawing) and the report sheet
  * the package lists   [Content_Types].xml, workbook.xml (the new sheet, last), the relationship files

Nothing is ever offered unless it has been CHECKED: `validate_patched` re-reads the result and confirms that every cell value and
formula is unchanged, that every untouched part is identical, and that the package is consistent. If patching or checking fails
for ANY reason a PatchError is raised and the caller shows the report only.
"""

import html
import io
import re
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_from_string

SEVERITY_FILL = {"high": "FFFFC7CE", "medium": "FFFFEB9C", "low": "FFDDEBF7"}
SEVERITY_ORDER = ("high", "medium", "low")
AUTHOR = "Excel Doctor"
REPORT_SHEET = "Doctor report"

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
CT_COMMENTS = "application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml"
CT_SHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
CT_VML = "application/vnd.openxmlformats-officedocument.vmlDrawing"
_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


class PatchError(Exception):
    """The highlighted copy could not be made, or did not pass the checks. The message is for the user."""


@dataclass
class Annotation:
    sheet: str
    coord: str                  # "B5"
    severity: str               # "high" | "medium" | "low"
    message: str


@dataclass
class ReportRow:
    number: int
    severity: str
    where: str
    title: str
    detail: str
    evidence: str
    fix: str
    link: tuple = None          # (sheet, coord) for the hyperlink


@dataclass
class PatchResult:
    data: bytes
    highlighted: int = 0                                # cells that got a colour
    notes: int = 0                                      # cells that got a note
    skipped: list = field(default_factory=list)         # plain sentences: what could not be marked, and why
    touched: set = field(default_factory=set)           # the parts of the package that differ from the original


def clean(text) -> str:
    return _ILLEGAL.sub("", str(text))


def _attrs(start_tag: str) -> dict:
    return dict(re.findall(r'([\w:.-]+)="([^"]*)"', start_tag))


def _split_coord(coord: str):
    letters, row = coordinate_from_string(coord)
    return row, column_index_from_string(letters)


# ---------------------------------------------------------------------------------------------------------------
# PACKAGE MAP
# ---------------------------------------------------------------------------------------------------------------
def _sheet_parts(workbook_xml: str, rels_xml: str) -> dict:
    """{sheet name: part path} from workbook.xml and its relationships."""
    targets = {}
    for tag in re.findall(r"<Relationship\b[^>]*>", rels_xml):
        a = _attrs(tag)
        if "Id" in a and "Target" in a:
            target = a["Target"]
            targets[a["Id"]] = target.lstrip("/") if target.startswith("/") else "xl/" + target
    parts = {}
    for tag in re.findall(r"<sheet\b[^>]*/>", workbook_xml):
        a = _attrs(tag)
        rid = next((v for k, v in a.items() if k.endswith(":id") or k == "id"), None)
        if "name" in a and rid in targets:
            parts[html.unescape(a["name"])] = targets[rid]
    return parts


def _next_number(names, pattern: str) -> int:
    numbers = [int(m.group(1)) for n in names if (m := re.match(pattern, n))]
    return max(numbers, default=0) + 1


# ---------------------------------------------------------------------------------------------------------------
# STYLES
# ---------------------------------------------------------------------------------------------------------------
def _patch_styles(styles: str, needed: set):
    """-> (new styles text, {(old xf index, severity): new xf index}, {"header": xf, "wrap": xf}).
    `needed` = {(old xf index, severity)}."""
    fills = re.search(r'<fills\b[^>]*count="(\d+)"[^>]*>(.*?)</fills>', styles, re.S)
    xfs = re.search(r'<cellXfs\b[^>]*count="(\d+)"[^>]*>(.*?)</cellXfs>', styles, re.S)
    fonts = re.search(r'<fonts\b[^>]*count="(\d+)"[^>]*>(.*?)</fonts>', styles, re.S)
    if not (fills and xfs and fonts):
        raise PatchError("The workbook's styles are laid out in a way this tool does not patch safely.")
    fill_count, xf_count, font_count = int(fills.group(1)), int(xfs.group(1)), int(fonts.group(1))
    fill_ids = {}
    new_fills = ""
    for offset, severity in enumerate(SEVERITY_ORDER):
        fill_ids[severity] = fill_count + offset
        new_fills += (f'<fill><patternFill patternType="solid"><fgColor rgb="{SEVERITY_FILL[severity]}"/>'
                      f'<bgColor indexed="64"/></patternFill></fill>')
    existing = re.findall(r"<xf\b[^>]*?(?:/>|>.*?</xf>)", xfs.group(2), re.S)
    if len(existing) != xf_count:
        raise PatchError("The workbook's cell styles could not be counted reliably.")

    def with_fill(xf: str, fill_id: int) -> str:
        start = re.match(r"<xf\b[^>]*?(/?)>", xf)
        tag = start.group(0)
        tag = re.sub(r'\sfillId="\d+"', "", tag)
        tag = re.sub(r'\sapplyFill="\d"', "", tag)
        tag = tag.replace("<xf", f'<xf fillId="{fill_id}" applyFill="1"', 1)
        return tag + xf[len(start.group(0)):]

    new_xfs, mapping = "", {}
    for index, (old, severity) in enumerate(sorted(needed)):
        if old >= len(existing):
            raise PatchError("A cell uses a style that is not in the style table.")
        mapping[(old, severity)] = xf_count + index
        new_xfs += with_fill(existing[old], fill_ids[severity])
    next_xf = xf_count + len(mapping)
    bold_font = '<font><b/><sz val="11"/><name val="Calibri"/></font>'
    new_xfs += f'<xf numFmtId="0" fontId="{font_count}" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    new_xfs += '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    special = {"header": next_xf, "wrap": next_xf + 1}

    out = styles
    for pattern, count, addition in ((r'(<cellXfs\b[^>]*?)count="\d+"([^>]*>.*?)(</cellXfs>)', xf_count + len(mapping) + 2, new_xfs),
                                     (r'(<fills\b[^>]*?)count="\d+"([^>]*>.*?)(</fills>)', fill_count + 3, new_fills),
                                     (r'(<fonts\b[^>]*?)count="\d+"([^>]*>.*?)(</fonts>)', font_count + 1, bold_font)):
        out, n = re.subn(pattern, lambda m: f'{m.group(1)}count="{count}"{m.group(2)}{addition}{m.group(3)}', out, count=1, flags=re.S)
        if n != 1:
            raise PatchError("The workbook's styles could not be updated.")
    return out, mapping, special


# ---------------------------------------------------------------------------------------------------------------
# NOTES (comments + VML)
# ---------------------------------------------------------------------------------------------------------------
def _comments_xml(notes: dict, author: str = AUTHOR) -> str:
    items = ""
    for coord in sorted(notes, key=lambda c: _split_coord(c)):
        items += (f'<comment ref="{coord}" authorId="0"><text><r><t xml:space="preserve">{escape(clean(notes[coord]))}</t></r></text></comment>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<comments xmlns="{NS_MAIN}"><authors><author>{escape(author)}</author></authors><commentList>{items}</commentList></comments>')


def _vml_xml(notes: dict, idmap: int) -> str:
    shapes = ""
    for number, coord in enumerate(sorted(notes, key=lambda c: _split_coord(c)), start=1):
        row, col = _split_coord(coord)
        anchor = f"{col}, 15, {max(row - 2, 0)}, 10, {col + 3}, 15, {row + 3}, 4"
        shapes += (
            f'<v:shape id="_x0000_s{idmap * 1024 + number}" type="#_x0000_t202" '
            'style="position:absolute;margin-left:80pt;margin-top:5pt;width:280pt;height:100pt;z-index:%d;visibility:hidden" '
            'fillcolor="#ffffe1" o:insetmode="auto"><v:fill color2="#ffffe1"/><v:shadow on="t" color="black" obscured="t"/>'
            '<v:path o:connecttype="none"/><v:textbox style="mso-direction-alt:auto"><div style="text-align:left"></div></v:textbox>'
            '<x:ClientData ObjectType="Note"><x:MoveWithCells/><x:SizeWithCells/>'
            f'<x:Anchor>{anchor}</x:Anchor><x:AutoFill>False</x:AutoFill><x:Row>{row - 1}</x:Row><x:Column>{col - 1}</x:Column>'
            '</x:ClientData></v:shape>' % number)
    return ('<xml xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" '
            'xmlns:x="urn:schemas-microsoft-com:office:excel">'
            f'<o:shapelayout v:ext="edit"><o:idmap v:ext="edit" data="{idmap}"/></o:shapelayout>'
            '<v:shapetype id="_x0000_t202" coordsize="21600,21600" o:spt="202" path="m,l,21600r21600,l21600,xe">'
            '<v:stroke joinstyle="miter"/><v:path gradientshapeok="t" o:connecttype="rect"/></v:shapetype>'
            f'{shapes}</xml>')


_AFTER_LEGACY = ("legacyDrawingHF", "picture", "oleObjects", "controls", "webPublishParameters", "tableParts", "extLst")


def _insert_legacy_drawing(sheet_xml: str, rid: str) -> str:
    root = re.search(r"<worksheet\b[^>]*>", sheet_xml)
    if not root:
        raise PatchError("A sheet part has an unexpected layout.")
    root_tag = root.group(0)
    if "xmlns:r=" not in root_tag:
        sheet_xml = sheet_xml.replace(root_tag, root_tag[:-1] + f' xmlns:r="{NS_R}">', 1)
    element = f'<legacyDrawing r:id="{rid}"/>'
    positions = [m.start() for tag in _AFTER_LEGACY if (m := re.search(rf"<{tag}[\s/>]", sheet_xml))]
    index = min(positions) if positions else sheet_xml.rindex("</worksheet>")
    return sheet_xml[:index] + element + sheet_xml[index:]


# ---------------------------------------------------------------------------------------------------------------
# REPORT SHEET
# ---------------------------------------------------------------------------------------------------------------
def _report_sheet_xml(rows: list, special: dict, footer: str, lead: list = ()) -> str:
    headers = ["#", "Importance", "Where", "What was found", "Why it matters", "Evidence", "Suggested fix"]

    def cell(ref, value, style):
        if isinstance(value, (int, float)):
            return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
        return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(clean(value))}</t></is></c>'

    # `lead`: lines written above the table (for example the column changes of a monthly check); the first one is a bold title
    shift = len(lead) + 1 if lead else 0
    body = "".join(f'<row r="{n}">{cell(f"A{n}", line, special["header"] if n == 1 else 0)}</row>' for n, line in enumerate(lead, start=1))
    header_row = shift + 1
    body += f'<row r="{header_row}">' + "".join(cell(f"{get_column_letter(i)}{header_row}", h, special["header"]) for i, h in enumerate(headers, start=1)) + "</row>"
    links = ""
    for position, row in enumerate(rows, start=header_row + 1):
        values = [row.number, row.severity.capitalize(), row.where, row.title, row.detail, row.evidence, row.fix]
        body += f'<row r="{position}">' + "".join(cell(f"{get_column_letter(i)}{position}", v, special["wrap"]) for i, v in enumerate(values, start=1)) + "</row>"
        if row.link:
            sheet, coord = row.link
            location = "'" + sheet.replace("'", "''") + "'!" + coord
            links += f'<hyperlink ref="C{position}" location="{escape(location, {chr(34): "&quot;"})}" display="{escape(clean(row.where), {chr(34): "&quot;"})}"/>'
    footer_row = header_row + len(rows) + 2
    body += f'<row r="{footer_row}">{cell(f"A{footer_row}", footer, 0)}</row>'
    widths = (5, 12, 26, 48, 60, 50, 40)
    cols = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, start=1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<worksheet xmlns="{NS_MAIN}" xmlns:r="{NS_R}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="{header_row}" topLeftCell="A{header_row + 1}" '
            'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/>'
            f'<cols>{cols}</cols><sheetData>{body}</sheetData>' + (f"<hyperlinks>{links}</hyperlinks>" if links else "") + "</worksheet>")


# ---------------------------------------------------------------------------------------------------------------
# THE PATCH
# ---------------------------------------------------------------------------------------------------------------
def patch_workbook(original: bytes, annotations: list, report_rows: list, footer: str = "", sheet_title: str = REPORT_SHEET,
                   author: str = AUTHOR, lead: list = ()) -> PatchResult:
    """Return the highlighted copy (validated). Raises PatchError when it cannot be made safely. `sheet_title` names the report sheet and
    `author` the notes (each tool uses its own: "Doctor report" / "Rule report"); `lead` are lines written above the table of findings."""
    try:
        result = _patch(original, annotations, report_rows, footer, sheet_title, author, lead)
    except PatchError:
        raise
    except Exception as error:                              # anything unexpected: never hand out a half-made file
        raise PatchError(f"The highlighted copy could not be made ({type(error).__name__}).")
    validate_patched(original, result.data, annotations)
    return result


def _patch(original: bytes, annotations: list, report_rows: list, footer: str, sheet_title: str = REPORT_SHEET, author: str = AUTHOR, lead: list = ()) -> PatchResult:
    try:
        source = zipfile.ZipFile(io.BytesIO(original))
    except zipfile.BadZipFile:
        raise PatchError("This is not a normal .xlsx file.")
    names = source.namelist()
    for required in ("[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml"):
        if required not in names:
            raise PatchError(f"The workbook has no {required} part.")
    text = {n: source.read(n).decode("utf-8") for n in ("[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml")}
    sheet_part = _sheet_parts(text["xl/workbook.xml"], text["xl/_rels/workbook.xml.rels"])
    result = PatchResult(data=b"")

    # one annotation set per cell: the most serious colour, every message
    by_sheet = {}
    for a in annotations:
        if a.sheet not in sheet_part:
            result.skipped.append(f"'{a.sheet}' was not found in the file.")
            continue
        cell = by_sheet.setdefault(a.sheet, {}).setdefault(a.coord.upper(), {"severity": a.severity, "messages": []})
        if SEVERITY_ORDER.index(a.severity) < SEVERITY_ORDER.index(cell["severity"]):
            cell["severity"] = a.severity
        if a.message not in cell["messages"]:
            cell["messages"].append(a.message)

    changed, added = {}, {}                                  # part name -> new text / bytes

    # --- which style numbers the highlighted cells have now ------------------------------------------------------------
    sheet_text, needed, current_style = {}, set(), {}
    for sheet, cells in by_sheet.items():
        part = sheet_part[sheet]
        if part not in names:
            raise PatchError(f"The part for sheet '{sheet}' is missing.")
        sheet_text[sheet] = source.read(part).decode("utf-8")
        for coord, info in cells.items():
            match = re.search(rf'<c r="{coord}"(?=[\s/>])([^>]*?)(/?)>', sheet_text[sheet])
            if not match:
                continue                                          # an empty cell has nothing to colour; the note still lists it
            style = re.search(r'\ss="(\d+)"', match.group(1))
            current_style[(sheet, coord)] = int(style.group(1)) if style else 0
            needed.add((current_style[(sheet, coord)], info["severity"]))
    styles, mapping, special = _patch_styles(text["xl/styles.xml"], needed)
    changed["xl/styles.xml"] = styles

    # --- colour the cells; add the notes ---------------------------------------------------------------------------------
    rels_text = text["xl/_rels/workbook.xml.rels"]
    content_types = text["[Content_Types].xml"]
    has_vml_default = re.search(r'<Default\b[^>]*Extension="vml"', content_types, re.I) is not None
    new_overrides, vml_drawings = "", 0
    for sheet, cells in by_sheet.items():
        part = sheet_part[sheet]
        xml = sheet_text[sheet]
        for coord, info in cells.items():
            key = (sheet, coord)
            if key not in current_style:
                result.skipped.append(f"{sheet}!{coord} is an empty cell, so it was not coloured.")
                continue
            new_style = mapping[(current_style[key], info["severity"])]

            def recolour(match, new_style=new_style, coord=coord):
                attrs = re.sub(r'\ss="\d+"', "", match.group(1))
                return f'<c r="{coord}" s="{new_style}"{attrs}{match.group(2)}>'
            xml, n = re.subn(rf'<c r="{coord}"(?=[\s/>])([^>]*?)(/?)>', recolour, xml, count=1)
            if n == 1:
                result.highlighted += 1

        # notes: only on sheets that have none yet
        sheet_rels_path = part.replace("worksheets/", "worksheets/_rels/") + ".rels"
        existing_rels = source.read(sheet_rels_path).decode("utf-8") if sheet_rels_path in names else None
        already = "<legacyDrawing" in xml or (existing_rels is not None and "/comments" in existing_rels)
        if already:
            result.skipped.append(f"Sheet '{sheet}' already has notes, so no notes were added there (the cells are still coloured and listed in the report).")
        else:
            notes = {coord: " | ".join(info["messages"]) for coord, info in cells.items() if (sheet, coord) in current_style}
            if notes:
                n_comments = _next_number(list(names) + list(added), r"xl/comments(\d+)\.xml$")
                n_vml = _next_number(list(names) + list(added), r"xl/drawings/vmlDrawing(\d+)\.vml$")
                vml_drawings += 1
                comments_part, vml_part = f"xl/comments{n_comments}.xml", f"xl/drawings/vmlDrawing{n_vml}.vml"
                added[comments_part] = _comments_xml(notes, author)
                added[vml_part] = _vml_xml(notes, 200 + vml_drawings)
                rels = existing_rels if existing_rels is not None else (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>')
                used = [int(m) for m in re.findall(r'Id="rId(\d+)"', rels)]
                rid_c, rid_v = f"rId{max(used, default=0) + 1}", f"rId{max(used, default=0) + 2}"
                rels = rels.replace("</Relationships>",
                                    f'<Relationship Id="{rid_c}" Type="{REL_BASE}comments" Target="../comments{n_comments}.xml"/>'
                                    f'<Relationship Id="{rid_v}" Type="{REL_BASE}vmlDrawing" Target="../drawings/vmlDrawing{n_vml}.vml"/></Relationships>')
                if existing_rels is None:
                    added[sheet_rels_path] = rels
                else:
                    changed[sheet_rels_path] = rels
                xml = _insert_legacy_drawing(xml, rid_v)
                new_overrides += f'<Override PartName="/{comments_part}" ContentType="{CT_COMMENTS}"/>'
                result.notes += len(notes)
        changed[part] = xml

    # --- the report sheet (last) -------------------------------------------------------------------------------------------
    workbook_xml = text["xl/workbook.xml"]
    existing_names = [html.unescape(n) for n in re.findall(r'<sheet\b[^>]*?\sname="([^"]*)"', workbook_xml)]
    title, k = sheet_title, 2
    while title in existing_names:
        title, k = f"{sheet_title} {k}", k + 1
    new_sheet_part = f"xl/worksheets/sheet{_next_number(list(names) + list(added), r'xl/worksheets/sheet(\d+)\.xml$')}.xml"
    added[new_sheet_part] = _report_sheet_xml(report_rows, special, footer, lead)
    used = [int(m) for m in re.findall(r'Id="rId(\d+)"', rels_text)]
    rid = f"rId{max(used, default=0) + 1}"
    changed["xl/_rels/workbook.xml.rels"] = rels_text.replace(
        "</Relationships>", f'<Relationship Id="{rid}" Type="{REL_BASE}worksheet" Target="{new_sheet_part[3:]}"/></Relationships>')
    sample = re.search(r"<sheet\b[^>]*/>", workbook_xml)
    prefix = next((k2.rsplit(":", 1)[0] for k2 in _attrs(sample.group(0)) if k2.endswith(":id")), None) if sample else None
    if prefix is None:
        raise PatchError("The workbook's sheet list has an unexpected layout.")
    sheet_ids = [int(m) for m in re.findall(r'\ssheetId="(\d+)"', workbook_xml)]
    changed["xl/workbook.xml"] = workbook_xml.replace(
        "</sheets>", f'<sheet name="{escape(title, {chr(34): "&quot;"})}" sheetId="{max(sheet_ids, default=0) + 1}" {prefix}:id="{rid}"/></sheets>', 1)
    content_types = content_types.replace("</Types>", (
        ("" if has_vml_default or not vml_drawings else f'<Default Extension="vml" ContentType="{CT_VML}"/>') + new_overrides
        + f'<Override PartName="/{new_sheet_part}" ContentType="{CT_SHEET}"/></Types>'), 1)
    changed["[Content_Types].xml"] = content_types

    # --- write the package: untouched parts copied byte for byte, in the original order ------------------------------------
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            if item.filename in changed:
                target.writestr(item, changed[item.filename].encode("utf-8"))
            else:
                target.writestr(item, source.read(item.filename))
        for name, content in added.items():
            if name not in source.namelist():
                target.writestr(name, content.encode("utf-8"))
    result.data = out.getvalue()
    result.touched = set(changed) | set(added)
    return result


# ---------------------------------------------------------------------------------------------------------------
# VALIDATION
# ---------------------------------------------------------------------------------------------------------------
def validate_patched(original: bytes, patched: bytes, annotations: list) -> None:
    """Raise PatchError unless the copy is the original plus colours, notes and the report sheet - and nothing else."""
    from xml.dom import minidom

    from toolkit.sheets import read_workbook

    try:
        before, after = zipfile.ZipFile(io.BytesIO(original)), zipfile.ZipFile(io.BytesIO(patched))
        if after.testzip() is not None:
            raise PatchError("The highlighted copy is damaged.")
        old_names, new_names = before.namelist(), after.namelist()
        if [n for n in old_names if n not in new_names]:
            raise PatchError("The highlighted copy lost a part of the original.")
        allowed_changes = {"[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml"}
        for name in old_names:
            same = before.read(name) == after.read(name)
            if not same and name not in allowed_changes and not re.match(r"xl/worksheets/(_rels/)?[^/]+\.(xml|rels)$", name):
                raise PatchError(f"The part {name} changed, but should not have.")
        for name in new_names:                                 # everything we wrote must be well-formed XML
            if name not in old_names or before.read(name) != after.read(name):
                try:
                    minidom.parseString(after.read(name))
                except Exception:
                    raise PatchError(f"The part {name} is not valid XML.")
        # the package must describe itself consistently
        types = after.read("[Content_Types].xml").decode("utf-8")
        defaults = {m.lower() for m in re.findall(r'<Default\b[^>]*Extension="([^"]+)"', types)}
        overrides = set(re.findall(r'<Override\b[^>]*PartName="([^"]+)"', types))
        different = {n for n in new_names if n not in old_names or before.read(n) != after.read(n)}
        for name in different:
            if name.endswith("/") or name == "[Content_Types].xml":
                continue
            needs_own_type = re.match(r"xl/(comments\d+|worksheets/sheet\d+)\.xml$", name) is not None      # a generic "xml" type is not enough
            if "/" + name not in overrides and (needs_own_type or name.rsplit(".", 1)[-1].lower() not in defaults):
                raise PatchError(f"The part {name} is not listed in the package.")
        for name in different:
            if name.endswith(".rels"):
                base = name.replace("_rels/", "").rsplit(".rels", 1)[0]
                folder = base.rsplit("/", 1)[0] + "/" if "/" in base else ""
                for tag in re.findall(r"<Relationship\b[^>]*>", after.read(name).decode("utf-8")):
                    attrs = _attrs(tag)
                    if attrs.get("TargetMode") == "External" or "Target" not in attrs:
                        continue
                    target = attrs["Target"]
                    path = target.lstrip("/") if target.startswith("/") else _normalize(folder + target)
                    if path not in new_names:
                        raise PatchError(f"A link inside the file points to {path}, which does not exist.")
        # every cell is unchanged: typed value, formula, stored result, number format
        was, now = read_workbook(original), read_workbook(patched)
        if now.order[:len(was.order)] != was.order or len(now.order) != len(was.order) + 1:
            raise PatchError("The sheets of the copy are not the original sheets plus the report.")
        for name, sheet in was.sheets.items():
            other = now.sheets[name]
            if set(sheet.cells) != set(other.cells):
                raise PatchError(f"Cells appeared or disappeared on '{name}'.")
            for key, cell in sheet.cells.items():
                twin = other.cells[key]
                if (cell.formula, cell.value, cell.number_format) != (twin.formula, twin.value, twin.number_format):
                    raise PatchError(f"A value changed at {name}!{cell.coord}.")
    except PatchError:
        raise
    except Exception as error:
        raise PatchError(f"The highlighted copy could not be checked ({type(error).__name__}).")


def _normalize(path: str) -> str:
    parts = []
    for piece in path.split("/"):
        if piece == "..":
            if parts:
                parts.pop()
        elif piece not in ("", "."):
            parts.append(piece)
    return "/".join(parts)
