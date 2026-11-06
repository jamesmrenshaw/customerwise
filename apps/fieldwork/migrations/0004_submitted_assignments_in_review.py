"""Assignments whose report is submitted but not yet accepted move from Complete to In review."""

from django.db import migrations


def forwards(apps, schema_editor):
    Assignment = apps.get_model("fieldwork", "Assignment")
    Assignment.objects.filter(stage="complete", report__status="submitted").update(stage="in_review")


def backwards(apps, schema_editor):
    Assignment = apps.get_model("fieldwork", "Assignment")
    Assignment.objects.filter(stage="in_review").update(stage="complete")


class Migration(migrations.Migration):
    dependencies = [
        ("fieldwork", "0003_alter_assignment_stage"),
        ("reports", "0003_review"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
