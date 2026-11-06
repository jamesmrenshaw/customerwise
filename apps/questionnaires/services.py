"""Questionnaire versions (D06): edit a draft, publish it, start a new draft from any version."""

import json
from collections import Counter

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.text import slugify

from .models import Question, QuestionnaireVersion, Section


def _next_number(project):
    return (project.questionnaire_versions.aggregate(n=Max("number"))["n"] or 0) + 1


def current_version(project):
    """The latest published version: what new reports use."""
    return (
        project.questionnaire_versions.filter(status=QuestionnaireVersion.Status.PUBLISHED)
        .order_by("-number")
        .first()
    )


def validate_for_publishing(version):
    problems = []
    sections = list(version.sections.prefetch_related("questions"))
    if not sections:
        problems.append("Add at least one section.")
    for section in sections:
        if not section.questions.all():
            problems.append(f"Section “{section.title}” has no questions.")
    keys = Counter(q.key for s in sections for q in s.questions.all())
    duplicates = sorted(k for k, n in keys.items() if n > 1)
    if duplicates:
        problems.append("Question keys must be unique; repeated: " + ", ".join(duplicates) + ".")
    return problems


@transaction.atomic
def publish(version, user=None, move_drafts=False):
    """Publish a draft. Earlier published versions stay, for the reports that answered them.

    With move_drafts, reports still being drafted move to this version (answers kept by key).
    """
    version = QuestionnaireVersion.objects.select_for_update().get(pk=version.pk)
    if not version.is_draft:
        raise ValidationError("Only a draft can be published.")
    problems = validate_for_publishing(version)
    if problems:
        raise ValidationError(problems)
    version.status = QuestionnaireVersion.Status.PUBLISHED
    version.published_at = timezone.now()
    version.published_by = user
    version.save(update_fields=["status", "published_at", "published_by", "updated_at"])
    moved = 0
    if move_drafts:
        from apps.reports.services import move_to_version, open_drafts

        for report in open_drafts(version):
            move_to_version(report, version)
            moved += 1
    from apps.audit.services import record

    record(
        user,
        "questionnaire.published",
        version,
        project=version.project,
        version=version.number,
        moved_drafts=moved,
    )
    return version


@transaction.atomic
def new_draft_from(version, *, by=None):
    """Copy a version's sections and questions (same keys) into a new draft."""
    project = version.project
    if project.questionnaire_versions.filter(status=QuestionnaireVersion.Status.DRAFT).exists():
        raise ValidationError("This project already has a draft questionnaire. Edit or publish that one.")
    draft = QuestionnaireVersion.objects.create(project=project, number=_next_number(project))
    from apps.audit.services import record

    record(
        by,
        "questionnaire.draft_started",
        draft,
        project=project,
        version=draft.number,
        copied_from=version.number,
    )
    for section in version.sections.prefetch_related("questions"):
        new_section = Section.objects.create(
            version=draft, order=section.order, title=section.title, display_style=section.display_style
        )
        Question.objects.bulk_create(
            Question(
                section=new_section,
                order=q.order,
                key=q.key,
                prompt=q.prompt,
                guidance=q.guidance,
                answer_type=q.answer_type,
            )
            for q in section.questions.all()
        )
    return draft


def make_key(section_title, index):
    return f"{slugify(section_title)[:60]}-{index}"


@transaction.atomic
def create_draft_from_data(project, data):
    """Create a draft version from {"sections": [{"title", "display_style", "questions": [...]}]}.

    Each question is {"prompt", "answer_type"?, "guidance"?, "key"?}. Used to load the pilot seed.
    """
    if project.questionnaire_versions.filter(status=QuestionnaireVersion.Status.DRAFT).exists():
        raise ValidationError("This project already has a draft questionnaire.")
    draft = QuestionnaireVersion.objects.create(project=project, number=_next_number(project))
    for s_index, s in enumerate(data["sections"], start=1):
        section = Section.objects.create(
            version=draft,
            order=s_index * 10,
            title=s["title"],
            display_style=s.get("display_style", Section.DisplayStyle.QA_TABLE),
        )
        for q_index, q in enumerate(s["questions"], start=1):
            Question.objects.create(
                section=section,
                order=q_index * 10,
                key=q.get("key") or make_key(s["title"], q_index),
                prompt=q["prompt"],
                guidance=q.get("guidance", ""),
                answer_type=q.get("answer_type", Question.AnswerType.LONG_TEXT),
            )
    return draft


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --- Editing a draft (questionnaire builder) ------------------------------------------


def _require_draft(version):
    if not version.is_draft:
        raise ValidationError("Published versions can't be changed. Start a new draft instead.")


def unique_key(version, base):
    existing = set(Question.objects.filter(section__version=version).values_list("key", flat=True))
    key, n = base, 1
    while key in existing:
        n += 1
        key = f"{base}-{n}"
    return key


@transaction.atomic
def add_section(version, title="New section", display_style=Section.DisplayStyle.QA_TABLE):
    _require_draft(version)
    last = version.sections.aggregate(m=Max("order"))["m"] or 0
    return Section.objects.create(version=version, title=title, display_style=display_style, order=last + 10)


@transaction.atomic
def add_question(section, prompt="New question"):
    _require_draft(section.version)
    last = section.questions.aggregate(m=Max("order"))["m"] or 0
    count = section.questions.count() + 1
    return Question.objects.create(
        section=section,
        order=last + 10,
        prompt=prompt,
        key=unique_key(section.version, make_key(section.title, count)),
    )


@transaction.atomic
def move_question(question, direction):
    """Swap a question with its neighbour. direction is -1 (up) or +1 (down)."""
    section = question.section
    _require_draft(section.version)
    questions = list(section.questions.order_by("order", "created_at"))
    i = questions.index(question)
    j = i + direction
    if 0 <= j < len(questions):
        questions[i], questions[j] = questions[j], questions[i]
    for n, q in enumerate(questions, start=1):
        if q.order != n * 10:
            q.order = n * 10
            q.save(update_fields=["order", "updated_at"])


@transaction.atomic
def delete_question(question):
    _require_draft(question.section.version)
    question.delete()


@transaction.atomic
def delete_section(section):
    _require_draft(section.version)
    section.delete()


def open_report_drafts(version):
    """How many reports are still being drafted on other versions of this questionnaire."""
    from apps.reports.services import open_drafts

    return open_drafts(version).count()
