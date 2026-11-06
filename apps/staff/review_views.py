"""Staff fieldwork monitoring and report review (prototype screens 05 and 06; CUS-68, CUS-80 to CUS-84)."""

from datetime import timedelta

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Max, Min, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.access import staff_required
from apps.core.ui import dialog, done, posted
from apps.fieldwork.models import Activity, Assignment
from apps.fieldwork.services import LOCKED_STAGES
from apps.projects.models import Project
from apps.questionnaires.models import Question
from apps.reports import review
from apps.reports.models import Report, ReviewComment
from apps.reports.rendering import build_document

STALLED_AFTER = timedelta(days=7)  # D08

FIELDWORK_FILTERS = [
    ("all", "All"),
    ("review", "Awaiting review"),
    ("stalled", "Stalled"),
    ("returned", "Returned"),
    ("progress", "In progress"),
    ("shopper", "Added by shopper"),
    ("unable", "Unable to complete"),
]


def _row(assignment, now):
    report = getattr(assignment, "report", None)
    open_ = assignment.is_open
    waiting_on_shopper = open_ and assignment.stage not in LOCKED_STAGES
    stalled = waiting_on_shopper and assignment.last_activity_at < now - STALLED_AFTER
    status = report.status if report else None
    return {
        "a": assignment,
        "report": report,
        "stalled": stalled,
        "stalled_days": (now - assignment.last_activity_at).days if stalled else 0,
        "awaiting_review": status == Report.Status.SUBMITTED,
        "returned": status == Report.Status.RETURNED,
        "accepted": status == Report.Status.ACCEPTED,
        "unable": assignment.closed_reason == Assignment.ClosedReason.UNABLE,
        "in_progress": waiting_on_shopper,
        "by_shopper": assignment.target.added_by_shopper,
    }


ROW_TESTS = {
    "all": lambda r: True,
    "review": lambda r: r["awaiting_review"],
    "stalled": lambda r: r["stalled"],
    "returned": lambda r: r["returned"],
    "progress": lambda r: r["in_progress"],
    "shopper": lambda r: r["by_shopper"],
    "unable": lambda r: r["unable"],
}


@staff_required
def fieldwork(request, pk):
    project = get_object_or_404(Project, pk=pk)
    now = timezone.now()
    current = Q(activities__deleted_at__isnull=True)
    assignments = (
        Assignment.objects.filter(target__project=project, target__archived_at__isnull=True)
        .filter(Q(closed_at__isnull=True) | Q(closed_reason=Assignment.ClosedReason.UNABLE))
        .select_related("target", "shopper", "report")
        .annotate(
            last_done=Max("activities__occurred_at", filter=current & Q(activities__occurred_at__lte=now)),
            next_planned=Min("activities__occurred_at", filter=current & Q(activities__occurred_at__gt=now)),
        )
        .order_by("target__name")
    )
    rows = [_row(a, now) for a in assignments]
    shown = request.GET.get("show", "all")
    if shown not in ROW_TESTS:
        shown = "all"
    count = lambda key: sum(1 for r in rows if ROW_TESTS[key](r))  # noqa: E731
    return render(
        request,
        "staff/fieldwork.html",
        {
            "project": project,
            "tab": "fieldwork",
            "rows": [r for r in rows if ROW_TESTS[shown](r)],
            "filters": [(k, label, count(k)) for k, label in FIELDWORK_FILTERS],
            "shown": shown,
            "tiles": [
                ("Assignments", len(rows), ""),
                ("In progress", count("progress"), "tile--active"),
                ("Awaiting review", count("review"), "tile--review"),
                ("Stalled", count("stalled"), "tile--warn"),
                ("Unable to complete", count("unable"), ""),
            ],
        },
    )


@staff_required
def assignment(request, pk):
    """Read-only staff view of one assignment's fieldwork, including edits and deletions (D10)."""
    a = get_object_or_404(
        Assignment.objects.select_related("target__project", "shopper", "assigned_by"), pk=pk
    )
    activities = list(
        Activity.objects.filter(assignment=a).select_related("created_by").order_by("-occurred_at")
    )
    for activity in activities:
        history = list(activity.history.order_by("history_date").select_related("history_user"))
        activity.edits = [h for h in history[1:] if h.history_type == "~"]
    report = Report.objects.filter(assignment=a).first()
    return render(
        request,
        "staff/assignment.html",
        {
            "project": a.target.project,
            "tab": "fieldwork",
            "assignment": a,
            "row": _row(a, timezone.now()),
            "activities": activities,
            "report": report,
            "evidence": a.evidence.all().order_by("created_at"),
            "earlier": Assignment.objects.filter(target=a.target).exclude(pk=a.pk).select_related("shopper"),
        },
    )


# --- Review -----------------------------------------------------------------------------


class CommentForm(forms.Form):
    text = forms.CharField(
        label="Comment for the shopper", widget=forms.Textarea(attrs={"rows": 3, "class": "input"})
    )


class ReturnForm(forms.Form):
    message = forms.CharField(
        label="Overall message",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "input"}),
    )


def _report(pk):
    return get_object_or_404(
        Report.objects.select_related(
            "assignment__target__project", "assignment__shopper", "questionnaire_version"
        ),
        pk=pk,
    )


def _review_url(report, anchor=""):
    return reverse("staff:review", args=[report.pk]) + (f"#{anchor}" if anchor else "")


@staff_required
def review_report(request, pk):
    report = _report(pk)
    submission = review.latest_submission(report)
    if submission is None:
        messages.error(request, "This report hasn't been submitted yet.")
        return redirect("staff:assignment", pk=report.assignment_id)
    document = build_document(submission.snapshot)
    drafts = list(review.draft_comments(report))
    by_key = {}
    for c in drafts:
        by_key.setdefault(c.question_key, []).append(c)
    previous = list(review.previous_round(report, submission))
    previous_by_key = {}
    for c in previous:
        previous_by_key.setdefault(c.question_key, []).append(c)
    editing = request.GET.get("edit", "")
    for section in [{"items": document["key_facts"]}, *document["sections"]]:
        for item in section["items"]:
            item["comments"] = by_key.get(item["key"], [])
            item["previous"] = previous_by_key.get(item["key"], [])
            item["commenting"] = editing == item["key"]
    sent = report.comments.filter(sent_at__isnull=False).select_related("author", "submission", "question")
    evidence_ids = {e["id"] for e in submission.snapshot.get("evidence", [])}
    return render(
        request,
        "staff/review.html",
        {
            "project": report.assignment.target.project,
            "report": report,
            "assignment": report.assignment,
            "submission": submission,
            "document": document,
            "draft_count": len(drafts),
            "general_drafts": by_key.get("", []),
            "previous_general": previous_by_key.get("", []),
            "previous_count": len(previous),
            "can_review": report.status == Report.Status.SUBMITTED,
            "evidence": report.assignment.evidence.filter(pk__in=evidence_ids).order_by("created_at"),
            "notes": submission.snapshot.get("notes", ""),
            "notes_updated_at": report.notes_updated_at
            if submission.snapshot.get("notes") == report.notes
            else None,
            "sent_comments": sent,
            "submissions": report.submissions.select_related("submitted_by").order_by("-number"),
            "comment_form": CommentForm(),
        },
    )


@staff_required
@require_POST
def comment_add(request, pk):
    report = _report(pk)
    key = request.POST.get("question", "")
    question = None
    if key:
        question = get_object_or_404(Question, section__version=report.questionnaire_version, key=key)
    form = CommentForm(request.POST)
    if form.is_valid():
        try:
            review.add_comment(report, author=request.user, text=form.cleaned_data["text"], question=question)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    else:
        messages.error(request, "Write a comment first.")
    return redirect(_review_url(report, f"q-{key}" if key else "review"))


def _comment(pk):
    return get_object_or_404(ReviewComment.objects.select_related("report"), pk=pk)


@staff_required
@require_POST
def comment_update(request, pk):
    comment = _comment(pk)
    try:
        if request.POST.get("delete"):
            review.delete_comment(comment)
        else:
            review.update_comment(comment, request.POST.get("text", ""))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    anchor = f"q-{comment.question_key}" if comment.question_key else "review"
    return redirect(_review_url(comment.report, anchor))


@staff_required
def return_report(request, pk):
    report = _report(pk)
    form = ReturnForm(posted(request))
    drafts = review.draft_comments(report).count()
    if request.method == "POST" and form.is_valid():
        try:
            review.return_report(report, by=request.user, message=form.cleaned_data["message"])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            shopper = report.assignment.shopper
            return done(
                request,
                _review_url(report),
                f"Returned to {shopper}. It shows as returned in their assignment list; "
                "the submitted version and your comments are kept in the history.",
            )
    return dialog(request, "staff/dialogs/return.html", {"form": form, "report": report, "drafts": drafts})


@staff_required
@require_POST
def accept_report(request, pk):
    report = _report(pk)
    try:
        review.accept_report(report, by=request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"Report on {report.assignment.target} accepted.")
    return redirect(_review_url(report))


# --- Export (MVP-02, CUS-142) -------------------------------------------------------------


@staff_required
def export_report(request, pk):
    """GET: the export dialog. POST: download the PDF/Word report, or a ZIP with chosen evidence."""
    from django.http import HttpResponse

    from apps.core.ui import dialog
    from apps.exports import builder

    report = _report(pk)
    submission = review.latest_submission(report)
    if not builder.exportable(report):
        messages.error(request, "Only a submitted or accepted report can be exported.")
        return redirect(_review_url(report))
    evidence = report.assignment.evidence.filter(
        pk__in=[e["id"] for e in submission.snapshot.get("evidence", [])]
    ).order_by("created_at")
    if request.method == "POST":
        try:
            result = builder.export_report(
                report,
                user=request.user,
                fmt=request.POST.get("format", builder.PDF),
                evidence_ids=request.POST.getlist("evidence"),
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect(_review_url(report))
        response = HttpResponse(result.content, content_type=result.content_type)
        response["Content-Disposition"] = f'attachment; filename="{result.filename}"'
        return response
    return dialog(
        request,
        "staff/dialogs/export.html",
        {"report": report, "submission": submission, "evidence": evidence},
    )
