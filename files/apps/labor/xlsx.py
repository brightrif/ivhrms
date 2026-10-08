"""A small Excel (.xlsx) writer that needs nothing beyond the Python standard library.

It writes what the labor reports need: a title, a header row, text, whole numbers, hours, money, dates, bold totals,
column widths and a frozen header. Text is stored as plain text, so a value such as '=1+1' is shown as typed and is
never run as a formula."""
import io
import re
import zipfile
from datetime import date, datetime
from decimal import Decimal
from xml.sax.saxutils import escape

# style ids, in the order they appear in STYLES below
DEFAULT, TITLE, HEADER, TEXT_BOLD, INT, HOURS, MONEY, DATE, INT_BOLD, HOURS_BOLD, MONEY_BOLD, NOTE = range(12)

NUMBER_FORMATS = {INT: 3, INT_BOLD: 3, HOURS: 164, HOURS_BOLD: 164, MONEY: 165, MONEY_BOLD: 165, DATE: 166}
_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")        # characters XML cannot hold
_EPOCH = date(1899, 12, 30)


def _column(index):
    """0 -> A, 25 -> Z, 26 -> AA"""
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _text(value):
    return escape(_ILLEGAL.sub("", str(value)))


class Cell:
    """A value and how to show it. `style` is one of the constants above."""

    def __init__(self, value, style=DEFAULT):
        self.value, self.style = value, style


def _cell_xml(ref, cell):
    value, style = cell.value, cell.style
    s = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{s}/>' if style else ""
    if isinstance(value, bool):
        value = "Yes" if value else "No"
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return f'<c r="{ref}" s="{style or DATE}"><v>{(value - _EPOCH).days}</v></c>'
    if isinstance(value, (int, float, Decimal)):
        return f'<c r="{ref}"{s}><v>{value}</v></c>'
    return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{_text(value)}</t></is></c>'


class Sheet:
    def __init__(self, name, rows, widths=None, freeze_row=None):
        self.name, self.rows, self.widths, self.freeze_row = name, rows, widths or [], freeze_row

    def xml(self):
        out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
               '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
        if self.freeze_row:
            out.append(f'<sheetViews><sheetView workbookViewId="0"><pane ySplit="{self.freeze_row}" '
                       f'topLeftCell="A{self.freeze_row + 1}" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
        if self.widths:
            out.append("<cols>" + "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
                                          for i, w in enumerate(self.widths, 1)) + "</cols>")
        out.append("<sheetData>")
        for r, row in enumerate(self.rows, 1):
            cells = "".join(_cell_xml(f"{_column(c)}{r}", cell if isinstance(cell, Cell) else Cell(cell))
                            for c, cell in enumerate(row))
            out.append(f'<row r="{r}">{cells}</row>')
        out.append("</sheetData></worksheet>")
        return "".join(out)


STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="3"><numFmt numFmtId="164" formatCode="#,##0.00"/><numFmt numFmtId="165" formatCode="#,##0.000"/><numFmt numFmtId="166" formatCode="dd mmm yyyy"/></numFmts>
<fonts count="4">
<font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="14"/><name val="Calibri"/></font>
<font><i/><sz val="10"/><color rgb="FF666666"/><name val="Calibri"/></font>
</fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFE8EEF6"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>
<border><left/><right/><top/><bottom style="thin"><color rgb="FF999999"/></bottom><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="12">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment wrapText="1" vertical="center"/></xf>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="166" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="3" fontId="1" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/>
<xf numFmtId="164" fontId="1" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/>
<xf numFmtId="165" fontId="1" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _sheet_name(raw, taken):
    """Excel sheet names: at most 31 characters, none of []:*?/\\, and unique in the workbook."""
    name = re.sub(r"[\[\]:*?/\\]", " ", raw).strip() or "Sheet"
    name = name[:31]
    base, n = name, 2
    while name.lower() in taken:
        suffix = f" {n}"
        name, n = base[:31 - len(suffix)] + suffix, n + 1
    taken.add(name.lower())
    return name


def build_workbook(sheets):
    """Return the bytes of an .xlsx file holding the given Sheets."""
    taken = set()
    names = [_sheet_name(s.name, taken) for s in sheets]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(1, len(sheets) + 1)) + "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{_text(n)}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, 1))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                             for i in range(1, len(sheets) + 1))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", STYLES)
        for i, sheet in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", sheet.xml())
    return buffer.getvalue()
