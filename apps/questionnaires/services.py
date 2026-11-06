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
def publish(version, user=None):
    """Publish a draft. Earlier published versions stay, for the reports that answered them.

    When reports exist (step 5), publishing will also ask whether open drafts move to this version.
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
    return version


@transaction.atomic
def new_draft_from(version):
    """Copy a version's sections and questions (same keys) into a new draft."""
    project = version.project
    if project.questionnaire_versions.filter(status=QuestionnaireVersion.Status.DRAFT).exists():
        raise ValidationError("This project already has a draft questionnaire. Edit or publish that one.")
    draft = QuestionnaireVersion.objects.create(project=project, number=_next_number(project))
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
