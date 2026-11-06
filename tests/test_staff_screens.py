"""Staff screens 07-11 (projects, details and brief, targets and shoppers, questionnaire, clients)."""

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.fieldwork import services as fieldwork
from apps.fieldwork.models import Assignment
from apps.projects.models import ClientOrganisation, Project, Target
from apps.questionnaires import services as questionnaires
from apps.questionnaires.models import Question, QuestionnaireVersion, Section

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


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
    return ClientOrganisation.objects.create(name="Harbourside Bank", reference="HB-2026")


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
    return Target.objects.create(project=project, name="Global Relay", website="globalrelay.com")


@pytest.fixture
def as_staff(client, staff):
    client.force_login(staff)
    return client


def project_data(project, **changes):
    data = {
        "client": project.client.pk,
        "status": project.status,
        "notification_email": "reviews@customerwise.example",
        "internal_name": project.internal_name,
        "client_facing_name": project.client_facing_name,
        "shopper_facing_name": project.shopper_facing_name,
        "brief": "",
    }
    data.update(changes)
    return data


# --- Access ----------------------------------------------------------------------------


def staff_urls(project, target, client_org):
    return [
        reverse("staff:projects"),
        reverse("staff:project_create"),
        reverse("staff:project_details", args=[project.pk]),
        reverse("staff:targets", args=[project.pk]),
        reverse("staff:target_add", args=[project.pk]),
        reverse("staff:target_assign", args=[target.pk]),
        reverse("staff:questionnaire", args=[project.pk]),
        reverse("staff:clients"),
        reverse("staff:client_add"),
        reverse("staff:client_edit", args=[client_org.pk]),
    ]


def test_staff_pages_load(as_staff, project, target, client_org):
    for url in staff_urls(project, target, client_org):
        assert as_staff.get(url).status_code == 200, url


def test_shoppers_get_404_on_every_staff_page(client, sam, project, target, client_org):
    client.force_login(sam)
    for url in staff_urls(project, target, client_org):
        assert client.get(url).status_code == 404, url


def test_signed_out_users_are_sent_to_sign_in(client, project):
    response = client.get(reverse("staff:projects"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:login"))


def test_staff_home_is_projects(as_staff):
    assert as_staff.get("/")["Location"] == reverse("staff:projects")


def test_admin_only_manages_users(as_staff):
    html = as_staff.get("/admin/").content.decode()
    assert "/admin/accounts/user/" in html
    for gone in ("/admin/projects/", "/admin/questionnaires/", "/admin/fieldwork/"):
        assert gone not in html
        assert as_staff.get(gone).status_code == 404


def test_staff_nav_matches_prototype(as_staff):
    html = as_staff.get(reverse("staff:projects")).content.decode()
    assert '<a href="/projects/" aria-current="page">Projects</a>' in html
    assert ">Clients</a>" in html
    assert reverse("admin:accounts_user_changelist") in html


# --- Projects --------------------------------------------------------------------------


def test_project_list_filters_and_searches(as_staff, project, client_org):
    Project.objects.create(
        client=client_org,
        internal_name="Draft thing",
        client_facing_name="A",
        shopper_facing_name="B",
    )
    html = as_staff.get(reverse("staff:projects") + "?status=draft").content.decode()
    assert "Draft thing" in html and "Harbourside: messaging vendors" not in html
    html = as_staff.get(reverse("staff:projects") + "?q=messaging").content.decode()
    assert "Harbourside: messaging vendors" in html and "Draft thing" not in html


def test_create_project_with_brief(as_staff, client_org):
    response = as_staff.post(
        reverse("staff:project_create"),
        {
            "client": client_org.pk,
            "status": "draft",
            "notification_email": "reviews@customerwise.example",
            "internal_name": "New internal",
            "client_facing_name": "New client name",
            "shopper_facing_name": "New shopper name",
            "brief": "Approach suppliers as a genuine buyer.",
        },
    )
    project = Project.objects.get(internal_name="New internal")
    assert response["Location"] == reverse("staff:project_details", args=[project.pk])
    assert project.latest_brief.text == "Approach suppliers as a genuine buyer."


def test_details_save_rejects_leaky_names_and_briefs(as_staff, project):
    url = reverse("staff:project_details", args=[project.pk])
    response = as_staff.post(
        url,
        project_data(
            project,
            shopper_facing_name=project.internal_name,
            brief="Researching for Harbourside Bank.",
        ),
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert "Not saved." in html
    assert "Use a different name from the internal one." in html
    assert "The brief mentions the client" in html
    assert project.brief_versions.count() == 0


def test_brief_changes_show_version_history_and_acknowledgements(as_staff, project, target, sam):
    url = reverse("staff:project_details", args=[project.pk])
    as_staff.post(url, project_data(project, brief="Version one."))
    fieldwork.assign(target, sam, approval_confirmed=True)
    response = as_staff.post(url, project_data(project, brief="Version two."), follow=True)
    html = response.content.decode()
    assert "Brief saved as version 2" in html
    assert "Version 2" in html and "Version 1" in html
    assert "Sam Okafor" in html and "Not yet" in html


def test_completing_a_project_records_its_end_date(as_staff, project):
    as_staff.post(
        reverse("staff:project_details", args=[project.pk]), project_data(project, status="complete")
    )
    project.refresh_from_db()
    assert project.ended_at is not None


# --- Targets and shoppers ----------------------------------------------------------------


def test_add_target_warns_about_duplicates_then_allows(as_staff, project, target):
    url = reverse("staff:target_add", args=[project.pk])
    response = as_staff.post(url, {"name": "Global Relay Ltd", "website": "https://globalrelay.com"}, **HTMX)
    html = response.content.decode()
    assert response.status_code == 200
    assert "Possible duplicate" in html and "Add anyway" in html
    assert not Target.objects.filter(name="Global Relay Ltd").exists()
    response = as_staff.post(
        url, {"name": "Global Relay Ltd", "website": "https://globalrelay.com", "add_anyway": "1"}, **HTMX
    )
    assert response.status_code == 204
    assert response["HX-Redirect"] == reverse("staff:targets", args=[project.pk])
    new = Target.objects.get(name="Global Relay Ltd")
    assert new.added_by.email == "alex@customerwise.example"


def test_dialogs_work_without_javascript(as_staff, project):
    html = as_staff.get(reverse("staff:target_add", args=[project.pk])).content.decode()
    assert "<dialog" in html and " open" in html and "<header" in html
    response = as_staff.post(reverse("staff:target_add", args=[project.pk]), {"name": "Northwind"})
    assert response.status_code == 302


def test_assign_requires_approval(as_staff, target, sam):
    url = reverse("staff:target_assign", args=[target.pk])
    html = as_staff.post(url, {"shopper": sam.pk}, **HTMX).content.decode()
    assert "Confirm the client has approved this shopper" in html
    assert not Assignment.objects.exists()
    response = as_staff.post(url, {"shopper": sam.pk, "approval_confirmed": "on"}, **HTMX)
    assert response.status_code == 204
    assignment = Assignment.objects.get()
    assert assignment.shopper == sam and assignment.approval_confirmed_offplatform


def test_assign_refuses_a_target_that_already_has_a_shopper(as_staff, target, sam, priya):
    fieldwork.assign(target, sam, approval_confirmed=True)
    url = reverse("staff:target_assign", args=[target.pk])
    html = as_staff.post(url, {"shopper": priya.pk, "approval_confirmed": "on"}, **HTMX).content.decode()
    assert "already has an open assignment" in html
    assert target.assignments.open().get().shopper == sam


def test_assign_only_offers_active_shoppers(as_staff, target, sam, staff):
    sam_inactive = User.objects.create_user("gone@example.com", is_active=False)
    html = as_staff.get(reverse("staff:target_assign", args=[target.pk])).content.decode()
    assert ">Sam Okafor</option>" in html
    assert sam_inactive.email not in html
    assert ">Alex Morgan</option>" not in html


def test_reassign_and_withdraw(as_staff, project, target, sam, priya):
    fieldwork.assign(target, sam, approval_confirmed=True)
    url = reverse("staff:target_reassign", args=[target.pk])
    html = as_staff.get(url).content.decode()
    assert "currently assigned to Sam Okafor" in html
    response = as_staff.post(
        url, {"shopper": priya.pk, "approval_confirmed": "on", "note": "Sam away"}, follow=True
    )
    assert "reassigned to Priya Shah" in response.content.decode()
    assert target.assignments.count() == 2
    assert target.assignments.open().get().shopper == priya

    response = as_staff.post(reverse("staff:target_withdraw", args=[target.pk]), {"note": ""}, follow=True)
    assert "Priya Shah withdrawn" in response.content.decode()
    assert not target.assignments.open().exists()
    assert "Not assigned" in as_staff.get(reverse("staff:targets", args=[project.pk])).content.decode()


def test_archive_withdraws_and_hides(as_staff, project, target, sam):
    fieldwork.assign(target, sam, approval_confirmed=True)
    as_staff.post(reverse("staff:target_archive", args=[target.pk]), {"note": "Out of scope"})
    target.refresh_from_db()
    assert target.archived_at is not None
    assert not target.assignments.open().exists()
    assert (
        "<strong>Global Relay</strong>"
        not in as_staff.get(reverse("staff:targets", args=[project.pk])).content.decode()
    )
    archived = as_staff.get(reverse("staff:targets", args=[project.pk]) + "?show=archived").content.decode()
    assert "Global Relay" in archived and "history kept" in archived


def test_target_filters(as_staff, project, target, sam):
    Target.objects.create(project=project, name="Telvar", added_by=sam, added_by_shopper=True)
    url = reverse("staff:targets", args=[project.pk])
    shopper_added = as_staff.get(url + "?show=shopper").content.decode()
    assert "Telvar" in shopper_added and "Global Relay" not in shopper_added
    assert "Global Relay" in as_staff.get(url + "?show=unassigned").content.decode()


# --- Questionnaire builder -----------------------------------------------------------------


def test_start_from_the_pilot_questionnaire(as_staff, project):
    html = as_staff.get(reverse("staff:questionnaire", args=[project.pk])).content.decode()
    assert "Start from the pilot questionnaire" in html
    as_staff.post(reverse("staff:questionnaire_start", args=[project.pk]), {"template": "pilot"})
    version = QuestionnaireVersion.objects.get(project=project)
    assert version.is_draft
    assert Question.objects.filter(section__version=version).count() == 46


def test_start_a_blank_draft(as_staff, project):
    as_staff.post(reverse("staff:questionnaire_start", args=[project.pk]))
    version = QuestionnaireVersion.objects.get(project=project)
    assert version.sections.count() == 1


@pytest.fixture
def draft(project):
    version = QuestionnaireVersion.objects.create(project=project, number=1)
    section = Section.objects.create(version=version, title="Commercial", order=10)
    Question.objects.create(section=section, key="commercial-1", prompt="First?", order=10)
    Question.objects.create(section=section, key="commercial-2", prompt="Second?", order=20)
    return version


def section_post(section, action="save", **overrides):
    questions = list(section.questions.order_by("order"))
    data = {
        "section-title": section.title,
        "section-display_style": section.display_style,
        "q-TOTAL_FORMS": len(questions),
        "q-INITIAL_FORMS": len(questions),
        "q-MIN_NUM_FORMS": 0,
        "q-MAX_NUM_FORMS": 1000,
        "action": action,
    }
    for i, q in enumerate(questions):
        data |= {
            f"q-{i}-id": q.pk,
            f"q-{i}-prompt": q.prompt,
            f"q-{i}-answer_type": q.answer_type,
            f"q-{i}-guidance": q.guidance,
        }
    data |= overrides
    return data


def test_edit_section_and_questions(as_staff, draft):
    section = draft.sections.get()
    url = reverse("staff:section_save", args=[section.pk])
    as_staff.post(
        url,
        section_post(
            section, **{"section-title": "Pricing", "q-0-prompt": "Edited?", "q-0-guidance": "Be specific."}
        ),
    )
    section.refresh_from_db()
    first = section.questions.order_by("order").first()
    assert section.title == "Pricing"
    assert first.prompt == "Edited?" and first.guidance == "Be specific."


def test_add_move_and_remove_questions(as_staff, draft):
    section = draft.sections.get()
    url = reverse("staff:section_save", args=[section.pk])
    second = section.questions.get(key="commercial-2")
    as_staff.post(url, section_post(section, action=f"up:{second.pk}"))
    assert list(section.questions.order_by("order").values_list("key", flat=True)) == [
        "commercial-2",
        "commercial-1",
    ]
    as_staff.post(url, section_post(section, action="add"))
    assert section.questions.count() == 3
    keys = list(section.questions.values_list("key", flat=True))
    assert len(set(keys)) == 3
    as_staff.post(url, section_post(section, action=f"remove:{second.pk}"))
    assert not section.questions.filter(pk=second.pk).exists()


def test_only_one_key_facts_section_in_the_builder(as_staff, draft):
    Section.objects.create(version=draft, title="Key facts", display_style="key_facts", order=0)
    section = draft.sections.get(title="Commercial")
    response = as_staff.post(
        reverse("staff:section_save", args=[section.pk]),
        section_post(section, **{"section-display_style": "key_facts"}),
    )
    assert response.status_code == 400
    assert "Only one section can use Key facts" in response.content.decode()


def test_publish_dialog_lists_problems_then_publishes(as_staff, draft):
    empty = Section.objects.create(version=draft, title="Pricing follow-up", order=30)
    url = reverse("staff:version_publish", args=[draft.pk])
    html = as_staff.get(url, **HTMX).content.decode()
    assert "Pricing follow-up" in html and "has no questions" in html
    assert 'type="submit"' not in html
    empty.delete()
    response = as_staff.post(url, {}, **HTMX)
    assert response.status_code == 204
    draft.refresh_from_db()
    assert not draft.is_draft


def test_published_versions_are_read_only(as_staff, draft, project):
    questionnaires.publish(draft)
    section = draft.sections.get()
    html = as_staff.get(reverse("staff:questionnaire", args=[project.pk])).content.decode()
    assert "Published versions can't be changed" in html
    assert 'name="q-0-prompt"' not in html
    assert "Start a new draft from this version" in html
    as_staff.post(
        reverse("staff:section_save", args=[section.pk]), section_post(section, **{"section-title": "Hacked"})
    )
    section.refresh_from_db()
    assert section.title == "Commercial"


def test_new_draft_from_published_version(as_staff, draft, project):
    questionnaires.publish(draft)
    as_staff.post(reverse("staff:version_new_draft", args=[draft.pk]))
    new = QuestionnaireVersion.objects.get(project=project, number=2)
    assert new.is_draft
    assert sorted(Question.objects.filter(section__version=new).values_list("key", flat=True)) == [
        "commercial-1",
        "commercial-2",
    ]


# --- Clients -----------------------------------------------------------------------------


def test_add_edit_and_deactivate_clients(as_staff, client_org):
    response = as_staff.post(reverse("staff:client_add"), {"name": "harbourside bank"}, **HTMX)
    assert "already exists" in response.content.decode()
    response = as_staff.post(
        reverse("staff:client_add"), {"name": "Northgate Insurance", "reference": "NI"}, **HTMX
    )
    assert response.status_code == 204
    northgate = ClientOrganisation.objects.get(name="Northgate Insurance")
    as_staff.post(
        reverse("staff:client_edit", args=[northgate.pk]),
        {"name": "Northgate Insurance plc", "reference": "NI"},
    )
    northgate.refresh_from_db()
    assert northgate.name == "Northgate Insurance plc"
    as_staff.post(reverse("staff:client_toggle", args=[northgate.pk]))
    northgate.refresh_from_db()
    assert not northgate.is_active
    html = as_staff.get(reverse("staff:project_create")).content.decode()
    assert ">Northgate Insurance plc</option>" not in html
    assert ">Harbourside Bank</option>" in html
