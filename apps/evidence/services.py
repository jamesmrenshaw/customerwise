from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.fieldwork.services import shopper_can_edit

from .models import EvidenceFile
from .validation import validate_upload


def _require_editable(assignment):
    if not shopper_can_edit(assignment):
        raise ValidationError("Evidence can't be changed after the report is submitted.")


@transaction.atomic
def add_evidence(assignment, upload, *, by, category=None):
    _require_editable(assignment)
    _, content_type, suggested = validate_upload(upload)
    evidence = EvidenceFile(
        assignment=assignment,
        original_name=upload.name[:255],
        content_type=content_type,
        size=upload.size,
        category=category or suggested,
        uploaded_by=by,
    )
    evidence.file.save(upload.name, upload, save=False)
    evidence.save()
    return evidence


def set_category(evidence, category):
    _require_editable(evidence.assignment)
    if category not in EvidenceFile.Category.values:
        raise ValidationError("Choose a category.")
    evidence.category = category
    evidence.save()


def delete_evidence(evidence):
    """Hidden from the shopper; the file and its history are kept for staff (D10)."""
    _require_editable(evidence.assignment)
    evidence.deleted_at = timezone.now()
    evidence.save()
