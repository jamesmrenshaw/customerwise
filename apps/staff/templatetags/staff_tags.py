from django import template

register = template.Library()

PROJECT_STATUS = {"draft": "tag--none", "active": "tag--active", "complete": "tag--done"}
STAGE = {
    "not_started": "tag--none",
    "in_review": "tag--review",
    "complete": "tag--done",
}


@register.filter
def project_status_class(status):
    return PROJECT_STATUS.get(status, "tag--none")


@register.filter
def stage_class(assignment):
    if assignment.closed_reason == "unable":
        return "tag--unable"
    return STAGE.get(assignment.stage, "tag--active")


@register.filter
def stage_label(assignment):
    if assignment.closed_reason == "unable":
        return "Unable to complete"
    return assignment.get_stage_display()
