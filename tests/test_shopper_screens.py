"""Shopper screens 02 and 03: My assignments and the assignment page (CUS-63 to CUS-67, CUS-141)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.fieldwork import services as fieldwork
from apps.fieldwork.models import Activity, Assignment
from apps.projects import services as projects
from apps.projects.models import ClientOrganisation, Project, Target

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def sam():
    return User.objects.create_user("sam@example.com", full_name="Sam Okafor")


@pytest.fixture
def priya():
    return User.objects.create_user("priya@example.com", full_name="Priya Shah")


@pytest.fixture
def staff():
    return User.objects.create_staff("alex@customerwise.example", full_name="Alex Morgan")


@pytest.fixture
def project():
    client = ClientOrganisation.objects.create(name="Harbourside Bank")
    return Project.objects.create(
        client=client,
        internal_name="Harbourside: messaging vendors",
        client_facing_name="Messaging capture vendor review",
        shopper_facing_name="Mobile messaging capture review",
        status=Project.Status.ACTIVE,
    )


@pytest.fixture
def assignment(project, sam):
    target = Target.objects.create(project=project, name="Global Relay", website="globalrelay.com")
    return fieldwork.assign(target, sam, approval_confirmed=True)


@pytest.fixture
def as_sam(client, sam):
    client.force_login(sam)
    return client


def activity_data(**overrides):
    when = timezone.localtime() - timedelta(hours=2)
    data = {
        "type": "email",
        "occurred_at": when.strftime("%Y-%m-%dT%H:%M"),
        "contact_job_title": "Account executive",
        "notes": "First enquiry through the website form.",
    }
    data.update(overrides)
    return data


# --- Access and privacy -----------------------------------------------------------------


def test_shopper_home_is_my_assignments(as_sam):
    assert as_sam.get("/")["Location"] == reverse("shopper:assignments")


def test_shoppers_only_see_their_own_assignments(client, assignment, priya, project):
    other = fieldwork.assign(
        Target.objects.create(project=project, name="Northwind"), priya, approval_confirmed=True
    )
    client.force_login(assignment.shopper)
    html = client.get(reverse("shopper:assignments")).content.decode()
    assert "Global Relay" in html and "Northwind" not in html
    assert client.get(reverse("shopper:assignment", args=[other.pk])).status_code == 404
    assert (
        client.post(reverse("shopper:assignment_stage", args=[other.pk]), {"stage": "planned"}).status_code
        == 404
    )


def test_shoppers_never_see_internal_or_client_names(as_sam, assignment):
    for url in (reverse("shopper:assignments"), reverse("shopper:assignment", args=[assignment.pk])):
        html = as_sam.get(url).content.decode()
        assert "Mobile messaging capture review" in html
        assert "Harbourside" not in html
        assert "Messaging capture vendor review" not in html


def test_staff_cannot_use_shopper_screens(client, staff, assignment):
    client.force_login(staff)
    assert client.get(reverse("shopper:assignments")).status_code == 404
    assert client.get(reverse("shopper:assignment", args=[assignment.pk])).status_code == 404


def test_reassigned_shopper_loses_access(client, assignment, priya):
    fieldwork.reassign(assignment, priya, approval_confirmed=True)
    client.force_login(assignment.shopper)
    assert client.get(reverse("shopper:assignment", args=[assignment.pk])).status_code == 404


# --- Stages -----------------------------------------------------------------------------


def test_set_stage_and_step_back(as_sam, assignment):
    url = reverse("shopper:assignment_stage", args=[assignment.pk])
    as_sam.post(url, {"stage": "follow_up"})
    assignment.refresh_from_db()
    assert assignment.stage == Assignment.Stage.FOLLOW_UP
    as_sam.post(url, {"stage": "planned"})
    assignment.refresh_from_db()
    assert assignment.stage == Assignment.Stage.PLANNED


def test_shopper_cannot_set_complete(as_sam, assignment):
    response = as_sam.post(
        reverse("shopper:assignment_stage", args=[assignment.pk]), {"stage": "complete"}, follow=True
    )
    assert "In review and Complete are set by submitting" in response.content.decode()
    assignment.refresh_from_db()
    assert assignment.stage == Assignment.Stage.NOT_STARTED


def test_unable_to_complete_closes_but_stays_visible(as_sam, assignment):
    url = reverse("shopper:assignment_unable", args=[assignment.pk])
    assert "Choose a reason" in as_sam.post(url, {"reason": ""}, **HTMX).content.decode()
    response = as_sam.post(url, {"reason": "no_response", "note": "Four attempts"}, **HTMX)
    assert response.status_code == 204
    assignment.refresh_from_db()
    assert assignment.closed_reason == Assignment.ClosedReason.UNABLE
    assert assignment.unable_reason == "no_response" and assignment.unable_note == "Four attempts"
    html = as_sam.get(reverse("shopper:assignments") + "?stage=closed").content.decode()
    assert "Global Relay" in html and "Unable to complete" in html
    detail = as_sam.get(reverse("shopper:assignment", args=[assignment.pk])).content.decode()
    assert "Closed as unable to complete" in detail
    assert "Add to log" not in detail


# --- Activity log -------------------------------------------------------------------------


def test_log_and_plan_activities(as_sam, assignment):
    url = reverse("shopper:activity_add", args=[assignment.pk])
    as_sam.post(url, activity_data())
    future = (timezone.localtime() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    as_sam.post(url, activity_data(type="video", occurred_at=future, notes="Discovery call"))
    assert assignment.activities.count() == 2
    html = as_sam.get(reverse("shopper:assignment", args=[assignment.pk])).content.decode()
    planned, history = html.split(">History<")
    assert "Discovery call" in planned
    assert "First enquiry" in history
    assignment.refresh_from_db()
    assert timezone.now() - assignment.last_activity_at < timedelta(minutes=1)
    listing = as_sam.get(reverse("shopper:assignments")).content.decode()
    assert "Nothing planned" not in listing


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contact_job_title", "jane.doe@globalrelay.com"),
        ("notes", "Call back on +44 20 7946 0000"),
        ("notes", "Her email is jane@example.org"),
    ],
)
def test_contact_details_are_refused(as_sam, assignment, field, value):
    response = as_sam.post(
        reverse("shopper:activity_add", args=[assignment.pk]), activity_data(**{field: value})
    )
    assert response.status_code == 400
    assert "Record people by job title only" in response.content.decode()
    assert not assignment.activities.exists()


def test_edit_and_delete_keep_history(as_sam, assignment):
    fieldwork.add_activity(
        assignment, by=assignment.shopper, type="email", occurred_at=timezone.now(), notes="Original"
    )
    activity = Activity.objects.get()
    as_sam.post(reverse("shopper:activity_edit", args=[activity.pk]), activity_data(notes="Edited"))
    activity.refresh_from_db()
    assert activity.notes == "Edited"
    as_sam.post(reverse("shopper:activity_delete", args=[activity.pk]))
    activity.refresh_from_db()
    assert activity.deleted_at is not None
    assert "Edited" not in as_sam.get(reverse("shopper:assignment", args=[assignment.pk])).content.decode()
    versions = [h.notes for h in activity.history.order_by("history_date")]
    assert versions[0] == "Original" and "Edited" in versions
    assert activity.history.count() == 3


def test_activities_are_locked_once_complete(as_sam, assignment):
    fieldwork.add_activity(
        assignment, by=assignment.shopper, type="note", occurred_at=timezone.now(), notes="x"
    )
    activity = Activity.objects.get()
    Assignment.objects.filter(pk=assignment.pk).update(stage=Assignment.Stage.COMPLETE)
    as_sam.post(reverse("shopper:activity_add", args=[assignment.pk]), activity_data())
    as_sam.post(reverse("shopper:activity_delete", args=[activity.pk]))
    assert assignment.activities.count() == 1
    activity.refresh_from_db()
    assert activity.deleted_at is None


def test_cannot_touch_another_shoppers_activity(client, assignment, priya, project):
    fieldwork.add_activity(
        assignment, by=assignment.shopper, type="note", occurred_at=timezone.now(), notes="x"
    )
    activity = Activity.objects.get()
    fieldwork.assign(Target.objects.create(project=project, name="Northwind"), priya, approval_confirmed=True)
    client.force_login(priya)
    assert client.get(reverse("shopper:activity_edit", args=[activity.pk])).status_code == 404
    assert client.post(reverse("shopper:activity_delete", args=[activity.pk])).status_code == 404


# --- Brief --------------------------------------------------------------------------------


def test_first_brief_must_be_read_then_changes_shown(as_sam, assignment, project, sam):
    projects.update_brief(project, "Approach as a genuine buyer.\nAsk for a full price list.")
    url = reverse("shopper:assignment", args=[assignment.pk])
    html = as_sam.get(url).content.decode()
    assert "Please read the brief" in html and "I have read the brief" in html
    as_sam.post(reverse("shopper:brief_acknowledge", args=[assignment.pk]))
    assert projects.has_acknowledged_latest_brief(sam, project)
    assert "confirmed the latest brief" in as_sam.get(url).content.decode()
    projects.update_brief(project, "Approach as a genuine buyer.\nAsk about WhatsApp limits.")
    html = as_sam.get(url).content.decode()
    assert "The brief changed" in html
    assert "+ Ask about WhatsApp limits." in html
    assert "− Ask for a full price list." in html


# --- Shoppers adding targets ----------------------------------------------------------------


def test_shopper_adds_a_target_and_it_is_assigned_to_them(as_sam, assignment, project, sam):
    url = reverse("shopper:target_add")
    form = as_sam.get(url).content.decode()
    assert "Mobile messaging capture review" in form and "Harbourside" not in form
    response = as_sam.post(
        url, {"project": project.pk, "name": "Telvar Systems", "website": "telvar.example"}, **HTMX
    )
    assert response.status_code == 204
    target = Target.objects.get(name="Telvar Systems")
    assert target.added_by_shopper and target.added_by == sam
    new = target.assignments.get()
    assert new.shopper == sam and new.is_open and not new.approval_confirmed_offplatform
    assert "Added by you" in as_sam.get(reverse("shopper:assignments")).content.decode()


def test_duplicate_of_own_target_is_named(as_sam, assignment, project):
    html = as_sam.post(
        reverse("shopper:target_add"), {"project": project.pk, "name": "global relay"}, **HTMX
    ).content.decode()
    assert "You already have this target" in html and "Global Relay" in html


def test_duplicate_of_someone_elses_target_is_not_revealed(client, assignment, project, priya):
    Target.objects.create(project=project, name="Keystone Capture", website="keystone.example")
    fieldwork.assign(Target.objects.create(project=project, name="Other"), priya, approval_confirmed=True)
    client.force_login(priya)
    html = client.post(
        reverse("shopper:target_add"),
        {"project": project.pk, "name": "Keystone", "website": "https://keystone.example/x"},
        **HTMX,
    ).content.decode()
    assert "may already be on this project" in html
    assert "Keystone Capture" not in html
    assert not Target.objects.filter(name="Keystone").exists()


def test_shoppers_can_only_add_to_their_active_projects(client, project, priya, assignment):
    other = Project.objects.create(
        client=project.client,
        internal_name="Other",
        client_facing_name="O",
        shopper_facing_name="Other review",
        status=Project.Status.ACTIVE,
    )
    client.force_login(assignment.shopper)
    response = client.post(reverse("shopper:target_add"), {"project": other.pk, "name": "Sneaky"}, **HTMX)
    assert "Select a valid choice" in response.content.decode()
    assert not Target.objects.filter(name="Sneaky").exists()


@pytest.mark.parametrize(
    "text",
    [
        "Call on 2026-11-06 10:42 went well",
        "Meeting 06/11/2026 at 14:00",
        "Pricing bands 1,000-5,000 users",
        "Quote ref 12345678",
        "Licence costs 120000 per year",
    ],
)
def test_dates_and_ordinary_numbers_are_allowed(as_sam, assignment, text):
    as_sam.post(reverse("shopper:activity_add", args=[assignment.pk]), activity_data(notes=text))
    assert assignment.activities.filter(notes=text).exists()


@pytest.mark.parametrize("text", ["Ring 07700 900123", "Tel: +44 (0)20 7946 0000", "call 020-7946-0000"])
def test_phone_numbers_are_refused(as_sam, assignment, text):
    as_sam.post(reverse("shopper:activity_add", args=[assignment.pk]), activity_data(notes=text))
    assert not assignment.activities.exists()
