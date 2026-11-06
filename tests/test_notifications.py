"""Report emails: submitted (to the project's notification email), returned and accepted (to the shopper)."""

import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts.models import User
from apps.fieldwork import services as fieldwork
from apps.projects.models import ClientOrganisation, Project, Target
from apps.questionnaires import services as questionnaires
from apps.questionnaires.models import Question, QuestionnaireVersion, Section
from apps.reports import review, services

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff():
    return User.objects.create_staff("alex@customerwise.example", full_name="Alex Morgan")


@pytest.fixture
def sam():
    return User.objects.create_user("sam@example.com", full_name="Sam Okafor")


@pytest.fixture
def report(sam, staff):
    client = ClientOrganisation.objects.create(name="Harbourside Bank")
    project = Project.objects.create(
        client=client,
        internal_name="Harbourside messaging",
        client_facing_name="Messaging review",
        shopper_facing_name="Mobile capture review",
        status=Project.Status.ACTIVE,
        notification_email="reviews@customerwise.example",
    )
    v = QuestionnaireVersion.objects.create(project=project, number=1)
    q = Question.objects.create(
        section=Section.objects.create(version=v, title="C"), key="k", prompt="Q?", answer_type="short_text"
    )
    questionnaires.publish(v, staff)
    a = fieldwork.assign(
        Target.objects.create(project=project, name="Northwind"), sam, by=staff, approval_confirmed=True
    )
    report = services.start_report(a)
    services.save_answer(report, q, "Answer")
    return report


def test_submitting_emails_the_projects_notification_address(report, sam, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        services.submit(report, sam, comment="Pricing PDF attached.")
    [email] = mail.outbox
    assert email.to == ["reviews@customerwise.example"]
    assert email.subject == "Report submitted: Northwind (Harbourside messaging)"
    assert "Sam Okafor has submitted their report on Northwind" in email.body
    assert "Pricing PDF attached." in email.body
    assert "http://localhost:8000" + reverse("staff:review", args=[report.pk]) in email.body


def test_return_and_accept_email_the_shopper_without_client_details(
    report, sam, staff, django_capture_on_commit_callbacks
):
    services.submit(report, sam)
    report.refresh_from_db()
    review.add_comment(report, author=staff, text="More detail please")
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        review.return_report(report, by=staff, message="Nearly there")
    [returned] = mail.outbox
    assert returned.to == ["sam@example.com"]
    assert returned.subject == "Your report on Northwind has been returned for changes"
    assert "with 2 comments" in returned.body and "Nearly there" in returned.body
    assert "http://localhost:8000" + reverse("reports:report", args=[report.assignment_id]) in returned.body

    report.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        services.submit(report, sam, replies={str(c.pk): "Done" for c in review.returned_comments(report)})
    resubmitted = mail.outbox[-1]
    assert resubmitted.subject.startswith("Report resubmitted: Northwind")
    assert "They replied to 2 of your review comments." in resubmitted.body

    report.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        review.accept_report(report, by=staff)
    accepted = mail.outbox[-1]
    assert accepted.to == ["sam@example.com"]
    assert accepted.subject == "Your report on Northwind has been accepted"
    assert (
        "http://localhost:8000" + reverse("shopper:assignment", args=[report.assignment_id]) in accepted.body
    )
    for email in (returned, accepted):
        assert "Mobile capture review" in email.body
        for secret in ("Harbourside", "Messaging review"):
            assert secret not in email.body and secret not in email.subject


def test_no_notification_email_means_no_submission_email(report, sam, django_capture_on_commit_callbacks):
    Project.objects.update(notification_email="")
    report.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        services.submit(report, sam)
    assert mail.outbox == []
    report.refresh_from_db()
    assert report.status == "submitted"


def test_a_failed_email_never_blocks_the_action(report, sam, settings, django_capture_on_commit_callbacks):
    settings.MAILERS = {"default": {"BACKEND": "tests.failing_mail.EmailBackend"}}
    with django_capture_on_commit_callbacks(execute=True):
        services.submit(report, sam)
    report.refresh_from_db()
    assert report.status == "submitted"


def test_emails_are_not_sent_if_the_action_fails(report, sam, django_capture_on_commit_callbacks):
    from django.core.exceptions import ValidationError

    Question.objects.create(
        section=Section.objects.get(version=report.questionnaire_version), key="k2", prompt="Unanswered?"
    )
    with django_capture_on_commit_callbacks(execute=True), pytest.raises(ValidationError):
        services.submit(report, sam)
    assert mail.outbox == []


def test_project_form_requires_a_notification_email(client, staff, report):
    client.force_login(staff)
    project = report.assignment.target.project
    response = client.post(
        reverse("staff:project_details", args=[project.pk]),
        {
            "client": project.client.pk,
            "status": "active",
            "notification_email": "",
            "internal_name": project.internal_name,
            "client_facing_name": project.client_facing_name,
            "shopper_facing_name": project.shopper_facing_name,
        },
    )
    assert response.status_code == 200 and "This field is required." in response.content.decode()
    Project.objects.update(notification_email="")
    assert (
        "No notification email" in client.get(reverse("staff:fieldwork", args=[project.pk])).content.decode()
    )


def html_part(email):
    [(html, mimetype)] = email.alternatives
    assert mimetype == "text/html"
    return html


def test_emails_are_html_with_the_logo_and_a_text_version(
    report, sam, staff, django_capture_on_commit_callbacks
):
    from apps.accounts import services as accounts

    accounts.send_invitation(sam, sent_by=staff)
    accounts.request_login_link("sam@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        report = services.submit(report, sam, comment="Line one\n<b>bold?</b>")
    with django_capture_on_commit_callbacks(execute=True):
        review.add_comment(report, author=staff, text="More detail")
        review.return_report(report, by=staff)
    report.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        report = services.submit(report, sam)
    with django_capture_on_commit_callbacks(execute=True):
        review.accept_report(report, by=staff)

    assert len(mail.outbox) == 6
    for email in mail.outbox:
        html = html_part(email)
        assert '<img src="http://localhost:8000/static/img/email-logo.png"' in html
        assert 'alt="Customer Wise"' in html
        [link] = [w for w in email.body.split() if w.startswith("http://localhost:8000/")]
        assert f'href="{link}"' in html
        assert "<html" not in email.body  # the text part stays plain
    submitted = html_part(mail.outbox[2])
    assert "Line one<br>&lt;b&gt;bold?&lt;/b&gt;" in submitted  # typed text is escaped


def test_the_email_logo_is_a_png_that_exists():
    from pathlib import Path

    from django.conf import settings

    logo = Path(settings.BASE_DIR, "static", "img", "email-logo.png").read_bytes()
    assert logo.startswith(b"\x89PNG")
