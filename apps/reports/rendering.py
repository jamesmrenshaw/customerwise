"""Lay a submitted report out by each section's display style (D06).

Used by the staff review screen now and by the PDF/Word export next. Works from a
ReportSubmission snapshot, so it shows exactly what was submitted.
"""

from datetime import datetime

from django.utils import timezone

from .formatting import blocks, render_html


def _answer(q):
    if q["answer_type"] == "datetime":
        if not q.get("datetime"):
            return {"html": "", "text": "", "blank": True}
        when = timezone.localtime(datetime.fromisoformat(q["datetime"]))
        text = when.strftime("%-d %B %Y, %H:%M")
        return {"html": text, "text": text, "blank": False}
    if q["answer_type"] == "long_text":
        return {
            "html": render_html(q["text"]),
            "blocks": blocks(q["text"]),
            "blank": not q["text"],
            "rich": True,
        }
    return {"html": q["text"], "text": q["text"], "blank": not q["text"].strip()}


def build_document(snapshot):
    """{"key_facts": [...], "sections": [{"title", "style", "items": [...]}]}."""
    key_facts, sections = [], []
    for section in snapshot["sections"]:
        items = [{"key": q["key"], "prompt": q["prompt"], **_answer(q)} for q in section["questions"]]
        if section["display_style"] == "key_facts":
            key_facts.extend(items)
        else:
            sections.append({"title": section["title"], "style": section["display_style"], "items": items})
    return {"key_facts": key_facts, "sections": sections}
