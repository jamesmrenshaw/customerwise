"""Customer Wise staff screens: projects, clients, targets and shoppers, questionnaire builder.

Matches the approved prototype (screens 07-11). Business rules live in the app services;
these views only collect input and show results. Every view is staff-only (404 for shoppers).
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.access import staff_required
from apps.core.ui import dialog, done, posted
from apps.fieldwork import services as fieldwork
from apps.fieldwork.models import Assignment
from apps.projects import services as projects
from apps.projects.models import ClientOrganisation, Project, Target
from apps.questionnaires import services as questionnaires
from apps.questionnaires.models import Question, QuestionnaireVersion, Section

from . import forms

# --- Projects ------------------------------------------------------------------------

PROJECT_FILTERS = [("all", "All"), ("active", "Active"), ("draft", "Draft"), ("complete", "Complete")]


@staff_required
def project_list(request):
    status = request.GET.get("status", "all")
    query = request.GET.get("q", "").strip()
    base = Project.objects.select_related("client")
    if query:
        base = base.filter(
            Q(internal_name__icontains=query)
            | Q(client__name__icontains=query)
            | Q(shopper_facing_name__icontains=query)
        )
    counts = {key: (base if key == "all" else base.filter(status=key)).count() for key, _ in PROJECT_FILTERS}
    shown = base if status == "all" else base.filter(status=status)
    shown = shown.annotate(
        target_count=Count("targets", filter=Q(targets__archived_at__isnull=True), distinct=True),
        open_count=Count(
            "targets__assignments",
            filter=Q(targets__assignments__closed_at__isnull=True)
            & ~Q(targets__assignments__stage=Assignment.Stage.COMPLETE),
            distinct=True,
        ),
    )
    rows = []
    for project in shown:
        published = questionnaires.current_version(project)
        draft = project.questionnaire_versions.filter(status=QuestionnaireVersion.Status.DRAFT).first()
        parts = []
        if published:
            parts.append(f"v{published.number} published")
        if draft:
            parts.append(f"v{draft.number} draft")
        rows.append({"project": project, "questionnaire": " · ".join(parts) or "Not started"})
    return render(
        request,
        "staff/projects.html",
        {
            "rows": rows,
            "filters": [(k, label, counts[k]) for k, label in PROJECT_FILTERS],
            "status": status,
            "query": query,
        },
    )


def _save_project(request, form, was_complete=False):
    project = form.save(commit=False)
    if project.status == Project.Status.COMPLETE and not was_complete and not project.ended_at:
        project.ended_at = timezone.now()
    elif project.status != Project.Status.COMPLETE:
        project.ended_at = None
    project.save()
    version = projects.update_brief(project, form.cleaned_data.get("brief"), request.user)
    if version and version.number > 1:
        return (
            project,
            f"Saved. Brief saved as version {version.number}; shoppers will be asked to acknowledge it.",
        )
    return project, "Project saved."


@staff_required
def project_create(request):
    form = forms.ProjectForm(posted(request))
    if request.method == "POST" and form.is_valid():
        project, message = _save_project(request, form)
        return done(request, reverse("staff:project_details", args=[project.pk]), message)
    return render(request, "staff/project_details.html", {"form": form, "project": None, "tab": "details"})


@staff_required
def project_home(request, pk):
    return redirect("staff:targets", pk=pk)


@staff_required
def project_details(request, pk):
    project = get_object_or_404(Project.objects.select_related("client"), pk=pk)
    was_complete = project.status == Project.Status.COMPLETE
    form = forms.ProjectForm(posted(request), instance=project)
    if request.method == "POST" and form.is_valid():
        project, message = _save_project(request, form, was_complete)
        return done(request, reverse("staff:project_details", args=[project.pk]), message)
    latest = project.latest_brief
    acked = set(latest.acknowledgements.values_list("user_id", flat=True)) if latest else set()
    open_shoppers = {
        a.shopper for a in Assignment.objects.open().filter(target__project=project).select_related("shopper")
    }
    acknowledgements = sorted(
        ({"shopper": s, "done": s.pk in acked} for s in open_shoppers), key=lambda a: str(a["shopper"])
    )
    history = project.brief_versions.select_related("created_by").prefetch_related("acknowledgements__user")
    return render(
        request,
        "staff/project_details.html",
        {
            "form": form,
            "project": project,
            "tab": "details",
            "acknowledgements": acknowledgements,
            "history": history,
            "latest": latest,
        },
    )


# --- Targets and shoppers ------------------------------------------------------------

TARGET_FILTERS = [
    ("current", "Current"),
    ("unassigned", "Not assigned"),
    ("shopper", "Added by shoppers"),
    ("archived", "Archived"),
]


@staff_required
def targets(request, pk):
    project = get_object_or_404(Project, pk=pk)
    all_targets = list(
        project.targets.select_related("added_by").prefetch_related("assignments__shopper").order_by("name")
    )
    rows = []
    for target in all_targets:
        assignments = sorted(target.assignments.all(), key=lambda a: a.created_at, reverse=True)
        current = next((a for a in assignments if a.is_open), None)
        # An "unable to complete" assignment is closed but still the latest state of the target.
        latest = current or next(
            (a for a in assignments if a.closed_reason == Assignment.ClosedReason.UNABLE), None
        )
        rows.append({"target": target, "assignment": current, "latest": latest})
    tests = {
        "current": lambda r: not r["target"].archived_at,
        "unassigned": lambda r: not r["target"].archived_at and not r["assignment"] and not r["latest"],
        "shopper": lambda r: not r["target"].archived_at and r["target"].added_by_shopper,
        "archived": lambda r: bool(r["target"].archived_at),
    }
    shown = request.GET.get("show", "current")
    if shown not in tests:
        shown = "current"
    return render(
        request,
        "staff/targets.html",
        {
            "project": project,
            "tab": "targets",
            "rows": [r for r in rows if tests[shown](r)],
            "filters": [(k, label, sum(1 for r in rows if tests[k](r))) for k, label in TARGET_FILTERS],
            "shown": shown,
        },
    )


@staff_required
def target_add(request, pk):
    project = get_object_or_404(Project, pk=pk)
    form = forms.TargetForm(posted(request), project=project)
    if request.method == "POST" and form.is_valid():
        target = form.save(commit=False)
        target.project = project
        target.added_by = request.user
        target.save()
        return done(
            request,
            reverse("staff:targets", args=[project.pk]),
            f"{target.name} added. Assign a shopper when you are ready.",
        )
    return dialog(request, "staff/dialogs/target_add.html", {"form": form, "project": project})


def _target(pk):
    return get_object_or_404(Target.objects.select_related("project"), pk=pk)


def _targets_url(target):
    return reverse("staff:targets", args=[target.project_id])


@staff_required
def target_assign(request, pk):
    target = _target(pk)
    form = forms.AssignForm(posted(request))
    if request.method == "POST" and form.is_valid():
        try:
            assignment = fieldwork.assign(
                target,
                form.cleaned_data["shopper"],
                by=request.user,
                approval_confirmed=form.cleaned_data["approval_confirmed"],
                approval_note=form.cleaned_data["approval_note"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return done(request, _targets_url(target), f"{target.name} assigned to {assignment.shopper}.")
    return dialog(
        request,
        "staff/dialogs/assign.html",
        {"form": form, "target": target, "title": "Assign shopper", "action": "Assign"},
    )


@staff_required
def target_reassign(request, pk):
    target = _target(pk)
    current = target.assignments.open().select_related("shopper").first()
    if current is None:
        return redirect(_targets_url(target))
    form = forms.ReassignForm(posted(request), exclude=current.shopper)
    if request.method == "POST" and form.is_valid():
        try:
            new = fieldwork.reassign(
                current,
                form.cleaned_data["shopper"],
                by=request.user,
                approval_confirmed=form.cleaned_data["approval_confirmed"],
                approval_note=form.cleaned_data["approval_note"],
                note=form.cleaned_data["note"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return done(
                request,
                _targets_url(target),
                f"{target.name} reassigned to {new.shopper}. The previous assignment is kept for staff.",
            )
    return dialog(
        request,
        "staff/dialogs/assign.html",
        {
            "form": form,
            "target": target,
            "current": current,
            "title": "Reassign target",
            "action": "Reassign",
        },
    )


@staff_required
def target_withdraw(request, pk):
    target = _target(pk)
    current = target.assignments.open().select_related("shopper").first()
    if current is None:
        return redirect(_targets_url(target))
    form = forms.ReasonForm(posted(request))
    if request.method == "POST" and form.is_valid():
        fieldwork.withdraw(current, note=form.cleaned_data["note"])
        return done(
            request,
            _targets_url(target),
            f"{current.shopper} withdrawn from {target.name}. Their work is kept for staff.",
        )
    return dialog(
        request,
        "staff/dialogs/reason.html",
        {
            "form": form,
            "target": target,
            "current": current,
            "title": "Withdraw shopper",
            "action": "Withdraw",
            "explain": "The shopper loses access to this target. Their activity and draft stay visible to "
            "staff. You can assign someone else afterwards.",
        },
    )


@staff_required
def target_archive(request, pk):
    target = _target(pk)
    if target.archived_at:
        return redirect(_targets_url(target))
    current = target.assignments.open().select_related("shopper").first()
    form = forms.ReasonForm(posted(request))
    if request.method == "POST" and form.is_valid():
        fieldwork.archive_target(target, note=form.cleaned_data["note"])
        return done(request, _targets_url(target), f"{target.name} archived. Its history is kept.")
    explain = "The target is hidden from lists but its history is kept. It can't be deleted."
    if current:
        explain = f"{current.shopper} will be withdrawn first. " + explain
    return dialog(
        request,
        "staff/dialogs/reason.html",
        {
            "form": form,
            "target": target,
            "current": current,
            "title": "Archive target",
            "action": "Archive",
            "explain": explain,
        },
    )


# --- Questionnaire builder -------------------------------------------------------------


@staff_required
def questionnaire(request, pk):
    project = get_object_or_404(Project, pk=pk)
    versions = list(project.questionnaire_versions.order_by("-number"))
    current = questionnaires.current_version(project)
    if not versions:
        return render(request, "staff/questionnaire.html", {"project": project, "tab": "questionnaire"})
    wanted = request.GET.get("v")
    draft = next((v for v in versions if v.is_draft), None)
    version = next((v for v in versions if str(v.number) == wanted), None) or draft or versions[0]
    sections = list(version.sections.annotate(n=Count("questions")).order_by("order", "created_at"))
    section = next((s for s in sections if str(s.pk) == request.GET.get("s")), None) or (
        sections[0] if sections else None
    )
    context = {
        "project": project,
        "tab": "questionnaire",
        "versions": versions,
        "version": version,
        "current": current,
        "draft": draft,
        "sections": sections,
        "section": section,
    }
    if section:
        context["section_form"] = forms.SectionForm(instance=section, prefix="section")
        context["formset"] = forms.QuestionFormSet(
            queryset=section.questions.order_by("order", "created_at"), prefix="q"
        )
    return render(request, "staff/questionnaire.html", context)


def _builder_url(version, section=None):
    url = reverse("staff:questionnaire", args=[version.project_id]) + f"?v={version.number}"
    return url + (f"&s={section.pk}" if section else "")


@staff_required
@require_POST
def questionnaire_start(request, pk):
    """Create the project's first draft questionnaire."""
    project = get_object_or_404(Project, pk=pk)
    if project.questionnaire_versions.exists():
        return redirect("staff:questionnaire", pk=pk)
    if request.POST.get("template") == "pilot":
        from apps.questionnaires.management.commands.load_questionnaire import PILOT

        version = questionnaires.create_draft_from_data(project, questionnaires.load_json(PILOT))
        return done(
            request,
            _builder_url(version),
            "Draft version 1 created from the pilot questionnaire. Check it, then publish.",
        )
    version = QuestionnaireVersion.objects.create(project=project, number=1)
    section = questionnaires.add_section(version, title="New section")
    return done(request, _builder_url(version, section), "Draft version 1 started.")


@staff_required
@require_POST
def version_new_draft(request, pk):
    version = get_object_or_404(QuestionnaireVersion, pk=pk)
    try:
        draft = questionnaires.new_draft_from(version)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect(_builder_url(version))
    return done(
        request,
        _builder_url(draft),
        f"Version {draft.number} started as a copy of version {version.number}. "
        "Question keys are kept, so answers carry over.",
    )


@staff_required
def version_publish(request, pk):
    version = get_object_or_404(QuestionnaireVersion.objects.select_related("project"), pk=pk)
    problems = questionnaires.validate_for_publishing(version) if version.is_draft else ["Already published."]
    drafts = questionnaires.open_report_drafts(version)
    form = forms.PublishForm(posted(request))
    if request.method == "POST" and not problems and form.is_valid():
        # The radio defaults to moving drafts; it's only shown when there are drafts to move.
        move = bool(drafts) and form.cleaned_data.get("move_drafts") is not False
        questionnaires.publish(version, request.user, move_drafts=move)
        moved = f" {drafts} draft report{'s' if drafts != 1 else ''} moved to it." if move else ""
        return done(
            request, _builder_url(version), f"Version {version.number} published. New reports use it.{moved}"
        )
    return dialog(
        request,
        "staff/dialogs/publish.html",
        {"version": version, "problems": problems, "form": form, "drafts": drafts},
    )


@staff_required
@require_POST
def section_add(request, pk):
    version = get_object_or_404(QuestionnaireVersion, pk=pk)
    try:
        section = questionnaires.add_section(version)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect(_builder_url(version))
    return redirect(_builder_url(version, section))


@staff_required
@require_POST
def section_save(request, pk):
    """Save a section and its questions, then apply the button pressed (add, move, remove)."""
    section = get_object_or_404(Section.objects.select_related("version__project"), pk=pk)
    version = section.version
    if not version.is_draft:
        messages.error(request, "Published versions can't be changed. Start a new draft instead.")
        return redirect(_builder_url(version, section))
    section_form = forms.SectionForm(request.POST, instance=section, prefix="section")
    formset = forms.QuestionFormSet(
        request.POST, queryset=section.questions.order_by("order", "created_at"), prefix="q"
    )
    if not (section_form.is_valid() and formset.is_valid()):
        sections = list(version.sections.annotate(n=Count("questions")).order_by("order", "created_at"))
        return render(
            request,
            "staff/questionnaire.html",
            {
                "project": version.project,
                "tab": "questionnaire",
                "versions": list(version.project.questionnaire_versions.order_by("-number")),
                "version": version,
                "current": questionnaires.current_version(version.project),
                "draft": version,
                "sections": sections,
                "section": section,
                "section_form": section_form,
                "formset": formset,
            },
            status=400,
        )
    section_form.save()
    formset.save()
    action = request.POST.get("action", "save")
    kind, _, target_id = action.partition(":")
    if kind == "add":
        questionnaires.add_question(section)
    elif kind == "delete_section":
        questionnaires.delete_section(section)
        messages.success(request, f"Section “{section.title}” removed.")
        return redirect(_builder_url(version))
    elif kind in {"up", "down", "remove"}:
        question = get_object_or_404(Question, pk=target_id, section=section)
        if kind == "remove":
            questionnaires.delete_question(question)
        else:
            questionnaires.move_question(question, -1 if kind == "up" else 1)
    else:
        messages.success(request, "Section saved.")
    return redirect(_builder_url(version, section))


# --- Clients ----------------------------------------------------------------------------


@staff_required
def client_list(request):
    clients = ClientOrganisation.objects.annotate(project_count=Count("projects")).order_by("name")
    return render(request, "staff/clients.html", {"clients": clients})


@staff_required
def client_edit(request, pk=None):
    client = get_object_or_404(ClientOrganisation, pk=pk) if pk else None
    form = forms.ClientForm(posted(request), instance=client)
    if request.method == "POST" and form.is_valid():
        client = form.save()
        return done(request, reverse("staff:clients"), f"{client.name} saved.")
    return dialog(
        request,
        "staff/dialogs/client.html",
        {"form": form, "client": client, "title": "Edit client" if client else "Add client"},
    )


@staff_required
@require_POST
def client_toggle(request, pk):
    client = get_object_or_404(ClientOrganisation, pk=pk)
    client.is_active = not client.is_active
    client.save(update_fields=["is_active", "updated_at"])
    state = "reactivated" if client.is_active else "deactivated"
    return done(request, reverse("staff:clients"), f"{client.name} {state}.")
