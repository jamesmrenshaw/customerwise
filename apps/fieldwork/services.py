"""Assignments and fieldwork (D07, D08, CW-027 to CW-032). Key actions are recorded in the audit log."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record

from .models import Assignment


def _audit(actor, action, assignment, **data):
    target = assignment.target
    record(
        actor,
        action,
        assignment,
        project=target.project,
        target=target.name,
        shopper=str(assignment.shopper),
        **data,
    )


def _check_shopper(shopper):
    if shopper.role != User.Role.SHOPPER or not shopper.is_active:
        raise ValidationError("Only active shoppers can be assigned to targets.")


@transaction.atomic
def assign(
    target, shopper, *, by=None, approval_confirmed=False, approval_note="", by_shopper=False, audit=True
):
    """Open an assignment for a target. Staff must confirm the client approved the shopper off-platform
    (CUS-140); a shopper adding their own target is assigned automatically (MVP-01)."""
    _check_shopper(shopper)
    if target.archived_at:
        raise ValidationError("This target is archived.")
    if not by_shopper and not approval_confirmed:
        raise ValidationError("Confirm the client approved this shopper (off-platform) before assigning.")
    try:
        with transaction.atomic():
            assignment = Assignment.objects.create(
                target=target,
                shopper=shopper,
                assigned_by=by,
                approval_confirmed_offplatform=approval_confirmed,
                approval_note=approval_note,
            )
    except IntegrityError as exc:
        raise ValidationError(f"{target} already has an open assignment. Reassign it instead.") from exc
    if audit:
        _audit(
            by, "assignment.created", assignment, approval_confirmed=approval_confirmed, by_shopper=by_shopper
        )
    return assignment


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
    new = assign(
        assignment.target,
        new_shopper,
        by=by,
        approval_confirmed=approval_confirmed,
        approval_note=approval_note,
        audit=False,
    )
    _audit(by, "assignment.reassigned", new, previous_shopper=str(assignment.shopper), note=note)
    return new


@transaction.atomic
def withdraw(assignment, *, note="", by=None):
    """Staff withdraw a shopper from a target. History is kept; the shopper loses access."""
    assignment = Assignment.objects.select_for_update().get(pk=assignment.pk)
    if not assignment.is_open:
        raise ValidationError("Only an open assignment can be withdrawn.")
    _close(assignment, Assignment.ClosedReason.WITHDRAWN, note)
    _audit(by, "assignment.withdrawn", assignment, note=note)
    return assignment


@transaction.atomic
def archive_target(target, *, note="", by=None):
    """Hide a target from lists. Any open assignment is withdrawn first; nothing is deleted (MVP-01)."""
    for assignment in target.assignments.open():
        withdraw(assignment, note=note or "Target archived", by=by)
    if target.archived_at is None:
        target.archived_at = timezone.now()
        target.save(update_fields=["archived_at", "updated_at"])
        record(by, "target.archived", target, project=target.project, target=target.name, note=note)
    return target


# --- Shopper actions (CW-028 to CW-032, MVP-01) ---------------------------------------

# Shoppers set these themselves and can step back (D08). In review is set by submitting the
# report and Complete by Customer Wise accepting it (refined 6 Nov 2026).
MANUAL_STAGES = [
    Assignment.Stage.NOT_STARTED,
    Assignment.Stage.PLANNED,
    Assignment.Stage.INITIAL_CONTACT,
    Assignment.Stage.FOLLOW_UP,
    Assignment.Stage.REPORT_DRAFTING,
]


LOCKED_STAGES = {Assignment.Stage.IN_REVIEW, Assignment.Stage.COMPLETE}


def shopper_can_edit(assignment):
    """Open and not submitted. Submitting locks it (In review); returning the report reopens it."""
    return assignment.is_open and assignment.stage not in LOCKED_STAGES


def _require_editable(assignment):
    if not shopper_can_edit(assignment):
        raise ValidationError("This assignment can no longer be changed.")


def _touch(assignment):
    assignment.last_activity_at = timezone.now()
    assignment.save(update_fields=["last_activity_at", "updated_at"])


@transaction.atomic
def set_stage(assignment, stage, *, by=None):
    _require_editable(assignment)
    if stage not in MANUAL_STAGES:
        raise ValidationError(
            "In review and Complete are set by submitting the report and by Customer Wise accepting it."
        )
    previous = assignment.get_stage_display()
    if stage != assignment.stage:
        _audit(
            by or assignment.shopper,
            "assignment.stage_changed",
            assignment,
            **{"from": previous, "to": Assignment.Stage(stage).label},
        )
    assignment.stage = stage
    assignment.last_activity_at = timezone.now()
    assignment.save(update_fields=["stage", "last_activity_at", "updated_at"])
    return assignment


@transaction.atomic
def mark_unable(assignment, reason, note="", *, by=None):
    """Close the assignment without a report (D08). The shopper still sees it, read-only."""
    _require_editable(assignment)
    if reason not in Assignment.UnableReason.values:
        raise ValidationError("Choose a reason.")
    assignment.unable_reason = reason
    assignment.unable_note = note
    assignment.save(update_fields=["unable_reason", "unable_note", "updated_at"])
    _close(assignment, Assignment.ClosedReason.UNABLE, note)
    _audit(
        by or assignment.shopper,
        "assignment.unable",
        assignment,
        reason=assignment.get_unable_reason_display(),
        note=note,
    )
    return assignment


@transaction.atomic
def add_activity(assignment, *, by, type, occurred_at, notes, contact_job_title=""):
    from .models import Activity

    _require_editable(assignment)
    activity = Activity.objects.create(
        assignment=assignment,
        type=type,
        occurred_at=occurred_at,
        notes=notes,
        contact_job_title=contact_job_title,
        created_by=by,
    )
    _touch(assignment)
    return activity


@transaction.atomic
def update_activity(activity, **fields):
    _require_editable(activity.assignment)
    for name, value in fields.items():
        setattr(activity, name, value)
    activity.save()
    _touch(activity.assignment)
    return activity


@transaction.atomic
def delete_activity(activity):
    """Soft delete: hidden from the shopper, kept in history for staff (D10)."""
    _require_editable(activity.assignment)
    activity.deleted_at = timezone.now()
    activity.save()
    _touch(activity.assignment)


class DuplicateTarget(ValidationError):
    """A matching target already exists on the project. own is the shopper's own match, if any."""

    def __init__(self, own=None):
        self.own = own
        super().__init__("Possible duplicate target.")


@transaction.atomic
def add_target_by_shopper(project, shopper, name, website=""):
    """A shopper adds a target to a project they're working on; it's assigned to them (MVP-01)."""
    from apps.core.access import assignments_for
    from apps.projects.models import Project, Target
    from apps.projects.services import find_duplicate_targets

    if project.status != Project.Status.ACTIVE:
        raise ValidationError("You can only add targets to active projects.")
    if not assignments_for(shopper).filter(target__project=project, closed_at__isnull=True).exists():
        raise ValidationError("You can only add targets to projects you're working on.")
    duplicates = list(find_duplicate_targets(project, name, website))
    if duplicates:
        mine = assignments_for(shopper).filter(target__in=duplicates).select_related("target").first()
        raise DuplicateTarget(own=mine.target if mine else None)
    target = Target.objects.create(
        project=project, name=name, website=website, added_by=shopper, added_by_shopper=True
    )
    record(shopper, "target.added", target, project=project, target=name, by_shopper=True)
    return assign(target, shopper, by=shopper, by_shopper=True)
