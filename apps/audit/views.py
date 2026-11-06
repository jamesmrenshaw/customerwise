"""Read-only audit log for Customer Wise staff (CW-085). Nothing here can change an entry."""

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from apps.core.access import staff_required
from apps.projects.models import Project

from .describe import GROUPS, describe
from .models import AuditEvent

PER_PAGE = 100


def _filtered(request, events):
    group = request.GET.get("type", "")
    if group in GROUPS:
        match = Q()
        for prefix in GROUPS[group][1]:
            match |= Q(action__startswith=prefix)
        events = events.filter(match)
    else:
        group = ""
    return events, group


def _render(request, events, project=None):
    events, group = _filtered(request, events.select_related("actor", "project"))
    page = Paginator(events, PER_PAGE).get_page(request.GET.get("page"))
    rows = [{"event": e, "text": describe(e)} for e in page]
    return render(
        request,
        "audit/log.html",
        {
            "project": project,
            "tab": "audit",
            "page": page,
            "rows": rows,
            "group": group,
            "groups": [("", "All")] + [(k, label) for k, (label, _) in GROUPS.items()],
        },
    )


@staff_required
def audit_log(request):
    return _render(request, AuditEvent.objects.all())


@staff_required
def project_audit_log(request, pk):
    project = get_object_or_404(Project, pk=pk)
    return _render(request, AuditEvent.objects.filter(project=project), project=project)
