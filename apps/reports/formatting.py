"""Rich-text answers (D06): paragraphs, line breaks, bold and bullet lists, nothing else.

Shoppers write in Trix, which produces HTML. Everything is cleaned with nh3 on save (and again
before display), so pasted headings, links, images, styles and scripts never get through.
`blocks()` turns the cleaned HTML into a simple structure that exports (PDF/Word) can render.
"""

import re
from html.parser import HTMLParser

import nh3
from django.utils.html import escape
from django.utils.safestring import mark_safe

ALLOWED_TAGS = {"div", "p", "br", "strong", "b", "ul", "li"}


def clean(html):
    """Sanitise to the allowed tags with no attributes; <b> becomes <strong>."""
    cleaned = nh3.clean(html or "", tags=ALLOWED_TAGS, attributes={}, strip_comments=True)
    cleaned = re.sub(r"<(/?)b>", r"<\1strong>", cleaned)
    return "" if is_blank(cleaned) else cleaned.strip()


def plain_text(html):
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text.replace("&nbsp;", " ")).strip()


def is_blank(html):
    return not plain_text(html)


def render_html(html):
    """Safe HTML for the report view (re-cleaned in case of older data)."""
    return mark_safe(clean(html))  # noqa: S308 - nh3 allows only the tags above, no attributes


class _BlockParser(HTMLParser):
    """Collects [("p", [[(text, bold), ...] per line]), ("ul", [[(text, bold), ...] per item])]."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.bold = 0
        self.in_li = False
        self.line = []
        self.lines = []
        self.blank_lines = 0

    def _end_line(self):
        if self.line:
            self.lines.append(self.line)
            self.blank_lines = 0
        elif self.lines:
            self.blank_lines += 1
        self.line = []

    def _end_paragraph(self):
        self._end_line()
        if self.lines:
            self.blocks.append(("p", self.lines))
        self.lines = []
        self.blank_lines = 0

    def handle_starttag(self, tag, attrs):
        if tag == "strong":
            self.bold += 1
        elif tag == "br":
            if self.in_li:
                self.line.append(("\n", False))
            else:
                self._end_line()
                if self.blank_lines:  # an empty line (two <br>s) starts a new paragraph
                    self._end_paragraph()
        elif tag == "ul":
            self._end_paragraph()
            self.blocks.append(("ul", []))
        elif tag == "li":
            self.in_li = True
            self.line = []
        elif tag in {"div", "p"}:
            self._end_paragraph()

    def handle_endtag(self, tag):
        if tag == "strong":
            self.bold = max(0, self.bold - 1)
        elif tag == "li":
            if self.blocks and self.blocks[-1][0] == "ul" and self.line:
                self.blocks[-1][1].append(self.line)
            self.line = []
            self.in_li = False
        elif tag in {"div", "p"}:
            self._end_paragraph()

    def handle_data(self, data):
        if data.strip() or self.line:
            self.line.append((data, self.bold > 0))

    def close(self):
        super().close()
        self._end_paragraph()
        return [b for b in self.blocks if b[1]]


def blocks(html):
    parser = _BlockParser()
    parser.feed(clean(html))
    return parser.close()


# --- Converting answers written before the rich-text editor (6 Nov 2026) -------------------

_BULLET = re.compile(r"^\s*[-•*]\s+")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def from_simple_markup(text):
    """Old answers used **bold**, '- ' bullets and blank-line paragraphs. Convert to HTML."""
    out = []
    for chunk in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip()):
        lines = [line.rstrip() for line in chunk.split("\n") if line.strip()]
        para, items = [], []
        for line in lines:
            if _BULLET.match(line):
                if para:
                    out.append("<div>" + "<br>".join(para) + "</div>")
                    para = []
                items.append(_BOLD.sub(r"<strong>\1</strong>", escape(_BULLET.sub("", line))))
            else:
                if items:
                    out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
                    items = []
                para.append(_BOLD.sub(r"<strong>\1</strong>", escape(line.strip())))
        if items:
            out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
        if para:
            out.append("<div>" + "<br>".join(para) + "</div>")
    return clean("".join(out))
