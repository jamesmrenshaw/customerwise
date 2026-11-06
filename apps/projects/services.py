from django.db import transaction
from django.db.models import Max, Q

from .models import BriefAcknowledgement, BriefVersion, Target, domain_from_website


def normalise_brief(text):
    return "\n".join(line.rstrip() for line in (text or "").strip().splitlines())


@transaction.atomic
def update_brief(project, text, user=None):
    """Save the brief as a new version if it changed. Returns the new version, or None."""
    text = normalise_brief(text)
    latest = project.latest_brief
    if not text or (latest and latest.text == text):
        return None
    number = (project.brief_versions.aggregate(n=Max("number"))["n"] or 0) + 1
    version = BriefVersion.objects.create(project=project, number=number, text=text, created_by=user)
    from apps.audit.services import record

    record(user, "brief.saved", version, project=project, version=number)
    return version


def has_acknowledged_latest_brief(user, project):
    latest = project.latest_brief
    if latest is None:
        return True
    return BriefAcknowledgement.objects.filter(brief_version=latest, user=user).exists()


def acknowledge_brief(user, brief_version):
    BriefAcknowledgement.objects.get_or_create(brief_version=brief_version, user=user)


def find_duplicate_targets(project, name, website="", exclude=None):
    """Current targets on the project with the same name or website domain (D05)."""
    match = Q(name__iexact=name.strip())
    domain = domain_from_website(website)
    if domain:
        match |= Q(domain=domain)
    targets = Target.objects.current().filter(project=project).filter(match)
    if exclude is not None:
        targets = targets.exclude(pk=exclude.pk)
    return targets


def last_acknowledged_brief(user, project):
    ack = (
        BriefAcknowledgement.objects.filter(user=user, brief_version__project=project)
        .select_related("brief_version")
        .order_by("-brief_version__number")
        .first()
    )
    return ack.brief_version if ack else None


def brief_changes(old_text, new_text):
    """Line-by-line changes between two brief versions: [("added"|"removed", line), ...]."""
    import difflib

    changes = []
    for line in difflib.ndiff((old_text or "").splitlines(), (new_text or "").splitlines()):
        if line.startswith("+ ") and line[2:].strip():
            changes.append(("added", line[2:]))
        elif line.startswith("- ") and line[2:].strip():
            changes.append(("removed", line[2:]))
    return changes
