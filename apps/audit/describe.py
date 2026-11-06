"""Plain-English descriptions of audit events, and the groups used to filter them."""

GROUPS = {
    "users": ("Users", ("user.",)),
    "setup": ("Setup", ("client.", "project.", "brief.", "target.", "questionnaire.")),
    "fieldwork": ("Fieldwork", ("assignment.", "evidence.")),
    "reports": ("Reports", ("report.",)),
}


def _changes(data):
    changes = data.get("changes") or {}
    labels = {
        "client": "client",
        "status": "status",
        "internal_name": "internal name",
        "client_facing_name": "client-facing name",
        "shopper_facing_name": "shopper-facing name",
        "notification_email": "notification email",
        "name": "name",
        "reference": "reference",
        "role": "role",
        "is_active": "active",
    }
    return "; ".join(f"{labels.get(k, k)} → {v}" for k, v in changes.items())


def describe(event):
    d = event.data
    t = d.get("target", "")
    # "Northwind: …" when the event names a target. Older events may not, and can't be edited.
    pre = f"{t}: " if t else ""
    a = event.action
    if a == "user.invited":
        return f"Invited {d.get('email')} ({d.get('role')})"
    if a == "user.created":
        return f"Created user {d.get('email')} ({d.get('role')})"
    if a == "user.updated":
        return f"Changed user {d.get('email')}: {_changes(d)}"
    if a == "client.created":
        return f"Added client {d.get('name')}"
    if a == "client.updated":
        return f"Updated client {d.get('name')}: {_changes(d)}"
    if a == "client.deactivated":
        return f"Deactivated client {d.get('name')}"
    if a == "client.reactivated":
        return f"Reactivated client {d.get('name')}"
    if a == "project.created":
        return f"Created the project {d.get('name')}"
    if a == "project.updated":
        return f"Changed project details: {_changes(d)}"
    if a == "brief.saved":
        return f"Saved brief version {d.get('version')}"
    if a == "target.added":
        return f"Added target {t}" + (" (by the shopper)" if d.get("by_shopper") else "")
    if a == "target.archived":
        return f"Archived target {t}" + (f": {d['note']}" if d.get("note") else "")
    if a == "questionnaire.draft_started":
        source = (
            f"a copy of version {d['copied_from']}" if d.get("copied_from") else d.get("template", "blank")
        )
        return f"Started questionnaire draft version {d.get('version')} ({source})"
    if a == "questionnaire.published":
        moved = d.get("moved_drafts") or 0
        return f"Published questionnaire version {d.get('version')}" + (
            f"; moved {moved} draft report{'s' if moved != 1 else ''} to it" if moved else ""
        )
    if a == "assignment.created":
        how = "client approval confirmed" if d.get("approval_confirmed") else "added by the shopper"
        return f"Assigned {d.get('shopper')} to {t} ({how})"
    if a == "assignment.reassigned":
        return f"Reassigned {t} from {d.get('previous_shopper')} to {d.get('shopper')}"
    if a == "assignment.withdrawn":
        return f"Withdrew {d.get('shopper')} from {t}" + (f": {d['note']}" if d.get("note") else "")
    if a == "assignment.stage_changed":
        return f"{pre}stage {d.get('from')} → {d.get('to')}"
    if a == "assignment.unable":
        return f"{pre}marked unable to complete ({d.get('reason')})"
    if a == "evidence.uploaded":
        return f"{pre}uploaded {d.get('file')} ({d.get('category')})"
    if a == "evidence.deleted":
        return f"{pre}deleted {d.get('file')}"
    if a == "report.submitted":
        extras = []
        if d.get("with_comment"):
            extras.append("with a comment")
        if d.get("replies"):
            extras.append(f"{d['replies']} repl{'ies' if d['replies'] != 1 else 'y'} to review comments")
        return f"{pre}report version {d.get('version')} submitted" + (
            f" ({', '.join(extras)})" if extras else ""
        )
    if a == "report.returned":
        n = d.get("comments", 0)
        return f"{pre}report version {d.get('version')} returned with {n} comment{'s' if n != 1 else ''}"
    if a == "report.accepted":
        return f"{pre}report version {d.get('version')} accepted"
    if a == "report.exported":
        files = len(d.get("evidence") or [])
        verb = "exported" if pre else "Exported"
        fmt = str(d.get("format", "")).upper()
        return (
            f"{pre}{verb} report version {d.get('version')} as {fmt}"
            + (f" with {files} evidence file{'s' if files != 1 else ''}" if files else "")
            + f" ({d.get('filename')})"
        )
    return a.replace(".", " ").replace("_", " ").capitalize()
