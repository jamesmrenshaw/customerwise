from django.urls import path

from . import views

app_name = "shopper"

urlpatterns = [
    path("assignments/", views.assignment_list, name="assignments"),
    path("assignments/add-target/", views.target_add, name="target_add"),
    path("assignments/<uuid:pk>/", views.assignment_detail, name="assignment"),
    path("assignments/<uuid:pk>/stage/", views.assignment_stage, name="assignment_stage"),
    path("assignments/<uuid:pk>/unable/", views.assignment_unable, name="assignment_unable"),
    path("assignments/<uuid:pk>/activities/", views.activity_add, name="activity_add"),
    path("assignments/<uuid:pk>/brief/acknowledge/", views.brief_acknowledge, name="brief_acknowledge"),
    path("activities/<uuid:pk>/edit/", views.activity_edit, name="activity_edit"),
    path("activities/<uuid:pk>/delete/", views.activity_delete, name="activity_delete"),
]
