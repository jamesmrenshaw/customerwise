from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel


class AssignmentQuerySet(models.QuerySet):
    def open(self):
        return self.filter(closed_at__isnull=True)


class Assignment(BaseModel):
    """One shopper's attempt at one target (D07, D08).

    A target has many assignments over time but at most one open one (enforced by the database).
    Reassigning closes the old assignment, keeping its history for staff, and opens a fresh one.
    """

    class Stage(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        PLANNED = "planned", "Planned"
        INITIAL_CONTACT = "initial_contact", "Initial contact"
        FOLLOW_UP = "follow_up", "Follow up"
        REPORT_DRAFTING = "report_drafting", "Report drafting"
        COMPLETE = "complete", "Complete"

    class UnableReason(models.TextChoices):
        NO_RESPONSE = "no_response", "No response"
        DECLINED = "declined", "Organisation declined"
        CLOSED = "closed", "Organisation closed or not trading"
        OTHER = "other", "Other"

    class ClosedReason(models.TextChoices):
        UNABLE = "unable", "Unable to complete"
        REASSIGNED = "reassigned", "Reassigned"
        WITHDRAWN = "withdrawn", "Withdrawn"

    target = models.ForeignKey("projects.Target", on_delete=models.PROTECT, related_name="assignments")
    shopper = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assignments"
    )
    stage = models.CharField(max_length=30, choices=Stage, default=Stage.NOT_STARTED)
    returned = models.BooleanField(default=False, help_text="Report returned by staff for changes.")
    unable_reason = models.CharField(max_length=20, choices=UnableReason, blank=True)
    unable_note = models.TextField(blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_reason = models.CharField(max_length=20, choices=ClosedReason, blank=True)
    closed_note = models.TextField(blank=True)
    # The pilot has no in-platform client approval of shoppers (CUS-140): staff confirm it happened.
    approval_confirmed_offplatform = models.BooleanField(default=False)
    approval_note = models.TextField(blank=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    last_activity_at = models.DateTimeField(default=timezone.now, help_text="Drives the stalled flag.")

    objects = AssignmentQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["target"],
                condition=models.Q(closed_at__isnull=True),
                name="fieldwork_one_open_assignment_per_target",
                violation_error_message="This target already has an open assignment.",
            )
        ]

    def __str__(self):
        return f"{self.target} → {self.shopper}"

    @property
    def is_open(self):
        return self.closed_at is None
