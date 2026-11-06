from django.urls import path

from . import review_views, views

app_name = "staff"

urlpatterns = [
    path("projects/", views.project_list, name="projects"),
    path("projects/new/", views.project_create, name="project_create"),
    path("projects/<uuid:pk>/", views.project_home, name="project"),
    path("projects/<uuid:pk>/details/", views.project_details, name="project_details"),
    path("projects/<uuid:pk>/fieldwork/", review_views.fieldwork, name="fieldwork"),
    path("staff/assignments/<uuid:pk>/", review_views.assignment, name="assignment"),
    path("staff/reports/<uuid:pk>/", review_views.review_report, name="review"),
    path("staff/reports/<uuid:pk>/comments/", review_views.comment_add, name="comment_add"),
    path("staff/comments/<uuid:pk>/", review_views.comment_update, name="comment_update"),
    path("staff/reports/<uuid:pk>/return/", review_views.return_report, name="return_report"),
    path("staff/reports/<uuid:pk>/accept/", review_views.accept_report, name="accept_report"),
    path("staff/reports/<uuid:pk>/export/", review_views.export_report, name="export_report"),
    path("projects/<uuid:pk>/targets/", views.targets, name="targets"),
    path("projects/<uuid:pk>/targets/add/", views.target_add, name="target_add"),
    path("projects/<uuid:pk>/questionnaire/", views.questionnaire, name="questionnaire"),
    path("projects/<uuid:pk>/questionnaire/start/", views.questionnaire_start, name="questionnaire_start"),
    path("targets/<uuid:pk>/assign/", views.target_assign, name="target_assign"),
    path("targets/<uuid:pk>/reassign/", views.target_reassign, name="target_reassign"),
    path("targets/<uuid:pk>/withdraw/", views.target_withdraw, name="target_withdraw"),
    path("targets/<uuid:pk>/archive/", views.target_archive, name="target_archive"),
    path("questionnaires/<uuid:pk>/new-draft/", views.version_new_draft, name="version_new_draft"),
    path("questionnaires/<uuid:pk>/publish/", views.version_publish, name="version_publish"),
    path("questionnaires/<uuid:pk>/sections/add/", views.section_add, name="section_add"),
    path("sections/<uuid:pk>/", views.section_save, name="section_save"),
    path("clients/", views.client_list, name="clients"),
    path("clients/add/", views.client_edit, name="client_add"),
    path("clients/<uuid:pk>/edit/", views.client_edit, name="client_edit"),
    path("clients/<uuid:pk>/toggle/", views.client_toggle, name="client_toggle"),
]
