"""Shopper screens: My assignments and the assignment page (prototype screens 02 and 03).

Every object is fetched through apps.core.access, so a shopper only ever sees their own
assignments (404 otherwise), and only the shopper-facing project name is shown.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Max, Min, Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.access import assignments_for, shopper_required
from apps.core.ui import dialog, done, posted
from apps.fieldwork import services as fieldwork
from apps.fieldwork.models import Activity, Assignment
from apps.projects import services as projects
from apps.projects.models import Project

from . import forms

STAGE_FILTERS = [("open", "Open assignments"), ("all", "All stages"), ("closed", "Complete or unable")]


def _assignment(request, pk):
    return get_object_or_404(assignments_for(request.user), pk=pk)


def _projects_for_new_targets(user):
    return Project.objects.filter(
        status=Project.Status.ACTIVE,
        pk__in=assignments_for(user).filter(closed_at__isnull=True).values("target__project"),
    ).order_by("shopper_facing_name")


@shopper_required
def assignment_list(request):
    now = timezone.now()
    current = Q(activities__deleted_at__isnull=True)
    qs = assignments_for(request.user).annotate(
        last_done=Max("activities__occurred_at", filter=current & Q(activities__occurred_at__lte=now)),
        next_planned=Min("activities__occurred_at", filter=current & Q(activities__occurred_at__gt=now)),
    )
    projects_qs = Project.objects.filter(pk__in=assignments_for(request.user).values("target__project"))
    project_id = request.GET.get("project", "")
    if project_id:
        qs = qs.filter(target__project_id=project_id)
    stage = request.GET.get("stage", "open")
    if stage == "open":
        qs = qs.filter(closed_at__isnull=True).exclude(stage=Assignment.Stage.COMPLETE)
    elif stage == "closed":
        qs = qs.filter(Q(closed_at__isnull=False) | Q(stage=Assignment.Stage.COMPLETE))
    from apps.reports.review import returned_comments

    assignments = list(qs.select_related("report").order_by("closed_at", "target__name"))
    returned = []
    for a in assignments:
        report = getattr(a, "report", None)
        a.returned_count = returned_comments(report).count() if report and a.returned else 0
        if a.returned:
            returned.append(a)
    return render(
        request,
        "shopper/assignments.html",
        {
            "returned": returned,
            "assignments": assignments,
            "projects": projects_qs.order_by("shopper_facing_name"),
            "project_id": project_id,
            "stage": stage,
            "stage_filters": STAGE_FILTERS,
            "can_add_target": _projects_for_new_targets(request.user).exists(),
        },
    )


def _detail_context(request, assignment, activity_form=None):
    project = assignment.target.project
    activities = list(assignment.activities.current().select_related("created_by"))
    now = timezone.now()
    latest = project.latest_brief
    acknowledged = projects.last_acknowledged_brief(request.user, project)
    needs_ack = latest is not None and (acknowledged is None or acknowledged.number < latest.number)
    changes = projects.brief_changes(acknowledged.text, latest.text) if needs_ack and acknowledged else []
    editable = fieldwork.shopper_can_edit(assignment)
    stage_index = (
        fieldwork.MANUAL_STAGES.index(assignment.stage)
        if assignment.stage in fieldwork.MANUAL_STAGES
        else {Assignment.Stage.IN_REVIEW: 5, Assignment.Stage.COMPLETE: 6}.get(assignment.stage, 0)
    )
    steps = [
        {
            "value": s.value,
            "label": s.label,
            "number": i + 1,
            "done": i < stage_index,
            "current": i == stage_index,
        }
        for i, s in enumerate(fieldwork.MANUAL_STAGES)
    ]
    from apps.reports.models import Report
    from apps.reports.services import missing_questions, questions

    report = Report.objects.filter(assignment=assignment).first()
    report_summary = None
    if report:
        total = questions(report).count()
        report_summary = {
            "report": report,
            "answered": total - len(missing_questions(report)),
            "total": total,
        }
    from apps.reports.review import returned_comments

    return {
        "returned_count": returned_comments(report).count() if report else 0,
        "report_summary": report_summary,
        "evidence_count": assignment.evidence.current().count(),
        "has_questionnaire": project.questionnaire_versions.filter(status="published").exists(),
        "assignment": assignment,
        "target": assignment.target,
        "project": project,
        "editable": editable,
        "steps": steps,
        "in_review": assignment.stage == Assignment.Stage.IN_REVIEW,
        "complete": assignment.stage == Assignment.Stage.COMPLETE,
        "planned": sorted((a for a in activities if a.occurred_at > now), key=lambda a: a.occurred_at),
        "history": [a for a in activities if a.occurred_at <= now],
        "activity_form": activity_form or forms.ActivityForm(),
        "brief": latest,
        "needs_ack": needs_ack,
        "first_ack": needs_ack and acknowledged is None,
        "changes": changes,
    }


@shopper_required
def assignment_detail(request, pk):
    assignment = _assignment(request, pk)
    return render(request, "shopper/assignment.html", _detail_context(request, assignment))


def _detail_url(assignment):
    return reverse("shopper:assignment", args=[assignment.pk])


@shopper_required
@require_POST
def assignment_stage(request, pk):
    assignment = _assignment(request, pk)
    try:
        fieldwork.set_stage(assignment, request.POST.get("stage", ""))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return done(request, _detail_url(assignment))
    return done(request, _detail_url(assignment), f"Stage set to {assignment.get_stage_display()}.")


@shopper_required
def assignment_unable(request, pk):
    assignment = _assignment(request, pk)
    form = forms.UnableForm(posted(request))
    if request.method == "POST" and form.is_valid():
        try:
            fieldwork.mark_unable(assignment, form.cleaned_data["reason"], form.cleaned_data["note"])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return done(
                request, reverse("shopper:assignments"), f"{assignment.target} closed as unable to complete."
            )
    return dialog(request, "shopper/dialogs/unable.html", {"form": form, "assignment": assignment})


@shopper_required
@require_POST
def activity_add(request, pk):
    assignment = _assignment(request, pk)
    form = forms.ActivityForm(request.POST)
    if form.is_valid():
        try:
            activity = fieldwork.add_activity(assignment, by=request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            label = "Planned" if activity.is_planned else "Added to the log"
            return done(request, _detail_url(assignment) + "#activity", f"{label}: {activity}.")
    context = _detail_context(request, assignment, activity_form=form)
    return render(request, "shopper/assignment.html", context, status=400)


def _activity(request, pk):
    return get_object_or_404(
        Activity.objects.current().select_related("assignment__target"),
        pk=pk,
        assignment__in=assignments_for(request.user),
    )


@shopper_required
def activity_edit(request, pk):
    activity = _activity(request, pk)
    form = forms.ActivityForm(posted(request), instance=activity)
    if request.method == "POST" and form.is_valid():
        try:
            fieldwork.update_activity(activity, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return done(request, _detail_url(activity.assignment) + "#activity", "Activity updated.")
    return dialog(request, "shopper/dialogs/activity_edit.html", {"form": form, "activity": activity})


@shopper_required
@require_POST
def activity_delete(request, pk):
    activity = _activity(request, pk)
    try:
        fieldwork.delete_activity(activity)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return done(request, _detail_url(activity.assignment))
    return done(request, _detail_url(activity.assignment) + "#activity", "Activity deleted.")


@shopper_required
@require_POST
def brief_acknowledge(request, pk):
    assignment = _assignment(request, pk)
    latest = assignment.target.project.latest_brief
    if latest:
        projects.acknowledge_brief(request.user, latest)
    return done(request, _detail_url(assignment) + "#brief", "Thanks. You've confirmed the latest brief.")


@shopper_required
def target_add(request):
    available = _projects_for_new_targets(request.user)
    form = forms.ShopperTargetForm(posted(request), projects=available)
    duplicate = None
    if request.method == "POST" and form.is_valid():
        try:
            assignment = fieldwork.add_target_by_shopper(
                form.cleaned_data["project"],
                request.user,
                form.cleaned_data["name"].strip(),
                form.cleaned_data["website"].strip(),
            )
        except fieldwork.DuplicateTarget as exc:
            duplicate = {"own": exc.own}
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return done(
                request,
                _detail_url(assignment),
                f"{assignment.target} added and assigned to you. You can start fieldwork now.",
            )
    return dialog(request, "shopper/dialogs/target_add.html", {"form": form, "duplicate": duplicate})
