"""Report editor and evidence (prototype screen 04; CUS-70 to CUS-79, CUS-84, CUS-119)."""

import html as htmllib
import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.evidence.models import EvidenceFile
from apps.fieldwork import services as fieldwork
from apps.fieldwork.models import Assignment
from apps.projects import services as projects
from apps.projects.models import ClientOrganisation, Project, Target
from apps.questionnaires import services as questionnaires
from apps.questionnaires.models import Question, QuestionnaireVersion, Section
from apps.reports import services
from apps.reports.formatting import blocks, clean, from_simple_markup
from apps.reports.models import Report

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


# --- Fixtures ---------------------------------------------------------------------------


@pytest.fixture
def sam():
    return User.objects.create_user("sam@example.com", full_name="Sam Okafor")


@pytest.fixture
def priya():
    return User.objects.create_user("priya@example.com", full_name="Priya Shah")


@pytest.fixture
def staff():
    return User.objects.create_staff("alex@customerwise.example")


@pytest.fixture
def project():
    client = ClientOrganisation.objects.create(name="Harbourside Bank")
    return Project.objects.create(
        client=client,
        internal_name="Internal",
        client_facing_name="Client facing",
        shopper_facing_name="Mobile messaging capture review",
        status=Project.Status.ACTIVE,
    )


@pytest.fixture
def published(project):
    version = QuestionnaireVersion.objects.create(project=project, number=1)
    facts = Section.objects.create(version=version, title="Key facts", display_style="key_facts", order=10)
    commercial = Section.objects.create(version=version, title="Commercial", order=20)
    Question.objects.create(
        section=facts, key="first-enquiry", prompt="First enquiry", answer_type="datetime", order=10
    )
    Question.objects.create(section=commercial, key="structure", prompt="How does pricing work?", order=10)
    Question.objects.create(
        section=commercial, key="min-users", prompt="Minimum users?", answer_type="short_text", order=20
    )
    return questionnaires.publish(version)


@pytest.fixture
def assignment(project, sam):
    target = Target.objects.create(project=project, name="Global Relay")
    return fieldwork.assign(target, sam, approval_confirmed=True)


@pytest.fixture
def as_sam(client, sam):
    client.force_login(sam)
    return client


@pytest.fixture
def report(assignment, published):
    return services.start_report(assignment)


def q(report, key):
    return Question.objects.get(section__version=report.questionnaire_version, key=key)


def answer_everything(report):
    services.save_answer(report, q(report, "first-enquiry"), timezone.now())
    services.save_answer(report, q(report, "structure"), "<div>Per user, billed yearly.</div>")
    services.save_answer(report, q(report, "min-users"), "100")


# --- Formatting ---------------------------------------------------------------------------


def test_rich_text_keeps_only_paragraphs_bold_and_bullets():
    dirty = (
        '<h1>Head</h1><div style="color:red" onclick="x()">Intro <b>bold</b><br>line two</div>'
        '<ul><li>one</li></ul><script>alert(1)</script><a href="https://evil">link</a><img src="x">'
    )
    assert clean(dirty) == "Head<div>Intro <strong>bold</strong><br>line two</div><ul><li>one</li></ul>link"


def test_blocks_split_paragraphs_lines_and_bullets():
    html = (
        "<div>Intro <strong>bold</strong><br>second<br><br>New para</div>"
        "<ul><li>a</li><li><strong>b</strong></li></ul>"
    )
    assert blocks(html) == [
        ("p", [[("Intro ", False), ("bold", True)], [("second", False)]]),
        ("p", [[("New para", False)]]),
        ("ul", [[("a", False)], [("b", True)]]),
    ]


def test_old_markup_answers_convert_to_rich_text():
    assert from_simple_markup("Intro\n\n- one\n- **two**") == (
        "<div>Intro</div><ul><li>one</li><li><strong>two</strong></li></ul>"
    )


# --- Starting and drafting ---------------------------------------------------------------


def test_report_needs_a_published_questionnaire(as_sam, assignment, project):
    response = as_sam.get(reverse("reports:report", args=[assignment.pk]), follow=True)
    assert "hasn't published the questionnaire" in response.content.decode()
    assert not Report.objects.exists()


def test_opening_starts_the_report_without_changing_the_stage(as_sam, assignment, published):
    html = as_sam.get(reverse("reports:report", args=[assignment.pk])).content.decode()
    report = Report.objects.get()
    assert report.questionnaire_version == published
    assert "Key facts" in html and "Commercial" in html
    assignment.refresh_from_db()
    assert assignment.stage == Assignment.Stage.NOT_STARTED


def test_autosave_each_answer_type(as_sam, report):
    for key, value in [
        ("structure", "Per user."),
        ("min-users", "100"),
        ("first-enquiry", "2026-10-21T10:42"),
    ]:
        question = q(report, key)
        response = as_sam.post(
            reverse("reports:autosave", args=[report.pk, question.pk]), {f"q-{question.pk}": value}, **HTMX
        )
        assert response.status_code == 200 and b"Saved" in response.content
    answers = services.answers_by_question(report)
    assert answers[q(report, "structure").pk].text == "Per user."  # plain text is kept as-is
    assert timezone.localtime(answers[q(report, "first-enquiry").pk].datetime_value).hour == 10


def test_autosave_rejects_a_bad_date(as_sam, report):
    question = q(report, "first-enquiry")
    response = as_sam.post(
        reverse("reports:autosave", args=[report.pk, question.pk]), {f"q-{question.pk}": "yesterday"}, **HTMX
    )
    assert response.status_code == 422


def test_save_section_and_move_on(as_sam, report):
    first, second = q(report, "structure"), q(report, "min-users")
    response = as_sam.post(
        reverse("reports:save_section", args=[report.pk]),
        {f"q-{first.pk}": "Banded", f"q-{second.pk}": "250", "go": "evidence"},
    )
    assert response["Location"].endswith("?view=evidence")
    assert services.answers_by_question(report)[second.pk].text == "250"


def test_notes_are_saved(as_sam, report):
    as_sam.post(
        reverse("reports:save_notes", args=[report.pk]), {"notes": "They asked which bank I work for."}
    )
    report.refresh_from_db()
    assert report.notes == "They asked which bank I work for."


def test_other_shoppers_cannot_open_or_edit_the_report(client, report, priya):
    client.force_login(priya)
    assert client.get(reverse("reports:report", args=[report.assignment_id])).status_code == 404
    question = q(report, "structure")
    assert client.post(reverse("reports:autosave", args=[report.pk, question.pk]), {}).status_code == 404
    assert client.post(reverse("reports:submit", args=[report.pk])).status_code == 404


def test_questions_from_another_version_are_refused(as_sam, report, project):
    other = QuestionnaireVersion.objects.create(project=project, number=2)
    s = Section.objects.create(version=other, title="X")
    stray = Question.objects.create(section=s, key="stray", prompt="Stray?")
    assert as_sam.post(reverse("reports:autosave", args=[report.pk, stray.pk]), {}).status_code == 404


# --- Submitting --------------------------------------------------------------------------


def test_submit_lists_what_is_missing(as_sam, report):
    services.save_answer(report, q(report, "structure"), "Per user.")
    response = as_sam.post(reverse("reports:submit", args=[report.pk]), **HTMX)
    html = htmllib.unescape(response.content.decode())
    assert response.status_code == 200
    assert "You can't submit yet" in html
    assert "Minimum users?" in html and "First enquiry" in html
    report.refresh_from_db()
    assert report.status == Report.Status.DRAFT


def test_submit_requires_the_latest_brief_to_be_confirmed(as_sam, report, project):
    projects.update_brief(project, "Approach as a genuine buyer.")
    answer_everything(report)
    html = as_sam.post(reverse("reports:submit", args=[report.pk]), **HTMX).content.decode()
    assert "Confirm you have read the latest brief" in html


def test_submit_freezes_a_snapshot_completes_and_locks(as_sam, report, sam):
    answer_everything(report)
    response = as_sam.post(reverse("reports:submit", args=[report.pk]), follow=True)
    assert "Report submitted to Customer Wise" in response.content.decode()
    report.refresh_from_db()
    assert report.status == Report.Status.SUBMITTED and report.version_no == 1
    assert report.assignment.stage == Assignment.Stage.IN_REVIEW
    submission = report.submissions.get()
    assert submission.snapshot["sections"][1]["questions"][0]["text"] == "<div>Per user, billed yearly.</div>"
    question = q(report, "structure")
    as_sam.post(
        reverse("reports:autosave", args=[report.pk, question.pk]), {f"q-{question.pk}": "Changed"}, **HTMX
    )
    assert services.answers_by_question(report)[question.pk].text == "<div>Per user, billed yearly.</div>"
    html = as_sam.get(reverse("reports:report", args=[report.assignment_id])).content.decode()
    assert "Submitted" in html and "to Customer Wise</button>" not in html


# --- Moving drafts to a new questionnaire version ------------------------------------------


def test_publishing_can_move_drafts_and_keeps_removed_answers(report, project):
    services.save_answer(report, q(report, "structure"), "Kept")
    services.save_answer(report, q(report, "min-users"), "Removed later")
    draft = questionnaires.new_draft_from(report.questionnaire_version)
    Question.objects.get(section__version=draft, key="min-users").delete()
    Question.objects.create(
        section=Section.objects.get(version=draft, title="Commercial"), key="new", prompt="New?"
    )
    assert questionnaires.open_report_drafts(draft) == 1
    questionnaires.publish(draft, move_drafts=True)
    report.refresh_from_db()
    assert report.questionnaire_version == draft
    answers = {a.question.key: a.text for a in report.answers.select_related("question")}
    assert answers == {"structure": "Kept"}
    assert report.removed_answers[0]["text"] == "Removed later"


def test_publishing_can_leave_drafts_on_the_old_version(report):
    old = report.questionnaire_version
    draft = questionnaires.new_draft_from(old)
    questionnaires.publish(draft, move_drafts=False)
    report.refresh_from_db()
    assert report.questionnaire_version == old


# --- Evidence -----------------------------------------------------------------------------


def ooxml(main, macros=False):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(f"{main}document.xml", "<doc/>")
        if macros:
            z.writestr(f"{main}vbaProject.bin", b"\x00")
    return buf.getvalue()


GOOD = {
    "quote.pdf": b"%PDF-1.7\n...",
    "shot.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,
    "photo.jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 20,
    "pricing.docx": ooxml("word/"),
    "prices.xlsx": ooxml("xl/"),
    "reply.eml": b"From: Sales <sales@example.com>\nSubject: Hi\n\nBody",
    "reply.msg": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 20,
    "call.vtt": b"WEBVTT\n\n00:00.000 --> 00:01.000\nHello",
    "call.srt": b"1\n00:00:00,000 --> 00:00:01,000\nHello",
    "notes.txt": "Plain notes £".encode(),
}

BAD = {
    "macros.docx": (ooxml("word/", macros=True), "macros"),
    "old.doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "can't be uploaded"),
    "macro.docm": (ooxml("word/"), "can't be uploaded"),
    "call.mp3": (b"ID3\x03", "No audio"),
    "invoice.pdf": (b"MZ\x90\x00 not a pdf", "doesn't look like a real .pdf"),
    "sheet.xlsx": (ooxml("word/"), "doesn't look like a real .xlsx"),
    "empty.txt": (b"", "is empty"),
}


class Page:
    """A response whose text is HTML-unescaped, so messages with apostrophes can be matched."""

    def __init__(self, response):
        self.response = response
        self.text = htmllib.unescape(response.content.decode())


def upload(client, report, name, content):
    return Page(
        client.post(
            reverse("reports:evidence_upload", args=[report.pk]),
            {"files": [SimpleUploadedFile(name, content)]},
            follow=True,
        )
    )


@pytest.mark.parametrize("name", sorted(GOOD))
def test_allowed_evidence_types_upload(as_sam, report, name):
    upload(as_sam, report, name, GOOD[name])
    evidence = EvidenceFile.objects.get()
    assert evidence.original_name == name
    assert evidence.scan_status == EvidenceFile.ScanStatus.NOT_SCANNED
    assert name not in evidence.file.name and evidence.file.name.startswith("evidence/")


@pytest.mark.parametrize("name", sorted(BAD))
def test_disallowed_evidence_is_rejected(as_sam, report, name):
    content, message = BAD[name]
    html = upload(as_sam, report, name, content).text
    assert message in html
    assert not EvidenceFile.objects.exists()


def test_files_over_25mb_are_rejected(as_sam, report, settings):
    settings.EVIDENCE_MAX_UPLOAD_BYTES = 10
    html = upload(as_sam, report, "big.pdf", b"%PDF-" + b"0" * 20).text
    assert "larger than" in html
    assert not EvidenceFile.objects.exists()


def test_categories_are_suggested_and_can_change(as_sam, report):
    upload(as_sam, report, "reply.eml", GOOD["reply.eml"])
    evidence = EvidenceFile.objects.get()
    assert evidence.category == "email"
    as_sam.post(reverse("reports:evidence_category", args=[evidence.pk]), {"category": "other"})
    evidence.refresh_from_db()
    assert evidence.category == "other"


def test_download_is_private_to_the_shopper_and_staff(client, as_sam, report, priya, staff):
    upload(as_sam, report, "quote.pdf", GOOD["quote.pdf"])
    evidence = EvidenceFile.objects.get()
    url = reverse("reports:evidence_download", args=[evidence.pk])
    response = as_sam.get(url)
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment")
    assert b"".join(response.streaming_content).startswith(b"%PDF")
    as_sam.logout()
    assert client.get(url).status_code == 302
    client.force_login(priya)
    assert client.get(url).status_code == 404
    client.force_login(staff)
    assert client.get(url).status_code == 200


def test_delete_hides_from_shopper_but_keeps_for_staff(client, as_sam, report, staff):
    upload(as_sam, report, "quote.pdf", GOOD["quote.pdf"])
    evidence = EvidenceFile.objects.get()
    as_sam.post(reverse("reports:evidence_delete", args=[evidence.pk]))
    evidence.refresh_from_db()
    assert evidence.deleted_at is not None
    assert as_sam.get(reverse("reports:evidence_download", args=[evidence.pk])).status_code == 404
    client.force_login(staff)
    assert client.get(reverse("reports:evidence_download", args=[evidence.pk])).status_code == 200


def test_evidence_is_locked_after_submitting(as_sam, report):
    upload(as_sam, report, "quote.pdf", GOOD["quote.pdf"])
    answer_everything(report)
    services.submit(report, report.assignment.shopper)
    html = upload(as_sam, report, "more.pdf", GOOD["quote.pdf"]).text
    assert "can't be changed after the report is submitted" in html
    evidence = EvidenceFile.objects.get()
    as_sam.post(reverse("reports:evidence_delete", args=[evidence.pk]))
    evidence.refresh_from_db()
    assert evidence.deleted_at is None
    assert services.answers_by_question(report)  # still there
    assert report.submissions.get().snapshot["evidence"][0]["name"] == "quote.pdf"


def test_assignment_page_links_to_the_report(as_sam, assignment, published):
    html = as_sam.get(reverse("shopper:assignment", args=[assignment.pk])).content.decode()
    assert "Start report" in html
    as_sam.get(reverse("reports:report", args=[assignment.pk]))
    html = as_sam.get(reverse("shopper:assignment", args=[assignment.pk])).content.decode()
    assert "Continue report" in html and "0 of 3 questions" in html


def test_rich_answers_are_cleaned_and_blank_ones_count_as_missing(as_sam, report):
    question = q(report, "structure")
    url = reverse("reports:autosave", args=[report.pk, question.pk])
    as_sam.post(url, {f"q-{question.pk}": "<div>Per <b>user</b><script>x()</script></div>"}, **HTMX)
    answer = services.answers_by_question(report)[question.pk]
    assert answer.text == "<div>Per <strong>user</strong></div>"
    as_sam.post(url, {f"q-{question.pk}": "<div><br></div>"}, **HTMX)
    answer.refresh_from_db()
    assert not answer.is_answered
    assert question in services.missing_questions(report)


def test_report_page_uses_the_rich_text_editor_under_a_strict_csp(as_sam, report):
    services.save_answer(report, q(report, "structure"), "<div>Per <strong>user</strong></div>")
    response = as_sam.get(
        reverse("reports:report", args=[report.assignment_id]) + f"?s={q(report, 'structure').section_id}"
    )
    html = response.content.decode()
    assert "<trix-editor" in html and 'data-trix-attribute="bold"' in html
    nonce = html.split('name="trix-csp-nonce" content="')[1].split('"')[0]
    csp = response["Content-Security-Policy"]
    assert f"style-src 'self' 'nonce-{nonce}'" in csp
    assert "unsafe-inline" not in csp
    assert 'value="&lt;div&gt;Per &lt;strong&gt;user&lt;/strong&gt;&lt;/div&gt;"' in html


def test_submitted_report_shows_formatted_answers(as_sam, report):
    answer_everything(report)
    services.save_answer(
        report, q(report, "structure"), "<div>Per <strong>user</strong></div><ul><li>Banded</li></ul>"
    )
    services.submit(report, report.assignment.shopper)
    html = as_sam.get(
        reverse("reports:report", args=[report.assignment_id]) + f"?s={q(report, 'structure').section_id}"
    ).content.decode()
    assert "<trix-editor" not in html
    assert "<div>Per <strong>user</strong></div><ul><li>Banded</li></ul>" in html


def test_submit_dialog_takes_an_optional_comment(as_sam, report):
    answer_everything(report)
    dialog = htmllib.unescape(
        as_sam.get(reverse("reports:submit", args=[report.pk]), **HTMX).content.decode()
    )
    assert "Comment for Customer Wise" in dialog and "Reply to Customer Wise" not in dialog
    response = as_sam.post(
        reverse("reports:submit", args=[report.pk]), {"comment": "Their pricing PDF is attached."}, **HTMX
    )
    assert response.status_code == 204
    assert report.submissions.get().shopper_comment == "Their pricing PDF is attached."


def test_submitted_report_is_locked_while_in_review(as_sam, report):
    answer_everything(report)
    services.submit(report, report.assignment.shopper)
    report.assignment.refresh_from_db()
    assert report.assignment.stage == Assignment.Stage.IN_REVIEW
    html = htmllib.unescape(
        as_sam.get(reverse("shopper:assignment", args=[report.assignment_id])).content.decode()
    )
    assert "Report submitted and in review" in html and "Add to log" not in html
    assert (
        as_sam.post(
            reverse("shopper:assignment_stage", args=[report.assignment_id]), {"stage": "planned"}
        ).status_code
        == 302
    )
    report.assignment.refresh_from_db()
    assert report.assignment.stage == Assignment.Stage.IN_REVIEW
