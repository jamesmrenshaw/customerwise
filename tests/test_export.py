"""Export (MVP-02, CUS-142) and the append-only audit log (CW-085)."""

import io
import zipfile

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from docx import Document

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.evidence.services import add_evidence
from apps.exports import builder
from apps.fieldwork import services as fieldwork
from apps.projects.models import ClientOrganisation, Project, Target
from apps.questionnaires import services as questionnaires
from apps.questionnaires.models import Question, QuestionnaireVersion, Section
from apps.reports import review, services

pytestmark = pytest.mark.django_db
HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def staff():
    return User.objects.create_staff("alex@customerwise.example", full_name="Alex Morgan")


@pytest.fixture
def sam():
    return User.objects.create_user("sam@example.com", full_name="Sam Okafor")


@pytest.fixture
def submitted(sam):
    client = ClientOrganisation.objects.create(name="Harbourside Bank")
    project = Project.objects.create(
        client=client,
        internal_name="SECRET internal name",
        client_facing_name="Messaging vendor review",
        shopper_facing_name="Mobile capture review",
        status=Project.Status.ACTIVE,
    )
    v = QuestionnaireVersion.objects.create(project=project, number=1)
    facts = Section.objects.create(version=v, title="Key facts", display_style="key_facts", order=10)
    qa = Section.objects.create(version=v, title="Commercial", order=20)
    heads = Section.objects.create(version=v, title="Feedback", display_style="headings", order=30)
    prose = Section.objects.create(version=v, title="Summary", display_style="answers_only", order=40)
    Question.objects.create(
        section=facts, key="method", prompt="Method of first response", answer_type="short_text"
    )
    Question.objects.create(section=qa, key="pricing", prompt="How does pricing work?")
    Question.objects.create(section=heads, key="strengths", prompt="Key strengths?")
    Question.objects.create(section=prose, key="summary", prompt="Summarise")
    questionnaires.publish(v)
    assignment = fieldwork.assign(
        Target.objects.create(project=project, name="Northwind"), sam, approval_confirmed=True
    )
    report = services.start_report(assignment)
    q = {x.key: x for x in Question.objects.filter(section__version=v)}
    services.save_answer(report, q["method"], "Phone call")
    services.save_answer(
        report, q["pricing"], "<div>Per user, <strong>banded</strong></div><ul><li>250 users</li></ul>"
    )
    services.save_answer(report, q["strengths"], "<div>Fast replies</div>")
    services.save_answer(report, q["summary"], "<div>A strong vendor overall.</div>")
    services.save_notes(report, "SECRET shopper note")
    add_evidence(assignment, SimpleUploadedFile("quote.pdf", b"%PDF-1.7 quote"), by=sam)
    add_evidence(assignment, SimpleUploadedFile("reply.eml", b"From: x\n\nhi"), by=sam)
    report = services.submit(report, sam, comment="SECRET submission comment")
    return report


def evidence_ids(report):
    return [str(e.pk) for e in report.assignment.evidence.all()]


def test_pdf_template_has_the_right_content_and_nothing_private(submitted):
    context = builder.export_context(submitted, list(submitted.assignment.evidence.all()))
    html = render_to_string("exports/report_pdf.html", context)
    assert "Messaging vendor review" in html and "Northwind" in html
    assert "<strong>banded</strong>" in html and "<li>250 users</li>" in html
    assert "<h4>Key strengths?</h4>" in html and "A strong vendor overall." in html
    assert "quote.pdf" in html and "Summarise" not in html  # answers-only hides the prompt
    for secret in ("SECRET", "Sam Okafor", "Harbourside"):
        assert secret not in html


def test_pdf_export(submitted, staff):
    result = builder.export_report(submitted, user=staff, fmt="pdf")
    assert result.content.startswith(b"%PDF-")
    assert result.filename == "Messaging vendor review - Northwind - v1.pdf"
    event = AuditEvent.objects.get(action="report.exported")
    assert event.actor == staff and event.data["format"] == "pdf" and event.data["evidence"] == []


def test_docx_export_keeps_formatting_and_hides_private_details(submitted, staff):
    result = builder.export_report(submitted, user=staff, fmt="docx")
    doc = Document(io.BytesIO(result.content))
    text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(
        c.text for t in doc.tables for row in t.rows for c in row.cells
    )
    assert "Messaging vendor review" in text and "Key strengths?" in text
    for secret in ("SECRET", "Sam Okafor", "Harbourside"):
        assert secret not in text
    answer_cell = doc.tables[1].rows[0].cells[1]
    assert [(r.text, r.bold) for r in answer_cell.paragraphs[0].runs] == [
        ("Per user, ", None),
        ("banded", True),
    ]
    assert answer_cell.paragraphs[1].style.name == "List Bullet"
    assert "page" in doc.sections[1].footer.paragraphs[0].text


def test_zip_contains_the_report_and_only_chosen_submitted_evidence(submitted, staff, sam):
    quote = submitted.assignment.evidence.get(original_name="quote.pdf")
    result = builder.export_report(
        submitted, user=staff, fmt="pdf", evidence_ids=[str(quote.pk), "not-an-id"]
    )
    assert result.filename.endswith(".zip") and result.content_type == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(result.content)).namelist()
    assert names == ["Messaging vendor review - Northwind - v1.pdf", "Evidence/quote.pdf"]


def test_returned_reports_cannot_be_exported(submitted, staff):
    review.return_report(submitted, by=staff, message="Fix")
    submitted.refresh_from_db()
    with pytest.raises(ValidationError):
        builder.export_report(submitted, user=staff, fmt="pdf")


def test_export_dialog_and_download(client, staff, submitted):
    client.force_login(staff)
    url = reverse("staff:export_report", args=[submitted.pk])
    dialog = client.get(url, **HTMX).content.decode()
    assert "Export report" in dialog and "quote.pdf" in dialog and "Check before sending" in dialog
    response = client.post(url, {"format": "docx", "evidence": evidence_ids(submitted)})
    assert response["Content-Type"] == "application/zip"
    assert 'filename="Messaging vendor review - Northwind - v1.zip"' in response["Content-Disposition"]
    assert "Export…" in client.get(reverse("staff:review", args=[submitted.pk])).content.decode()


def test_shoppers_cannot_export(client, sam, submitted):
    client.force_login(sam)
    assert client.get(reverse("staff:export_report", args=[submitted.pk])).status_code == 404
    assert (
        client.post(reverse("staff:export_report", args=[submitted.pk]), {"format": "pdf"}).status_code == 404
    )


def test_audit_events_cannot_be_changed_or_deleted(staff):
    from apps.audit.services import record

    event = record(staff, "test.event", data_value=1)
    with pytest.raises(DatabaseError), transaction.atomic():
        AuditEvent.objects.filter(pk=event.pk).update(action="tampered")
    with pytest.raises(DatabaseError), transaction.atomic():
        AuditEvent.objects.filter(pk=event.pk).delete()
    assert AuditEvent.objects.get(pk=event.pk).action == "test.event"
