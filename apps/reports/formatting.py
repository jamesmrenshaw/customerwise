"""Simple answer formatting (D06): paragraphs, bullet lists and bold, nothing else.

Shoppers type plain text: a blank line starts a new paragraph, a line starting with "- " or
"• " is a bullet, and **text** is bold. The text is HTML-escaped before any markup is added,
so nothing else can get into the report view or exports.
"""

import re

from django.utils.html import escape
from django.utils.safestring import mark_safe

BULLET = re.compile(r"^\s*[-•*]\s+")
BOLD = re.compile(r"\*\*(.+?)\*\*")


def blocks(text):
    """Split into [("p", [lines]) | ("ul", [items])] for renderers (HTML here, Word export later)."""
    result = []
    for chunk in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip()):
        lines = [line.rstrip() for line in chunk.split("\n") if line.strip()]
        if not lines:
            continue
        paragraph = []
        for line in lines:
            if BULLET.match(line):
                if paragraph:
                    result.append(("p", paragraph))
                    paragraph = []
                if result and result[-1][0] == "ul":
                    result[-1][1].append(BULLET.sub("", line))
                else:
                    result.append(("ul", [BULLET.sub("", line)]))
            else:
                paragraph.append(line.strip())
        if paragraph:
            result.append(("p", paragraph))
    return result


def inline_html(text):
    return BOLD.sub(r"<strong>\1</strong>", escape(text))


def render_html(text):
    out = []
    for kind, items in blocks(text):
        if kind == "ul":
            out.append("<ul>" + "".join(f"<li>{inline_html(i)}</li>" for i in items) + "</ul>")
        else:
            out.append("<p>" + "<br>".join(inline_html(i) for i in items) + "</p>")
    return mark_safe("".join(out))  # noqa: S308 - every piece of text is escaped above
