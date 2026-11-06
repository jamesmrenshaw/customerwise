from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("assignments/<uuid:pk>/report/", views.report, name="report"),
    path("reports/<uuid:pk>/answers/<uuid:question_pk>/", views.autosave, name="autosave"),
    path("reports/<uuid:pk>/save/", views.save_section, name="save_section"),
    path("reports/<uuid:pk>/notes/", views.save_notes, name="save_notes"),
    path("reports/<uuid:pk>/submit/", views.submit, name="submit"),
    path("reports/<uuid:pk>/evidence/", views.evidence_upload, name="evidence_upload"),
    path("evidence/<uuid:pk>/category/", views.evidence_category, name="evidence_category"),
    path("evidence/<uuid:pk>/delete/", views.evidence_delete, name="evidence_delete"),
    path("evidence/<uuid:pk>/download/", views.evidence_download, name="evidence_download"),
]
