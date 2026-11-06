from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords

from apps.core.models import BaseModel


class Report(BaseModel):
    """One report per assignment (D06), answering one questionnaire version."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        RETURNED = "returned", "Returned for changes"

    assignment = models.OneToOneField("fieldwork.Assignment", on_delete=models.PROTECT, related_name="report")
    questionnaire_version = models.ForeignKey(
        "questionnaires.QuestionnaireVersion", on_delete=models.PROTECT, related_name="reports"
    )
    status = models.CharField(max_length=20, choices=Status, default=Status.DRAFT)
    version_no = models.PositiveIntegerField(default=0, help_text="How many times it has been submitted.")
    notes = models.TextField(blank=True, help_text="Notes for Customer Wise. Never included in exports.")
    removed_answers = models.JSONField(
        default=list,
        blank=True,
        help_text="Answers to questions removed when the draft moved to a newer questionnaire version.",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Report on {self.assignment.target}"

    @property
    def is_editable(self):
        return self.status in {self.Status.DRAFT, self.Status.RETURNED}


class Answer(BaseModel):
    """An answer to one question. Long text uses the simple formatting in apps.reports.formatting."""

    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey("questionnaires.Question", on_delete=models.PROTECT, related_name="answers")
    text = models.TextField(blank=True)
    datetime_value = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["report", "question"], name="reports_one_answer_per_question")
        ]

    def __str__(self):
        return f"Answer to {self.question_id}"

    @property
    def is_answered(self):
        return bool(self.text.strip()) or self.datetime_value is not None


class ReportSubmission(BaseModel):
    """A frozen copy of the report at each submission, for review and audit (CW-043, CW-048)."""

    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="submissions")
    number = models.PositiveIntegerField()
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    snapshot = models.JSONField()

    class Meta:
        ordering = ["report", "-number"]
        constraints = [
            models.UniqueConstraint(fields=["report", "number"], name="reports_submission_number_unique")
        ]

    def __str__(self):
        return f"{self.report} v{self.number}"
