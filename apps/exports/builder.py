"""Export a submitted report as PDF or Word, optionally zipped with chosen evidence (MVP-02, CUS-142).

What goes in: a Customer Wise title page (client-facing project name, target, date), key facts,
each section in its display style, and the list of included evidence files.
What never goes in: the internal project name, the shopper's identity, the shopper's notes and
Customer Wise review comments.
"""

import io
import re
import zipfile
from dataclasses import dataclass

from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.utils import timezone

from apps.audit.services import record
from apps.reports.models import Report
from apps.reports.rendering import build_document
from apps.reports.review import latest_submission

PDF = "pdf"
DOCX = "docx"
CONTENT_TYPES = {
    PDF: "application/pdf",
    DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass
class ExportFile:
    filename: str
    content: bytes
    content_type: str


def safe_filename(text):
    text = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE).strip()
    return re.sub(r"\s+", " ", text)[:80] or "report"


def export_context(report, evidence):
    submission = latest_submission(report)
    project = report.assignment.target.project
    return {
        "project_name": project.client_facing_name,  # never the internal name
        "target": report.assignment.target.name,
        "date": timezone.localtime(submission.created_at),
        "version": submission.number,
        "document": build_document(submission.snapshot),
        "evidence": evidence,
        "logo_path": finders.find("img/logo.webp"),
        "font_path": finders.find("fonts/nunito-sans-latin-wght-normal.woff2"),
    }


def render_pdf(context):
    from weasyprint import HTML

    html = render_to_string("exports/report_pdf.html", context)
    return HTML(string=html, base_url="/").write_pdf()


def render_docx(context):
    from .docx_builder import build

    return build(context)


def exportable(report):
    return report.status in {Report.Status.SUBMITTED, Report.Status.ACCEPTED} and latest_submission(report)


def export_report(report, *, user, fmt, evidence_ids=()):
    """Build the export and record it in the audit log. Returns an ExportFile."""
    if fmt not in CONTENT_TYPES:
        raise ValidationError("Choose PDF or Word.")
    if not exportable(report):
        raise ValidationError("Only a submitted or accepted report can be exported.")
    submission = latest_submission(report)
    allowed = {e["id"] for e in submission.snapshot.get("evidence", [])}
    evidence = list(
        report.assignment.evidence.filter(pk__in=[i for i in evidence_ids if str(i) in allowed]).order_by(
            "created_at"
        )
    )
    context = export_context(report, evidence)
    content = render_pdf(context) if fmt == PDF else render_docx(context)
    base = safe_filename(f"{context['project_name']} - {context['target']} - v{context['version']}")
    report_name = f"{base}.{fmt}"
    if evidence:
        buffer = io.BytesIO()
        used = set()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(report_name, content)
            for e in evidence:
                name = safe_filename(e.original_name)
                stem, dot, ext = name.rpartition(".")
                n = 1
                while name in used:
                    n += 1
                    name = f"{stem} ({n}).{ext}" if dot else f"{name} ({n})"
                used.add(name)
                with e.file.open("rb") as f:
                    z.writestr(f"Evidence/{name}", f.read())
        result = ExportFile(f"{base}.zip", buffer.getvalue(), "application/zip")
    else:
        result = ExportFile(report_name, content, CONTENT_TYPES[fmt])
    record(
        user,
        "report.exported",
        report,
        project=report.assignment.target.project,
        format=fmt,
        version=submission.number,
        evidence=[str(e.pk) for e in evidence],
        filename=result.filename,
    )
    return result
