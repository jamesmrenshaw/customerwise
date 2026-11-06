import uuid

from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords

from apps.core.models import BaseModel


def evidence_path(instance, filename):
    # A random key: the original filename is kept in the database only, never in the bucket path.
    return f"evidence/{uuid.uuid4().hex}"


class EvidenceQuerySet(models.QuerySet):
    def current(self):
        return self.filter(deleted_at__isnull=True)


class EvidenceFile(BaseModel):
    """A file a shopper uploads to support their report (CW-040, D10). Stored privately (CW-041)."""

    class Category(models.TextChoices):
        EMAIL = "email", "Email"
        SCREENSHOT = "screenshot", "Screenshot"
        SUPPLIER_DOCUMENT = "supplier_document", "Supplier document"
        TRANSCRIPT = "transcript", "Call transcript"
        OTHER = "other", "Other"

    class ScanStatus(models.TextChoices):
        # The pilot has no malware scanning (CUS-140); a scanner can set these later.
        NOT_SCANNED = "not_scanned", "Not scanned (pilot)"
        CLEAN = "clean", "Scanned"
        REJECTED = "rejected", "Rejected"

    assignment = models.ForeignKey("fieldwork.Assignment", on_delete=models.PROTECT, related_name="evidence")
    file = models.FileField(upload_to=evidence_path, max_length=200)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size = models.PositiveBigIntegerField()
    category = models.CharField(max_length=30, choices=Category, default=Category.OTHER)
    scan_status = models.CharField(max_length=20, choices=ScanStatus, default=ScanStatus.NOT_SCANNED)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    deleted_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords(excluded_fields=["file"])
    objects = EvidenceQuerySet.as_manager()

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.original_name
