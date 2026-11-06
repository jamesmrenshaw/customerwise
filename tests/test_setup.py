"""Setup done in Django admin: projects, briefs, questionnaires, targets and assignments (step 3)."""

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.accounts.models import User
from apps.core.access import assignments_for, targets_for
from apps.fieldwork import services as fieldwork
from apps.fieldwork.models import Assignment
from apps.projects import services as projects
from apps.projects.models import ClientOrganisation, Project, Target, domain_from_website
from apps.questionnaires import services as questionnaires
from apps.questionnaires.models import Question, QuestionnaireVersion, Section
from apps.staff.forms import ProjectForm

pytestmark = pytest.mark.django_db


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
def client_org():
    return ClientOrganisation.objects.create(name="Harbourside Bank")


@pytest.fixture
def project(client_org):
    return Project.objects.create(
        client=client_org,
        internal_name="Harbourside: messaging vendors",
        client_facing_name="Messaging capture vendor review",
        shopper_facing_name="Mobile messaging capture review",
        status=Project.Status.ACTIVE,
    )


@pytest.fixture
def target(project):
    return Target.objects.create(project=project, name="Global Relay", website="https://www.globalrelay.com/")


# --- Projects and briefs ---------------------------------------------------------


def test_project_names_must_differ_from_the_internal_name(client_org):
    form = ProjectForm(
        data={
            "client": client_org.pk,
            "internal_name": "Harbourside review",
            "client_facing_name": "Harbourside review",
            "shopper_facing_name": "harbourside review",
            "status": "draft",
        }
    )
    assert not form.is_valid()
    assert "client_facing_name" in form.errors
    assert "shopper_facing_name" in form.errors


def test_brief_must_not_name_the_client(client_org):
    form = ProjectForm(
        data={
            "client": client_org.pk,
            "internal_name": "Internal",
            "client_facing_name": "Client facing",
            "shopper_facing_name": "Shopper facing",
            "status": "draft",
            "brief": "You are researching suppliers on behalf of Harbourside Bank.",
        }
    )
    assert not form.is_valid()
    assert "brief" in form.errors


def test_brief_changes_create_new_versions(project, staff, sam):
    v1 = projects.update_brief(project, "Approach each supplier as a genuine buyer.", staff)
    assert v1.number == 1
    assert projects.update_brief(project, "Approach each supplier as a genuine buyer.  \n", staff) is None
    projects.acknowledge_brief(sam, v1)
    assert projects.has_acknowledged_latest_brief(sam, project)
    v2 = projects.update_brief(
        project, "Approach each supplier as a genuine buyer.\nAsk about WhatsApp.", staff
    )
    assert v2.number == 2
    assert project.latest_brief == v2
    assert not projects.has_acknowledged_latest_brief(sam, project)
    assert project.brief_versions.count() == 2


# --- Targets ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("website", "domain"),
    [
        ("https://www.GlobalRelay.com/about", "globalrelay.com"),
        ("globalrelay.com", "globalrelay.com"),
        ("http://sub.example.co.uk", "sub.example.co.uk"),
        ("", ""),
    ],
)
def test_domain_from_website(website, domain):
    assert domain_from_website(website) == domain


def test_duplicate_targets_match_name_or_domain(project, target):
    assert list(projects.find_duplicate_targets(project, "global relay")) == [target]
    assert list(projects.find_duplicate_targets(project, "GR Ltd", "globalrelay.com/contact")) == [target]
    assert not projects.find_duplicate_targets(project, "Northwind", "northwind.example").exists()
    target.archived_at = target.created_at
    target.save()
    assert not projects.find_duplicate_targets(project, "Global Relay").exists()


# --- Questionnaires --------------------------------------------------------------


def test_pilot_seed_loads_and_publishes(project):
    call_command("load_questionnaire", str(project.pk), "--publish")
    version = questionnaires.current_version(project)
    assert version.status == QuestionnaireVersion.Status.PUBLISHED
    sections = list(version.sections.all())
    assert len(sections) == 14
    assert sections[0].display_style == Section.DisplayStyle.KEY_FACTS
    assert Question.objects.filter(section__version=version).count() == 46
    references = Question.objects.get(section__version=version, key="commercial-7")
    assert "successful" in references.guidance
    first_enquiry = Question.objects.get(section__version=version, key="key-facts-1")
    assert first_enquiry.answer_type == Question.AnswerType.DATETIME


def test_publishing_validates_the_draft(project):
    draft = QuestionnaireVersion.objects.create(project=project, number=1)
    with pytest.raises(ValidationError, match="at least one section"):
        questionnaires.publish(draft)
    section = Section.objects.create(version=draft, title="Commercial")
    with pytest.raises(ValidationError, match="no questions"):
        questionnaires.publish(draft)
    Question.objects.create(section=section, key="q", prompt="One?")
    Question.objects.create(section=section, key="q", prompt="Two?")
    with pytest.raises(ValidationError, match="unique"):
        questionnaires.publish(draft)


def test_only_one_draft_and_one_key_facts_section(project):
    draft = QuestionnaireVersion.objects.create(project=project, number=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        QuestionnaireVersion.objects.create(project=project, number=2)
    Section.objects.create(version=draft, title="Facts", display_style="key_facts")
    with pytest.raises(IntegrityError), transaction.atomic():
        Section.objects.create(version=draft, title="More facts", display_style="key_facts")


def test_new_draft_copies_questions_with_the_same_keys(project):
    call_command("load_questionnaire", str(project.pk), "--publish")
    v1 = questionnaires.current_version(project)
    v2 = questionnaires.new_draft_from(v1)
    assert v2.number == 2 and v2.is_draft
    keys = lambda v: sorted(Question.objects.filter(section__version=v).values_list("key", flat=True))  # noqa: E731
    assert keys(v1) == keys(v2)
    with pytest.raises(ValidationError, match="already has a draft"):
        questionnaires.new_draft_from(v1)


# --- Assignments -----------------------------------------------------------------


def test_staff_must_confirm_client_approval(target, sam, staff):
    with pytest.raises(ValidationError, match="Confirm the client approved"):
        fieldwork.assign(target, sam, by=staff)
    assignment = fieldwork.assign(target, sam, by=staff, approval_confirmed=True)
    assert assignment.approval_confirmed_offplatform
    assert assignment.stage == Assignment.Stage.NOT_STARTED


def test_shopper_added_targets_are_assigned_without_approval(target, sam):
    assert fieldwork.assign(target, sam, by=sam, by_shopper=True).is_open


def test_only_active_shoppers_can_be_assigned(target, staff, sam):
    with pytest.raises(ValidationError, match="Only active shoppers"):
        fieldwork.assign(target, staff, approval_confirmed=True)
    sam.is_active = False
    sam.save()
    with pytest.raises(ValidationError, match="Only active shoppers"):
        fieldwork.assign(target, sam, approval_confirmed=True)


def test_a_target_has_at_most_one_open_assignment(target, sam, priya):
    fieldwork.assign(target, sam, approval_confirmed=True)
    with pytest.raises(ValidationError, match="already has an open assignment"):
        fieldwork.assign(target, priya, approval_confirmed=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        Assignment.objects.create(target=target, shopper=priya)


def test_reassigning_keeps_history_and_moves_access(target, sam, priya, staff):
    old = fieldwork.assign(target, sam, approval_confirmed=True)
    new = fieldwork.reassign(old, priya, by=staff, approval_confirmed=True, note="Sam unavailable")
    old.refresh_from_db()
    assert old.closed_reason == Assignment.ClosedReason.REASSIGNED
    assert old.closed_note == "Sam unavailable"
    assert new.shopper == priya and new.is_open
    assert target.assignments.count() == 2
    assert not assignments_for(sam).exists()
    assert not targets_for(sam).exists()
    assert list(assignments_for(priya)) == [new]
    assert set(assignments_for(staff)) == {old, new}


def test_withdrawing_removes_access_and_keeps_history(target, sam):
    assignment = fieldwork.assign(target, sam, approval_confirmed=True)
    fieldwork.withdraw(assignment, note="Client paused this target")
    assert not assignments_for(sam).exists()
    assert Assignment.objects.get().closed_reason == Assignment.ClosedReason.WITHDRAWN
    with pytest.raises(ValidationError):
        fieldwork.withdraw(assignment)


def test_unable_to_complete_stays_visible_to_the_shopper(target, sam):
    assignment = fieldwork.assign(target, sam, approval_confirmed=True)
    Assignment.objects.filter(pk=assignment.pk).update(
        closed_at=assignment.created_at, closed_reason=Assignment.ClosedReason.UNABLE
    )
    assert list(assignments_for(sam)) == [assignment]


def test_shoppers_only_see_their_own_targets_on_a_shared_project(project, target, sam, priya):
    other = Target.objects.create(project=project, name="Northwind Messaging")
    fieldwork.assign(target, sam, approval_confirmed=True)
    fieldwork.assign(other, priya, approval_confirmed=True)
    assert list(targets_for(sam)) == [target]
    assert list(targets_for(priya)) == [other]


# --- Admin pages and the invitation button (C) -------------------------------------


def test_send_invitation_button_on_user_page(client, staff, sam):
    client.force_login(staff)
    page = client.get(reverse("admin:accounts_user_change", args=[sam.pk])).content.decode()
    assert "Send invitation" in page
    url = reverse("admin:accounts_user_send_invitation", args=[sam.pk])
    assert client.get(url).status_code == 405
    response = client.post(url, follow=True)
    assert "Invitation sent to sam@example.com" in response.content.decode()
    assert mail.outbox[-1].to == ["sam@example.com"]
    assert "Resend invitation" in response.content.decode()


def test_shoppers_cannot_use_the_invitation_button(client, sam, priya):
    client.force_login(sam)
    response = client.post(reverse("admin:accounts_user_send_invitation", args=[priya.pk]))
    assert response.status_code == 302
    assert mail.outbox == []
