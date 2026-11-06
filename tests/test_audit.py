"""Audit log (CUS-120, CW-085): key actions are recorded, readable by staff only, never editable."""

import html as htmllib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.accounts import services as accounts
from apps.accounts.models import User
from apps.audit.describe import describe
from apps.audit.models import AuditEvent
from apps.evidence import services as evidence
from apps.fieldwork import services as fieldwork
from apps.projects import services as projects
from apps.projects.models import ClientOrganisation, Project, Target
from apps.questionnaires import services as questionnaires
from apps.questionnaires.models import Question, QuestionnaireVersion, Section
from apps.reports import review
from apps.reports import services as reports

pytestmark = pytest.mark.django_db


def text(response):
    return htmllib.unescape(response.content.decode())


@pytest.fixture
def staff():
    return User.objects.create_staff("alex@customerwise.example", full_name="Alex Morgan")


@pytest.fixture
def sam():
    return User.objects.create_user("sam@example.com", full_name="Sam Okafor")


@pytest.fixture
def priya():
    return User.objects.create_user("priya@example.com", full_name="Priya Shah")


@pytest.fixture
def project():
    client = ClientOrganisation.objects.create(name="Harbourside Bank")
    return Project.objects.create(
        client=client,
        internal_name="Harbourside messaging",
        client_facing_name="Messaging review",
        shopper_facing_name="Capture review",
        status=Project.Status.ACTIVE,
    )


def last(action):
    return AuditEvent.objects.filter(action=action).latest("at")


def test_fieldwork_actions_are_recorded(project, staff, sam, priya):
    target = Target.objects.create(project=project, name="Northwind")
    a = fieldwork.assign(target, sam, by=staff, approval_confirmed=True)
    assert (
        describe(last("assignment.created")) == "Assigned Sam Okafor to Northwind (client approval confirmed)"
    )
    fieldwork.set_stage(a, "follow_up", by=sam)
    stage = last("assignment.stage_changed")
    assert stage.actor == sam and describe(stage) == "Northwind: stage Not started → Follow up"
    new = fieldwork.reassign(a, priya, by=staff, approval_confirmed=True)
    assert describe(last("assignment.reassigned")) == "Reassigned Northwind from Sam Okafor to Priya Shah"
    assert AuditEvent.objects.filter(action="assignment.created").count() == 1  # not double-logged
    fieldwork.withdraw(new, by=staff, note="Paused")
    assert describe(last("assignment.withdrawn")) == "Withdrew Priya Shah from Northwind: Paused"
    other = fieldwork.assign(
        Target.objects.create(project=project, name="Halden"), sam, by=staff, approval_confirmed=True
    )
    fieldwork.mark_unable(other, "no_response", "Four attempts", by=sam)
    assert describe(last("assignment.unable")) == "Halden: marked unable to complete (No response)"
    fieldwork.archive_target(target, by=staff, note="Out of scope")
    assert describe(last("target.archived")) == "Archived target Northwind: Out of scope"
    # Shoppers can only add targets to projects they're still working on.
    fieldwork.assign(
        Target.objects.create(project=project, name="Keystone"), sam, by=staff, approval_confirmed=True
    )
    added = fieldwork.add_target_by_shopper(project, sam, "Telvar")
    assert describe(last("target.added")) == "Added target Telvar (by the shopper)"
    assert describe(last("assignment.created")) == "Assigned Sam Okafor to Telvar (added by the shopper)"
    assert added.shopper == sam
    assert AuditEvent.objects.filter(project=project).count() == AuditEvent.objects.count()


def test_report_and_evidence_actions_are_recorded(project, staff, sam):
    v = QuestionnaireVersion.objects.create(project=project, number=1)
    section = Section.objects.create(version=v, title="Commercial")
    q = Question.objects.create(section=section, key="pricing", prompt="Pricing?", answer_type="short_text")
    questionnaires.publish(v, staff)
    assert describe(last("questionnaire.published")) == "Published questionnaire version 1"
    a = fieldwork.assign(
        Target.objects.create(project=project, name="Northwind"), sam, by=staff, approval_confirmed=True
    )
    report = reports.start_report(a)
    reports.save_answer(report, q, "Per user")
    upload = evidence.add_evidence(a, SimpleUploadedFile("quote.pdf", b"%PDF-1 x"), by=sam)
    assert describe(last("evidence.uploaded")) == "Northwind: uploaded quote.pdf (Supplier document)"
    evidence.delete_evidence(upload, by=sam)
    assert describe(last("evidence.deleted")) == "Northwind: deleted quote.pdf"
    report = reports.submit(report, sam, comment="All done")
    assert describe(last("report.submitted")) == "Northwind: report version 1 submitted (with a comment)"
    review.add_comment(report, author=staff, text="More detail", question=q)
    review.return_report(report, by=staff)
    assert describe(last("report.returned")) == "Northwind: report version 1 returned with 1 comment"
    report.refresh_from_db()
    comment = review.returned_comments(report).get()
    report = reports.submit(report, sam, replies={str(comment.pk): "Done"})
    assert (
        describe(last("report.submitted"))
        == "Northwind: report version 2 submitted (1 reply to review comments)"
    )
    review.accept_report(report, by=staff)
    accepted = last("report.accepted")
    assert accepted.actor == staff and describe(accepted) == "Northwind: report version 2 accepted"


def test_setup_actions_through_the_screens_are_recorded(client, staff, project, sam):
    client.force_login(staff)
    client.post(reverse("staff:client_add"), {"name": "Northgate Insurance"})
    assert describe(last("client.created")) == "Added client Northgate Insurance"
    northgate = ClientOrganisation.objects.get(name="Northgate Insurance")
    client.post(reverse("staff:client_toggle", args=[northgate.pk]))
    assert describe(last("client.deactivated")) == "Deactivated client Northgate Insurance"
    data = {
        "client": project.client.pk,
        "status": "complete",
        "notification_email": "reviews@customerwise.example",
        "internal_name": project.internal_name,
        "client_facing_name": "Messaging vendor review",
        "shopper_facing_name": project.shopper_facing_name,
        "brief": "Be a genuine buyer.",
    }
    client.post(reverse("staff:project_details", args=[project.pk]), data)
    updated = last("project.updated")
    assert "client-facing name → Messaging vendor review" in describe(updated)
    assert "status → complete" in describe(updated)
    assert describe(last("brief.saved")) == "Saved brief version 1"
    client.post(reverse("staff:target_add", args=[project.pk]), {"name": "Keystone"})
    assert describe(last("target.added")) == "Added target Keystone"
    client.post(reverse("admin:accounts_user_send_invitation", args=[sam.pk]))
    invited = last("user.invited")
    assert invited.actor == staff and describe(invited) == "Invited sam@example.com (Shopper)"


def test_audit_screens_are_staff_only_and_filterable(client, staff, sam, project):
    fieldwork.assign(
        Target.objects.create(project=project, name="Northwind"), sam, by=staff, approval_confirmed=True
    )
    projects.update_brief(project, "Brief", staff)
    other = Project.objects.create(
        client=project.client, internal_name="Other project", client_facing_name="O", shopper_facing_name="O2"
    )
    projects.update_brief(other, "Other brief", staff)
    accounts.send_invitation(sam, sent_by=staff)

    client.force_login(sam)
    assert client.get(reverse("staff:audit")).status_code == 404
    assert client.get(reverse("staff:project_audit", args=[project.pk])).status_code == 404

    client.force_login(staff)
    page = text(client.get(reverse("staff:audit")))
    assert "Assigned Sam Okafor to Northwind" in page and "Invited sam@example.com" in page
    assert "Harbourside messaging" in page and "Other project" in page
    assert "Audit log" in page
    users_only = text(client.get(reverse("staff:audit") + "?type=users"))
    assert "Invited sam@example.com" in users_only and "Assigned Sam Okafor" not in users_only
    project_page = text(client.get(reverse("staff:project_audit", args=[project.pk])))
    assert "Assigned Sam Okafor to Northwind" in project_page
    assert "Other brief" not in project_page and project_page.count("Saved brief version 1") == 1


def test_timestamps_and_actor_are_shown(client, staff, sam, project):
    from apps.core.templatetags.ui import stamp

    a = fieldwork.assign(
        Target.objects.create(project=project, name="Northwind"), sam, by=staff, approval_confirmed=True
    )
    fieldwork.set_stage(a, "planned", by=sam)
    client.force_login(staff)
    page = client.get(reverse("staff:project_audit", args=[project.pk])).content.decode()
    event = last("assignment.stage_changed")
    assert stamp(event.at) in page
    assert "Sam Okafor" in page and "Shopper</span>" in page
    assert timezone.now() >= event.at


def test_descriptions_cope_with_events_missing_a_target(staff):
    from apps.audit.services import record

    event = record(staff, "report.exported", None, format="pdf", version=1, evidence=[], filename="r.pdf")
    assert describe(event) == "Exported report version 1 as PDF (r.pdf)"
