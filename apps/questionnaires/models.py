from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class QuestionnaireVersion(BaseModel):
    """A project's questionnaire. Drafts are editable; published versions never change (D06).

    Reports record the version they answered, so editing means publishing a new version.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    project = models.ForeignKey(
        "projects.Project", on_delete=models.PROTECT, related_name="questionnaire_versions"
    )
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status, default=Status.DRAFT)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["project", "-number"]
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="questionnaire_version_unique"),
            models.UniqueConstraint(
                fields=["project"],
                condition=models.Q(status="draft"),
                name="questionnaire_one_draft_per_project",
            ),
        ]

    def __str__(self):
        return f"{self.project} questionnaire v{self.number} ({self.get_status_display().lower()})"

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT


class Section(BaseModel):
    class DisplayStyle(models.TextChoices):
        QA_TABLE = "qa_table", "Q&A table"
        HEADINGS = "headings", "Questions as headings"
        ANSWERS_ONLY = "answers_only", "Answers only"
        KEY_FACTS = "key_facts", "Key facts"

    version = models.ForeignKey(QuestionnaireVersion, on_delete=models.CASCADE, related_name="sections")
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=200)
    display_style = models.CharField(
        max_length=20,
        choices=DisplayStyle,
        default=DisplayStyle.QA_TABLE,
        help_text="How the section looks in the report view and export. The shopper's form is the same.",
    )

    class Meta:
        ordering = ["version", "order", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["version"],
                condition=models.Q(display_style="key_facts"),
                name="questionnaire_one_key_facts_section",
                violation_error_message="A questionnaire can have only one Key facts section.",
            )
        ]

    def __str__(self):
        return self.title


class Question(BaseModel):
    class AnswerType(models.TextChoices):
        SHORT_TEXT = "short_text", "Short text"
        LONG_TEXT = "long_text", "Long text"
        DATETIME = "datetime", "Date and time"

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="questions")
    order = models.PositiveIntegerField(default=0)
    key = models.SlugField(
        max_length=80,
        help_text="Stays the same across versions, so answers carry over when drafts move to a new version.",
    )
    prompt = models.TextField()
    guidance = models.TextField(blank=True, help_text="Optional note shown to shoppers under the question.")
    answer_type = models.CharField(max_length=20, choices=AnswerType, default=AnswerType.LONG_TEXT)

    class Meta:
        ordering = ["section__order", "order", "created_at"]

    def __str__(self):
        return self.prompt[:80]
