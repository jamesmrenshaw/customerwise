"""Staff review of submitted reports (CW-045 to CW-049): comment, return, accept."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.fieldwork.models import Assignment

from .models import Report, ReviewComment


def _record(actor, action, report, **data):
    from apps.audit.services import record

    target = report.assignment.target
    record(
        actor, action, report, project=target.project, target=target.name, version=report.version_no, **data
    )


def latest_submission(report):
    return report.submissions.order_by("-number").first()


def draft_comments(report):
    return report.comments.filter(sent_at__isnull=True).select_related("question", "author")


def previous_round(report, submission):
    """Comments from the return before this submission, with any replies the shopper gave."""
    if submission is None or submission.number < 2:
        return ReviewComment.objects.none()
    return report.comments.filter(
        sent_at__isnull=False, submission__number=submission.number - 1
    ).select_related("question", "author")


def returned_comments(report):
    """Comments the shopper should act on: those sent when the latest submission was returned."""
    if report.status != Report.Status.RETURNED:
        return ReviewComment.objects.none()
    return report.comments.filter(sent_at__isnull=False, submission=latest_submission(report)).select_related(
        "question", "author"
    )


def _require_submitted(report):
    if report.status != Report.Status.SUBMITTED:
        raise ValidationError("Only a submitted report can be reviewed.")


def add_comment(report, *, author, text, question=None):
    _require_submitted(report)
    text = (text or "").strip()
    if not text:
        raise ValidationError("Write a comment first.")
    if question is not None and question.section.version_id != report.questionnaire_version_id:
        raise ValidationError("That question isn't part of this report.")
    return ReviewComment.objects.create(
        report=report,
        question=question,
        question_key=question.key if question else "",
        text=text,
        author=author,
    )


def update_comment(comment, text):
    if comment.sent_at:
        raise ValidationError("This comment has already been sent to the shopper.")
    text = (text or "").strip()
    if not text:
        raise ValidationError("Write a comment first.")
    comment.text = text
    comment.save(update_fields=["text", "updated_at"])


def delete_comment(comment):
    if comment.sent_at:
        raise ValidationError("This comment has already been sent to the shopper.")
    comment.delete()


@transaction.atomic
def return_report(report, *, by, message=""):
    """Send the draft comments to the shopper and reopen the report for changes (CW-048)."""
    report = Report.objects.select_for_update().get(pk=report.pk)
    _require_submitted(report)
    message = (message or "").strip()
    drafts = list(draft_comments(report))
    if not drafts and not message:
        raise ValidationError("Add at least one comment or a message so the shopper knows what to change.")
    submission = latest_submission(report)
    now = timezone.now()
    if message:
        drafts.append(ReviewComment.objects.create(report=report, text=message, author=by))
    ReviewComment.objects.filter(pk__in=[c.pk for c in drafts]).update(sent_at=now, submission=submission)
    report.status = Report.Status.RETURNED
    report.save(update_fields=["status", "updated_at"])
    Assignment.objects.filter(pk=report.assignment_id).update(
        stage=Assignment.Stage.REPORT_DRAFTING, returned=True, last_activity_at=now
    )
    _record(by, "report.returned", report, comments=len(drafts))
    from .notifications import report_returned

    report_returned(report, comments=len(drafts), message=message)
    return report


@transaction.atomic
def accept_report(report, *, by):
    """Accept the submitted report: it leaves the review queue and stays read-only."""
    report = Report.objects.select_for_update().get(pk=report.pk)
    _require_submitted(report)
    if draft_comments(report).exists():
        raise ValidationError(
            "You have comments that haven't been sent. Return the report, or delete them first."
        )
    report.status = Report.Status.ACCEPTED
    report.accepted_at = timezone.now()
    report.accepted_by = by
    report.save(update_fields=["status", "accepted_at", "accepted_by", "updated_at"])
    # Only an accepted report makes the assignment Complete (refined 6 Nov 2026).
    Assignment.objects.filter(pk=report.assignment_id).update(
        stage=Assignment.Stage.COMPLETE, last_activity_at=report.accepted_at
    )
    _record(by, "report.accepted", report)
    from .notifications import report_accepted

    report_accepted(report)
    return report
