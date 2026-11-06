"""Fieldwork monitoring and report review (screens 05 and 06; CUS-68, CUS-80 to CUS-84)."""

import html as htmllib
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.fieldwork import services as fieldwork
from apps.fieldwork.models import Activity, Assignment
from apps.projects.models import ClientOrganisation, Project, Target
from apps.questionnaires import services as questionnaires
from apps.questionnaires.models import Question, QuestionnaireVersion, Section
from apps.reports import review, services
from apps.reports.models import Report

pytestmark = pytest.mark.django_db
HTMX = {"HTTP_HX_REQUEST": "true"}


def text(response):
    return htmllib.unescape(response.content.decode())


@pytest.fixture
def staff():
    return User.objects.create_staff("alex@customerwise.example", full_name="Alex Morgan")


@pytest.fixture
def sam():
    return User.objects.create_user("sam@example.com", full_name="Sam Okafor")


@pytest.fixture
def project():
    client = ClientOrganisation.objects.create(name="Harbourside Bank")
    project = Project.objects.create(
        client=client,
        internal_name="Harbourside: messaging vendors",
        client_facing_name="Messaging review",
        shopper_facing_name="Mobile messaging capture review",
        status=Project.Status.ACTIVE,
    )
    v = QuestionnaireVersion.objects.create(project=project, number=1)
    facts = Section.objects.create(version=v, title="Key facts", display_style="key_facts", order=10)
    commercial = Section.objects.create(version=v, title="Commercial", order=20)
    feedback = Section.objects.create(version=v, title="Feedback", display_style="headings", order=30)
    Question.objects.create(
        section=facts, key="method", prompt="Method of first response", answer_type="short_text"
    )
    Question.objects.create(section=commercial, key="pricing", prompt="How does pricing work?")
    Question.objects.create(section=feedback, key="strengths", prompt="Key strengths?")
    questionnaires.publish(v)
    return project


@pytest.fixture
def assignment(project, sam):
    return fieldwork.assign(
        Target.objects.create(project=project, name="Northwind"), sam, approval_confirmed=True
    )


def q(report, key):
    return Question.objects.get(section__version=report.questionnaire_version, key=key)


@pytest.fixture
def submitted(assignment, sam):
    report = services.start_report(assignment)
    services.save_answer(report, q(report, "method"), "Phone call, same day")
    services.save_answer(report, q(report, "pricing"), "<div>Per user, <strong>banded</strong></div>")
    services.save_answer(report, q(report, "strengths"), "<ul><li>Fast</li></ul>")
    return services.submit(report, sam)


@pytest.fixture
def as_staff(client, staff):
    client.force_login(staff)
    return client


# --- Fieldwork monitoring ----------------------------------------------------------------


def test_project_opens_on_fieldwork(as_staff, project):
    assert as_staff.get(reverse("staff:project", args=[project.pk]))["Location"] == reverse(
        "staff:fieldwork", args=[project.pk]
    )


def test_fieldwork_filters_and_flags(as_staff, project, submitted, sam):
    stalled = fieldwork.assign(
        Target.objects.create(project=project, name="Keystone"), sam, approval_confirmed=True
    )
    Assignment.objects.filter(pk=stalled.pk).update(last_activity_at=timezone.now() - timedelta(days=9))
    unable = fieldwork.assign(
        Target.objects.create(project=project, name="Halden"), sam, approval_confirmed=True
    )
    fieldwork.mark_unable(unable, "no_response", "Four attempts")
    url = reverse("staff:fieldwork", args=[project.pk])
    page = text(as_staff.get(url))
    assert "Awaiting review" in page and "Stalled · 9 days" in page and "Unable to complete" in page
    review_only = text(as_staff.get(url + "?show=review"))
    assert "Northwind" in review_only and "Keystone" not in review_only
    assert "Review report" in review_only
    stalled_only = text(as_staff.get(url + "?show=stalled"))
    assert "Keystone" in stalled_only and "Northwind" not in stalled_only
    assert "Four attempts" in text(as_staff.get(url + "?show=unable"))


def test_complete_and_unable_are_never_stalled(as_staff, project, submitted):
    Assignment.objects.update(last_activity_at=timezone.now() - timedelta(days=30))
    assert "Northwind" not in text(
        as_staff.get(reverse("staff:fieldwork", args=[project.pk]) + "?show=stalled")
    )


def test_staff_assignment_page_shows_edits_and_deletions(as_staff, assignment, sam):
    activity = fieldwork.add_activity(
        assignment, by=sam, type="email", occurred_at=timezone.now(), notes="First"
    )
    fieldwork.update_activity(activity, notes="First, edited")
    gone = fieldwork.add_activity(assignment, by=sam, type="note", occurred_at=timezone.now(), notes="Oops")
    fieldwork.delete_activity(gone)
    page = text(as_staff.get(reverse("staff:assignment", args=[assignment.pk])))
    assert "First, edited" in page and "Edited 1 time" in page
    assert "Oops" in page and "Deleted" in page
    assert Activity.objects.count() == 2


def test_shoppers_cannot_reach_review_screens(client, sam, submitted, project):
    client.force_login(sam)
    for url in [
        reverse("staff:fieldwork", args=[project.pk]),
        reverse("staff:assignment", args=[submitted.assignment_id]),
        reverse("staff:review", args=[submitted.pk]),
        reverse("staff:return_report", args=[submitted.pk]),
    ]:
        assert client.get(url).status_code == 404
    assert client.post(reverse("staff:accept_report", args=[submitted.pk])).status_code == 404


# --- Review screen -----------------------------------------------------------------------


def test_review_shows_the_report_by_display_style(as_staff, submitted):
    page = as_staff.get(reverse("staff:review", args=[submitted.pk])).content.decode()
    assert (
        '<div class="key-facts__row" id="q-method"><dt>Method of first response</dt>'
        "<dd><span>Phone call, same day" in page
    )
    assert '<table class="qa-table">' in page and "<strong>banded</strong>" in page
    assert "<h4>Key strengths?</h4>" in page and "<li>Fast</li>" in page
    assert "Awaiting review" in page and "Accept report" in page


def test_comments_are_drafts_until_returned(as_staff, client, submitted, sam):
    url = reverse("staff:comment_add", args=[submitted.pk])
    as_staff.post(url, {"question": "pricing", "text": "What's the price at 250 users?"})
    assert review.draft_comments(submitted).count() == 1
    client.force_login(sam)
    page = text(
        client.get(
            reverse("reports:report", args=[submitted.assignment_id])
            + f"?s={q(submitted, 'pricing').section_id}"
        )
    )
    assert "price at 250 users" not in page


def test_comments_can_be_edited_and_deleted_before_returning(as_staff, submitted, staff):
    comment = review.add_comment(submitted, author=staff, text="Draft", question=q(submitted, "pricing"))
    as_staff.post(reverse("staff:comment_update", args=[comment.pk]), {"text": "Better"})
    comment.refresh_from_db()
    assert comment.text == "Better"
    as_staff.post(reverse("staff:comment_update", args=[comment.pk]), {"delete": "1"})
    assert not review.draft_comments(submitted).exists()


def test_return_needs_a_comment_or_message(as_staff, submitted):
    response = as_staff.post(reverse("staff:return_report", args=[submitted.pk]), {"message": ""}, **HTMX)
    assert "Add at least one comment or a message" in text(response)
    submitted.refresh_from_db()
    assert submitted.status == Report.Status.SUBMITTED


def test_full_return_and_resubmit_cycle(as_staff, client, submitted, staff, sam):
    review.add_comment(
        submitted, author=staff, text="Give the banded prices.", question=q(submitted, "pricing")
    )
    response = as_staff.post(
        reverse("staff:return_report", args=[submitted.pk]), {"message": "Nearly there."}, **HTMX
    )
    assert response.status_code == 204
    submitted.refresh_from_db()
    assignment = submitted.assignment
    assignment.refresh_from_db()
    assert submitted.status == Report.Status.RETURNED
    assert assignment.stage == Assignment.Stage.REPORT_DRAFTING and assignment.returned
    first = submitted.submissions.get(number=1)
    assert first.comments.count() == 2  # recorded against the submission they refer to

    client.force_login(sam)
    listing = text(client.get(reverse("shopper:assignments")))
    assert "Report returned for changes: Northwind" in listing and "Returned: 2 comments" in listing
    report_page = text(
        client.get(
            reverse("reports:report", args=[assignment.pk]) + f"?s={q(submitted, 'pricing').section_id}"
        )
    )
    assert "Comments from Customer Wise" in report_page
    assert "Give the banded prices." in report_page and "Nearly there." in report_page
    assert "Resubmit to Customer Wise" in report_page

    # Editing is unlocked; the shopper fixes it and resubmits.
    pricing = q(submitted, "pricing")
    client.post(
        reverse("reports:autosave", args=[submitted.pk, pricing.pk]),
        {f"q-{pricing.pk}": "<div>250 users: £20; 1,000 users: £16</div>"},
        **HTMX,
    )
    client.post(reverse("reports:submit", args=[submitted.pk]))
    submitted.refresh_from_db()
    assignment.refresh_from_db()
    assert submitted.status == Report.Status.SUBMITTED and submitted.version_no == 2
    assert assignment.stage == Assignment.Stage.IN_REVIEW and not assignment.returned
    assert "Comments from Customer Wise" not in text(
        client.get(reverse("reports:report", args=[assignment.pk]))
    )

    # Staff see version 2, and the earlier return in the history.
    as_staff.force_login(staff)  # as_staff and client are the same session
    page = text(as_staff.get(reverse("staff:review", args=[submitted.pk])))
    assert "Version 2" in page and "£20" in page
    assert "Returned" in page and "Give the banded prices." in page


def test_accept_takes_it_off_the_queue_and_locks_it(as_staff, submitted, staff, project):
    response = as_staff.post(reverse("staff:accept_report", args=[submitted.pk]), follow=True)
    assert "accepted" in text(response)
    submitted.refresh_from_db()
    assert submitted.status == Report.Status.ACCEPTED and submitted.accepted_by == staff
    submitted.assignment.refresh_from_db()
    assert submitted.assignment.stage == Assignment.Stage.COMPLETE
    assert "Northwind" not in text(
        as_staff.get(reverse("staff:fieldwork", args=[project.pk]) + "?show=review")
    )
    with pytest.raises(ValidationError):
        review.add_comment(submitted, author=staff, text="Late")
    with pytest.raises(ValidationError):
        review.return_report(submitted, by=staff, message="Too late")


def test_cannot_accept_with_unsent_comments(as_staff, submitted, staff):
    review.add_comment(submitted, author=staff, text="Unsent")
    response = as_staff.post(reverse("staff:accept_report", args=[submitted.pk]), follow=True)
    assert "haven't been sent" in text(response)
    submitted.refresh_from_db()
    assert submitted.status == Report.Status.SUBMITTED


def test_returned_report_can_only_be_reviewed_again_after_resubmission(as_staff, submitted, staff):
    review.return_report(submitted, by=staff, message="Fix it")
    page = text(as_staff.get(reverse("staff:review", args=[submitted.pk])))
    assert "Returned to Sam Okafor" in page and "Accept report" not in page


def test_in_review_is_never_stalled_or_in_progress(as_staff, project, submitted):
    Assignment.objects.update(last_activity_at=timezone.now() - timedelta(days=30))
    url = reverse("staff:fieldwork", args=[project.pk])
    assert "Northwind" not in text(as_staff.get(url + "?show=stalled"))
    assert "Northwind" not in text(as_staff.get(url + "?show=progress"))
    assert "In review" in text(as_staff.get(url))


def test_shopper_replies_to_comments_and_staff_see_them(as_staff, client, submitted, staff, sam):
    review.add_comment(
        submitted, author=staff, text="Give the banded prices.", question=q(submitted, "pricing")
    )
    review.return_report(submitted, by=staff, message="Nearly there.")
    submitted.refresh_from_db()
    pricing_comment, overall = list(review.returned_comments(submitted).order_by("created_at"))
    client.force_login(sam)
    dialog = text(client.get(reverse("reports:submit", args=[submitted.pk]), **HTMX))
    assert "Reply to Customer Wise's comments" in dialog and "Give the banded prices." in dialog
    response = client.post(
        reverse("reports:submit", args=[submitted.pk]),
        {
            f"reply-{pricing_comment.pk}": "They won't give prices until a call with procurement.",
            f"reply-{overall.pk}": "",
            "comment": "Nothing else changed.",
        },
        **HTMX,
    )
    assert response.status_code == 204
    pricing_comment.refresh_from_db()
    assert pricing_comment.reply.startswith("They won't give prices")
    as_staff.force_login(staff)
    page = text(as_staff.get(reverse("staff:review", args=[submitted.pk])))
    assert "From Sam Okafor with version 2" in page and "Nothing else changed." in page
    assert "Your earlier comment" in page and "Sam Okafor replied" in page
    assert "They won't give prices until a call with procurement." in page


def test_stamp_shows_date_and_uk_time():
    from datetime import UTC, datetime

    from apps.core.templatetags.ui import stamp

    # 6 Nov is GMT; 6 Jul is BST (UTC+1)
    assert (
        stamp(datetime(2026, 11, 6, 16, 5, tzinfo=UTC))
        == '<time datetime="2026-11-06T16:05+00:00">6 Nov 2026, 16:05</time>'
    )
    assert ">6 Jul 2026, 17:05<" in stamp(datetime(2026, 7, 6, 16, 5, tzinfo=UTC))
    assert stamp(None) == ""


def test_comments_replies_and_notes_show_when(as_staff, client, submitted, staff, sam):
    from apps.core.templatetags.ui import stamp

    with pytest.raises(ValidationError):  # submitted reports can't be changed
        services.save_notes(submitted, "Asked which bank")
    comment = review.add_comment(
        submitted, author=staff, text="More detail", question=q(submitted, "pricing")
    )
    page = as_staff.get(reverse("staff:review", args=[submitted.pk])).content.decode()
    assert stamp(comment.created_at) in page
    review.return_report(submitted, by=staff, message="Nearly")
    comment.refresh_from_db()
    client.force_login(sam)
    report_page = client.get(
        reverse("reports:report", args=[submitted.assignment_id]) + f"?s={q(submitted, 'pricing').section_id}"
    ).content.decode()
    assert stamp(comment.sent_at) in report_page
