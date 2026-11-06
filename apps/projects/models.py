from urllib.parse import urlsplit

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from simple_history.models import HistoricalRecords

from apps.core.models import BaseModel


class ClientOrganisation(BaseModel):
    """A commissioning client. Internal record only in the pilot: clients have no logins (CW-005)."""

    name = models.CharField(max_length=200)
    reference = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True, help_text="Untick instead of deleting; history is kept.")

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), name="projects_client_name_ci_unique")]

    def __str__(self):
        return self.name


class Project(BaseModel):
    """A B2B mystery-shopping project with separate names per audience (CW-009, CW-010).

    All three names are required, so there is never a fallback to the confidential internal name:
    shoppers only ever see shopper_facing_name, and exports only client_facing_name.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        COMPLETE = "complete", "Complete"

    client = models.ForeignKey(ClientOrganisation, on_delete=models.PROTECT, related_name="projects")
    internal_name = models.CharField(max_length=200, help_text="Customer Wise staff only.")
    client_facing_name = models.CharField(max_length=200, help_text="Used in exports for the client.")
    shopper_facing_name = models.CharField(
        max_length=200, help_text="The only name shoppers see. Must not identify the client."
    )
    status = models.CharField(max_length=20, choices=Status, default=Status.DRAFT)
    notification_email = models.EmailField(
        blank=True,
        help_text="The Customer Wise staff member who is emailed when a report is submitted.",
    )
    ended_at = models.DateTimeField(
        null=True, blank=True, help_text="Set when the project ends; drives the 12-month retention rule."
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.internal_name

    @property
    def latest_brief(self):
        return self.brief_versions.order_by("-number").first()


class BriefVersion(BaseModel):
    """One saved version of the shopper-facing brief. Never edited after saving (D06)."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="brief_versions")
    number = models.PositiveIntegerField()
    text = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["project", "-number"]
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="projects_brief_version_unique")
        ]

    def __str__(self):
        return f"{self.project} brief v{self.number}"


class BriefAcknowledgement(models.Model):
    """A shopper confirming they have read a brief version (D06). Needed before submitting a report."""

    brief_version = models.ForeignKey(BriefVersion, on_delete=models.CASCADE, related_name="acknowledgements")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    acknowledged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["brief_version", "user"], name="projects_brief_ack_unique")
        ]

    def __str__(self):
        return f"{self.user} acknowledged {self.brief_version}"


def domain_from_website(website):
    """'https://www.Example.com/about' -> 'example.com'. Used to spot duplicate targets (D05)."""
    if not website:
        return ""
    if "://" not in website:
        website = "https://" + website
    host = (urlsplit(website).hostname or "").lower()
    return host.removeprefix("www.")


class TargetQuerySet(models.QuerySet):
    def current(self):
        return self.filter(archived_at__isnull=True)


class Target(BaseModel):
    """An organisation to be researched on a project (D05: name required, website optional)."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="targets")
    name = models.CharField(max_length=200)
    website = models.CharField(max_length=300, blank=True)
    domain = models.CharField(max_length=253, blank=True, editable=False, db_index=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    added_by_shopper = models.BooleanField(default=False, editable=False)
    archived_at = models.DateTimeField(
        null=True, blank=True, help_text="Archived targets keep their history but are hidden."
    )

    history = HistoricalRecords()
    objects = TargetQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        self.website = self.website.strip()
        self.domain = domain_from_website(self.website)
        super().save(*args, **kwargs)
