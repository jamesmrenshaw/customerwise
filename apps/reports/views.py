"""The shopper's report editor and evidence (prototype screen 04)."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from apps.core.access import assignments_for, evidence_for, reports_for, shopper_required
from apps.evidence import services as evidence_services
from apps.evidence.models import EvidenceFile
from apps.evidence.validation import ACCEPT
from apps.questionnaires.models import Question

from . import services


def _report(request, pk):
    return get_object_or_404(reports_for(request.user), pk=pk)


def _report_url(report, section=None, view=None):
    url = reverse("reports:report", args=[report.assignment_id])
    if view:
        return f"{url}?view={view}"
    return f"{url}?s={section.pk}" if section else url


def _parse_value(question, raw):
    if question.answer_type == Question.AnswerType.DATETIME:
        if not raw:
            return None
        value = parse_datetime(raw)
        if value is None:
            raise ValidationError("Enter a date and time.")
        return timezone.make_aware(value) if timezone.is_naive(value) else value
    return raw


def _context(request, report, problems=None):
    version = report.questionnaire_version
    sections = list(version.sections.prefetch_related("questions"))
    answers = services.answers_by_question(report)
    view = request.GET.get("view", "")
    current = next((s for s in sections if str(s.pk) == request.GET.get("s")), None) or (
        sections[0] if sections else None
    )
    from .review import returned_comments

    comments = list(returned_comments(report))
    by_key = {}
    for c in comments:
        by_key.setdefault(c.question_key, []).append(c)
    section_of = {q.key: q.section for s in sections for q in s.questions.all()}
    comment_list = [
        {
            "comment": c,
            "section": section_of.get(c.question_key),
            "prompt": c.question.prompt if c.question else "",
        }
        for c in comments
    ]
    total = answered = 0
    nav = []
    for s in sections:
        qs = list(s.questions.all())
        done_here = sum(1 for q in qs if answers.get(q.pk) and answers[q.pk].is_answered)
        total += len(qs)
        answered += done_here
        nav.append(
            {
                "section": s,
                "done": done_here,
                "count": len(qs),
                "complete": done_here == len(qs),
                "has_comment": any(q.key in by_key for q in qs),
            }
        )
    missing_ids = {p["question"].pk for p in problems or [] if p["kind"] == "question"}
    items = []
    if current and not view:
        for i, q in enumerate(current.questions.all(), start=1):
            a = answers.get(q.pk)
            items.append(
                {
                    "q": q,
                    "n": i,
                    "text": a.text if a else "",
                    "datetime": a.datetime_value if a else None,
                    "missing": q.pk in missing_ids,
                    "comments": by_key.get(q.key, []),
                }
            )
    index = sections.index(current) if current in sections else 0
    return {
        "report": report,
        "comment_list": comment_list,
        "assignment": report.assignment,
        "target": report.assignment.target,
        "editable": services.can_edit(report),
        "nav": nav,
        "section": current,
        "items": items,
        "section_number": index + 1,
        "prev_section": sections[index - 1] if current and index > 0 else None,
        "next_section": sections[index + 1] if current and index + 1 < len(sections) else None,
        "view": view,
        "answered": answered,
        "total": total,
        "problems": problems,
        "evidence": evidence_for(request.user).filter(assignment=report.assignment).order_by("created_at"),
        "categories": EvidenceFile.Category.choices,
        "accept": ACCEPT,
        "last_saved": report.updated_at,
        "submissions": report.submissions.all(),
    }


@shopper_required
def report(request, pk):
    """Open (and on first use, start) the report for an assignment."""
    assignment = get_object_or_404(assignments_for(request.user), pk=pk)
    try:
        report = services.start_report(assignment)
    except services.NoQuestionnaire as exc:
        messages.error(request, exc.messages[0])
        return redirect("shopper:assignment", pk=assignment.pk)
    return render(request, "reports/report.html", _context(request, report))


@shopper_required
@require_POST
def autosave(request, pk, question_pk):
    """Save one answer as the shopper types (HTMX). Returns the save-status text."""
    report = _report(request, pk)
    question = get_object_or_404(services.questions(report), pk=question_pk)
    try:
        services.save_answer(
            report, question, _parse_value(question, request.POST.get(f"q-{question.pk}", ""))
        )
    except ValidationError as exc:
        return HttpResponse(" ".join(exc.messages), status=422)
    return render(request, "reports/_saved.html", {"when": timezone.now()})


@shopper_required
@require_POST
def save_section(request, pk):
    """Save every answer in the posted section (the Save draft button; works without JavaScript)."""
    report = _report(request, pk)
    section = None
    try:
        for question in services.questions(report):
            name = f"q-{question.pk}"
            if name in request.POST:
                services.save_answer(report, question, _parse_value(question, request.POST[name]))
                section = question.section
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect(_report_url(report, section))
    target = request.POST.get("go")
    if target == "next" and request.POST.get("next"):
        return redirect(_report_url(report) + f"?s={request.POST['next']}")
    if target == "prev" and request.POST.get("prev"):
        return redirect(_report_url(report) + f"?s={request.POST['prev']}")
    if target == "evidence":
        return redirect(_report_url(report, view="evidence"))
    messages.success(request, "Draft saved.")
    return redirect(_report_url(report, section))


@shopper_required
@require_POST
def save_notes(request, pk):
    report = _report(request, pk)
    try:
        services.save_notes(report, request.POST.get("notes", ""))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Notes saved.")
    return redirect(_report_url(report, view="notes"))


@shopper_required
def submit(request, pk):
    """Submit dialog: lists anything missing, otherwise takes an optional comment and replies."""
    from apps.core.ui import dialog, done

    from .review import returned_comments

    report = _report(request, pk)
    if not services.can_edit(report):
        messages.error(request, "This report has already been submitted.")
        return redirect(_report_url(report))
    problems = services.submission_problems(report, request.user)
    comments = list(returned_comments(report))
    resubmitting = report.status == report.Status.RETURNED
    if request.method == "POST" and not problems:
        replies = {str(c.pk): request.POST.get(f"reply-{c.pk}", "") for c in comments}
        try:
            services.submit(report, request.user, comment=request.POST.get("comment", ""), replies=replies)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect(_report_url(report))
        return done(
            request,
            reverse("shopper:assignment", args=[report.assignment_id]),
            f"Report {'re' if resubmitting else ''}submitted to Customer Wise. It's now in review; "
            "you can't change it unless Customer Wise returns it.",
        )
    return dialog(
        request,
        "reports/dialogs/submit.html",
        {
            "report": report,
            "problems": problems,
            "comments": comments,
            "resubmitting": resubmitting,
            "comment": request.POST.get("comment", ""),
        },
    )


# --- Evidence -------------------------------------------------------------------------------


@shopper_required
@require_POST
def evidence_upload(request, pk):
    report = _report(request, pk)
    files = request.FILES.getlist("files")
    if not files:
        messages.error(request, "Choose at least one file to upload.")
    added, failed = [], []
    for upload in files:
        try:
            evidence_services.add_evidence(report.assignment, upload, by=request.user)
            added.append(upload.name)
        except ValidationError as exc:
            failed.append(" ".join(exc.messages))
    if added:
        messages.success(request, f"Uploaded {', '.join(added)}.")
    for problem in failed:
        messages.error(request, problem)
    return redirect(_report_url(report, view="evidence"))


def _evidence(request, pk):
    return get_object_or_404(evidence_for(request.user).select_related("assignment"), pk=pk)


@shopper_required
@require_POST
def evidence_category(request, pk):
    evidence = _evidence(request, pk)
    try:
        evidence_services.set_category(evidence, request.POST.get("category", ""))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    report = evidence.assignment.report
    return redirect(_report_url(report, view="evidence"))


@shopper_required
@require_POST
def evidence_delete(request, pk):
    evidence = _evidence(request, pk)
    try:
        evidence_services.delete_evidence(evidence)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"Deleted {evidence.original_name}.")
    return redirect(_report_url(evidence.assignment.report, view="evidence"))


def evidence_download(request, pk):
    """Shoppers (their own files) and staff can download. Files are never public (CW-041)."""
    user = request.user
    if not user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())
    evidence = _evidence(request, pk)
    if evidence.scan_status == EvidenceFile.ScanStatus.REJECTED:
        raise Http404
    storage = evidence.file.storage
    disposition = f'attachment; filename="{evidence.original_name.replace(chr(34), "")}"'
    if hasattr(storage, "bucket_name"):
        # Private bucket: a presigned link that expires after AWS_QUERYSTRING_EXPIRE (5 minutes).
        url = storage.url(
            evidence.file.name,
            parameters={
                "ResponseContentDisposition": disposition,
                "ResponseContentType": evidence.content_type,
            },
        )
        return redirect(url)
    return FileResponse(
        evidence.file.open("rb"),
        as_attachment=True,
        filename=evidence.original_name,
        content_type=evidence.content_type,
    )
