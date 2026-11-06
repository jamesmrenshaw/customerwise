"""Word (.docx) export with python-docx. Mirrors templates/exports/report_pdf.html."""

import io

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image

BRAND = RGBColor(0x00, 0x74, 0x80)
MUTED = RGBColor(0x50, 0x58, 0x62)
SHADE = "F6F7F9"


def _shade(cell, colour=SHADE):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), colour)
    tc_pr.append(shd)


def _layout(table, widths, padding_twips=(90, 110)):
    """Fixed column widths and cell padding (Word and LibreOffice both honour these)."""
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tbl_pr.append(layout)
    margins = OxmlElement("w:tblCellMar")
    vertical, horizontal = padding_twips
    for side, value in (("top", vertical), ("bottom", vertical), ("left", horizontal), ("right", horizontal)):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), str(value))
        el.set(qn("w:type"), "dxa")
        margins.append(el)
    tbl_pr.append(margins)
    for row in table.rows:
        for cell, width in zip(row.cells, widths, strict=False):
            cell.width = width
    for column, width in zip(table.columns, widths, strict=False):
        column.width = width


def _page_field(paragraph):
    run = paragraph.add_run()
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)


def _runs(paragraph, runs):
    for text, bold in runs:
        if text == "\n":
            paragraph.add_run().add_break(WD_BREAK.LINE)
            continue
        run = paragraph.add_run(text)
        run.bold = bold or None


def _answer(container, item, document):
    """Write one answer into a document or table cell, keeping paragraphs, bullets and bold."""
    add = container.add_paragraph
    if item.get("blank"):
        p = add()
        r = p.add_run("No answer")
        r.italic = True
        r.font.color.rgb = MUTED
        return
    if not item.get("rich"):
        add(item.get("text", ""))
        return
    for kind, entries in item["blocks"]:
        if kind == "ul":
            for runs in entries:
                _runs(add(style=document.styles["List Bullet"]), runs)
        else:
            p = add()
            for i, line in enumerate(entries):
                if i:
                    p.add_run().add_break(WD_BREAK.LINE)
                _runs(p, line)


def _clear_first_paragraph(cell):
    # New table cells start with one empty paragraph; reuse it so cells don't open with a blank line.
    first = cell.paragraphs[0]
    if not first.text and len(cell.paragraphs) > 1:
        first._element.getparent().remove(first._element)


def build(ctx):
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10.5)
    for name, size in (("Title", 26), ("Heading 1", 20), ("Heading 2", 15), ("Heading 3", 12)):
        style = doc.styles[name]
        style.font.name = "Arial"
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0x16, 0x19, 0x1D)

    section = doc.sections[0]
    section.page_height, section.page_width = Cm(29.7), Cm(21.0)
    for side in ("left_margin", "right_margin"):
        setattr(section, side, Cm(2.2))

    # Title page
    if ctx.get("logo_path"):
        png = io.BytesIO()
        Image.open(ctx["logo_path"]).save(png, format="PNG")
        png.seek(0)
        doc.add_picture(png, width=Cm(6.5))
    for _ in range(6):
        doc.add_paragraph()
    eyebrow = doc.add_paragraph().add_run("MYSTERY SHOPPING REPORT")
    eyebrow.font.color.rgb = BRAND
    eyebrow.bold = True
    doc.add_paragraph(ctx["project_name"], style="Title")
    target = doc.add_paragraph().add_run(ctx["target"])
    target.font.size = Pt(16)
    when = doc.add_paragraph().add_run(f"{ctx['date']:%-d %B %Y}")
    when.font.color.rgb = MUTED
    body = doc.add_section(WD_SECTION.NEW_PAGE)
    body.footer.is_linked_to_previous = False  # no footer on the title page
    footer = body.footer.paragraphs[0]
    footer.style.font.size = Pt(8)  # the page number takes the paragraph style's size
    footer.style.font.color.rgb = MUTED
    footer.text = f"Customer Wise · {ctx['project_name']} · page "
    _page_field(footer)
    for run in footer.runs:
        run.font.size = Pt(8)
        run.font.color.rgb = MUTED
        rpr = run._r.get_or_add_rPr()  # the page-number field run needs its size set explicitly too
        for tag in ("w:sz", "w:szCs"):
            size = OxmlElement(tag)
            size.set(qn("w:val"), "16")
            rpr.append(size)

    doc.add_heading(ctx["target"], level=1)
    document = ctx["document"]
    if document["key_facts"]:
        table = doc.add_table(rows=0, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.LEFT
        for item in document["key_facts"]:
            label, value = table.add_row().cells
            _shade(label)
            _shade(value)
            label.paragraphs[0].add_run(item["prompt"]).bold = True
            _answer(value, item, doc)
            _clear_first_paragraph(value)
        _layout(table, [Cm(7.0), Cm(9.6)])
        doc.add_paragraph()

    for sec in document["sections"]:
        doc.add_heading(sec["title"], level=2)
        if sec["style"] == "qa_table":
            table = doc.add_table(rows=0, cols=2)
            table.style = doc.styles["Table Grid"]
            for item in sec["items"]:
                question, answer = table.add_row().cells
                _shade(question)
                question.paragraphs[0].add_run(item["prompt"]).bold = True
                _answer(answer, item, doc)
                _clear_first_paragraph(answer)
            _layout(table, [Cm(5.6), Cm(11.0)])
            doc.add_paragraph()
        elif sec["style"] == "headings":
            for item in sec["items"]:
                doc.add_heading(item["prompt"], level=3)
                _answer(doc, item, doc)
        else:  # answers only: the questions were prompts for the shopper
            for item in sec["items"]:
                _answer(doc, item, doc)

    doc.add_heading("Evidence", level=2)
    if ctx["evidence"]:
        for e in ctx["evidence"]:
            p = doc.add_paragraph(style=doc.styles["List Bullet"])
            p.add_run(e.original_name)
            p.add_run(f" · {e.get_category_display()}").font.color.rgb = MUTED
    else:
        doc.add_paragraph("No evidence files are included with this report.")

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
