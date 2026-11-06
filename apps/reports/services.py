"""Report drafting and submission (CW-036 to CW-044, D06)."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.fieldwork.models import Assignment
from apps.fieldwork.services import shopper_can_edit
from apps.projects.services import has_acknowledged_latest_brief
from apps.questionnaires.models import Question, QuestionnaireVersion
from apps.questionnaires.services import current_version

from .formatting import clean
from .models import Answer, Report, ReportSubmission


class NoQuestionnaire(ValidationError):
    pass


def start_report(assignment):
    """The assignment's report, created on first use with the latest published questionnaire.

    Opening the report doesn't change the fieldwork stage (D08).
    """
    report = Report.objects.filter(assignment=assignment).select_related("questionnaire_version").first()
    if report:
        return report
    version = current_version(assignment.target.project)
    if version is None:
        raise NoQuestionnaire("Customer Wise hasn't published the questionnaire for this project yet.")
    return Report.objects.create(assignment=assignment, questionnaire_version=version)


def can_edit(report):
    return report.is_editable and shopper_can_edit(report.assignment)


def _require_editable(report):
    if not can_edit(report):
        raise ValidationError("This report has been submitted and can't be changed.")


def questions(report):
    return Question.objects.filter(section__version=report.questionnaire_version).select_related("section")


def answers_by_question(report):
    return {a.question_id: a for a in report.answers.all()}


@transaction.atomic
def save_answer(report, question, value):
    """Save one answer (draft saves never require an answer; D06 checks at submit)."""
    _require_editable(report)
    if question.section.version_id != report.questionnaire_version_id:
        raise ValidationError("That question isn't part of this report.")
    answer, _ = Answer.objects.get_or_create(report=report, question=question)
    if question.answer_type == Question.AnswerType.DATETIME:
        answer.datetime_value = value or None
        answer.text = ""
    elif question.answer_type == Question.AnswerType.LONG_TEXT:
        answer.text = clean(value)  # rich text: only paragraphs, line breaks, bold and bullets survive
    else:
        answer.text = (value or "").strip()
    answer.save()
    Report.objects.filter(pk=report.pk).update(updated_at=timezone.now())
    return answer


def save_notes(report, notes):
    _require_editable(report)
    notes = notes.strip()
    if notes != report.notes:
        report.notes = notes
        report.notes_updated_at = timezone.now()
    report.save(update_fields=["notes", "notes_updated_at", "updated_at"])


def missing_questions(report):
    answered = {qid for qid, a in answers_by_question(report).items() if a.is_answered}
    return [q for q in questions(report) if q.pk not in answered]


def submission_problems(report, user):
    problems = []
    project = report.assignment.target.project
    if not has_acknowledged_latest_brief(user, project):
        problems.append({"kind": "brief", "text": "Confirm you have read the latest brief"})
    for q in missing_questions(report):
        problems.append({"kind": "question", "question": q, "text": f"{q.section.title}: {q.prompt}"})
    return problems


def snapshot(report):
    answers = answers_by_question(report)
    sections = []
    for section in report.questionnaire_version.sections.prefetch_related("questions"):
        qs = []
        for q in section.questions.all():
            a = answers.get(q.pk)
            qs.append(
                {
                    "key": q.key,
                    "prompt": q.prompt,
                    "answer_type": q.answer_type,
                    "text": a.text if a else "",
                    "datetime": a.datetime_value.isoformat() if a and a.datetime_value else None,
                }
            )
        sections.append({"title": section.title, "display_style": section.display_style, "questions": qs})
    evidence = [
        {"id": str(e.pk), "name": e.original_name, "category": e.category, "size": e.size}
        for e in report.assignment.evidence.current()
    ]
    return {
        "questionnaire_version": report.questionnaire_version.number,
        "sections": sections,
        "evidence": evidence,
        "notes": report.notes,
    }


@transaction.atomic
def submit(report, user, *, comment="", replies=None):
    """Submit for review: freeze a snapshot, move the assignment to In review and lock editing.

    comment is the shopper's message for Customer Wise. replies maps returned ReviewComment ids to the
    shopper's reply, for when they explain rather than (or as well as) change an answer.
    """
    report = (
        Report.objects.select_for_update().select_related("assignment__target__project").get(pk=report.pk)
    )
    _require_editable(report)
    problems = submission_problems(report, user)
    if problems:
        raise ValidationError([p["text"] for p in problems])
    from .review import returned_comments

    now = timezone.now()
    for c in returned_comments(report):
        reply = ((replies or {}).get(str(c.pk)) or "").strip()
        if reply:
            c.reply, c.replied_at = reply, now
            c.save(update_fields=["reply", "replied_at", "updated_at"])
    report.version_no += 1
    report.status = Report.Status.SUBMITTED
    report.submitted_at = now
    report.save(update_fields=["version_no", "status", "submitted_at", "updated_at"])
    ReportSubmission.objects.create(
        report=report,
        number=report.version_no,
        submitted_by=user,
        snapshot=snapshot(report),
        shopper_comment=(comment or "").strip(),
    )
    Assignment.objects.filter(pk=report.assignment_id).update(
        stage=Assignment.Stage.IN_REVIEW, returned=False, last_activity_at=now
    )
    from apps.audit.services import record

    record(
        user,
        "report.submitted",
        report,
        project=report.assignment.target.project,
        target=report.assignment.target.name,
        version=report.version_no,
        with_comment=bool((comment or "").strip()),
        replies=sum(1 for v in (replies or {}).values() if (v or "").strip()),
    )
    from .notifications import report_submitted

    report_submitted(
        report,
        comment=(comment or "").strip(),
        replies=sum(1 for v in (replies or {}).values() if (v or "").strip()),
    )
    return report


# --- Moving drafts to a new questionnaire version (D06) -----------------------------------


def open_drafts(version):
    """Reports still being drafted on other versions of this questionnaire."""
    return Report.objects.filter(
        questionnaire_version__project=version.project,
        status__in=[Report.Status.DRAFT, Report.Status.RETURNED],
    ).exclude(questionnaire_version=version)


@transaction.atomic
def move_to_version(report, version):
    """Carry answers over by question key; new questions start blank; removed answers are kept."""
    if version.status != QuestionnaireVersion.Status.PUBLISHED:
        raise ValidationError("Drafts can only move to a published version.")
    new_questions = {q.key: q for q in Question.objects.filter(section__version=version)}
    removed = list(report.removed_answers)
    for answer in report.answers.select_related("question__section"):
        target = new_questions.get(answer.question.key)
        if target is None:
            if answer.is_answered:
                removed.append(
                    {
                        "key": answer.question.key,
                        "prompt": answer.question.prompt,
                        "text": answer.text,
                        "datetime": answer.datetime_value.isoformat() if answer.datetime_value else None,
                        "from_version": report.questionnaire_version.number,
                    }
                )
            answer.delete()
        else:
            answer.question = target
            answer.save(update_fields=["question", "updated_at"])
    report.questionnaire_version = version
    report.removed_answers = removed
    report.save(update_fields=["questionnaire_version", "removed_answers", "updated_at"])
    return report
