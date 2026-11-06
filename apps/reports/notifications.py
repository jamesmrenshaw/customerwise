"""Report emails (added for the pilot, 6 Nov 2026).

- Submitted or resubmitted: to the project's notification email (Customer Wise staff).
- Returned or accepted: to the shopper.

Emails are sent after the database change commits, and a failed send never undoes or blocks the
action; it is logged instead. Shopper emails only use the shopper-facing project name.
"""

import logging

from django.db import transaction
from django.urls import reverse

from apps.accounts.services import absolute_url
from apps.core.emails import send_email

logger = logging.getLogger(__name__)


def _send(to, subject, template, context):
    try:
        send_email(to, subject, template, context)
    except Exception:
        logger.exception("Report email %r to %s failed", subject, to)


def _later(to, subject, template, context):
    if not to:
        return
    transaction.on_commit(lambda: _send(to, subject, template, context))


def report_submitted(report, *, comment="", replies=0):
    project = report.assignment.target.project
    if not project.notification_email:
        logger.warning("No notification email on project %s; submission email not sent", project.pk)
        return
    target = report.assignment.target.name
    resubmitted = report.version_no > 1
    _later(
        project.notification_email,
        f"Report {'resubmitted' if resubmitted else 'submitted'}: {target} ({project.internal_name})",
        "notifications/report_submitted",
        {
            "report": report,
            "project": project,
            "target": target,
            "shopper": report.assignment.shopper,
            "resubmitted": resubmitted,
            "comment": comment,
            "replies": replies,
            "link": absolute_url(reverse("staff:review", args=[report.pk])),
        },
    )


def report_returned(report, *, comments, message=""):
    shopper = report.assignment.shopper
    target = report.assignment.target.name
    _later(
        shopper.email,
        f"Your report on {target} has been returned for changes",
        "notifications/report_returned",
        {
            "shopper": shopper,
            "target": target,
            "project_name": report.assignment.target.project.shopper_facing_name,
            "comments": comments,
            "message": message,
            "link": absolute_url(reverse("reports:report", args=[report.assignment_id])),
        },
    )


def report_accepted(report):
    shopper = report.assignment.shopper
    target = report.assignment.target.name
    _later(
        shopper.email,
        f"Your report on {target} has been accepted",
        "notifications/report_accepted",
        {
            "shopper": shopper,
            "target": target,
            "project_name": report.assignment.target.project.shopper_facing_name,
            "link": absolute_url(reverse("shopper:assignment", args=[report.assignment_id])),
        },
    )
