"""Assigning shoppers to targets (D07, CW-027). Staff do this in admin; these rules apply everywhere."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User

from .models import Assignment


def _check_shopper(shopper):
    if shopper.role != User.Role.SHOPPER or not shopper.is_active:
        raise ValidationError("Only active shoppers can be assigned to targets.")


@transaction.atomic
def assign(target, shopper, *, by=None, approval_confirmed=False, approval_note="", by_shopper=False):
    """Open an assignment for a target. Staff must confirm the client approved the shopper off-platform
    (CUS-140); a shopper adding their own target is assigned automatically (MVP-01)."""
    _check_shopper(shopper)
    if target.archived_at:
        raise ValidationError("This target is archived.")
    if not by_shopper and not approval_confirmed:
        raise ValidationError("Confirm the client approved this shopper (off-platform) before assigning.")
    try:
        with transaction.atomic():
            return Assignment.objects.create(
                target=target,
                shopper=shopper,
                assigned_by=by,
                approval_confirmed_offplatform=approval_confirmed,
                approval_note=approval_note,
            )
    except IntegrityError as exc:
        raise ValidationError(f"{target} already has an open assignment. Reassign it instead.") from exc


def _close(assignment, reason, note=""):
    assignment.closed_at = timezone.now()
    assignment.closed_reason = reason
    assignment.closed_note = note
    assignment.save(update_fields=["closed_at", "closed_reason", "closed_note", "updated_at"])


@transaction.atomic
def reassign(assignment, new_shopper, *, by=None, approval_confirmed=False, approval_note="", note=""):
    """Close the open assignment (history kept, old shopper loses access) and open a fresh one."""
    assignment = Assignment.objects.select_for_update().get(pk=assignment.pk)
    if not assignment.is_open:
        raise ValidationError("Only an open assignment can be reassigned.")
    if new_shopper == assignment.shopper:
        raise ValidationError("Choose a different shopper.")
    _check_shopper(new_shopper)
    if not approval_confirmed:
        raise ValidationError("Confirm the client approved the new shopper (off-platform).")
    _close(assignment, Assignment.ClosedReason.REASSIGNED, note)
    return assign(
        assignment.target,
        new_shopper,
        by=by,
        approval_confirmed=approval_confirmed,
        approval_note=approval_note,
    )


@transaction.atomic
def withdraw(assignment, *, note=""):
    """Staff withdraw a shopper from a target. History is kept; the shopper loses access."""
    assignment = Assignment.objects.select_for_update().get(pk=assignment.pk)
    if not assignment.is_open:
        raise ValidationError("Only an open assignment can be withdrawn.")
    _close(assignment, Assignment.ClosedReason.WITHDRAWN, note)
    return assignment


@transaction.atomic
def archive_target(target, *, note=""):
    """Hide a target from lists. Any open assignment is withdrawn first; nothing is deleted (MVP-01)."""
    for assignment in target.assignments.open():
        withdraw(assignment, note=note or "Target archived")
    if target.archived_at is None:
        target.archived_at = timezone.now()
        target.save(update_fields=["archived_at", "updated_at"])
    return target
